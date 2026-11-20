"""Environment Modules and Lmod on a cluster: listing, details, load tests.

Commands run in a login shell, where `module` is defined. Extra module
directories (a profile's `module_use`, e.g. Spack's) are added first.

Module names carry versions and builds in site-specific ways:
  qe/7.3.1b-intelmpi-omp                    a site's own modules (Lynx)
  yambo/gpl/5.2.4-single-omp                with a middle level ("gpl")
  quantum-espresso/7.5-intel-oneapi-compilers-2026.0.0-ra7jhfo   Spack
ModuleName splits them for display: program, version, build, Spack hash.
Recorded outputs from Lynx (Environment Modules 5.3.1) are in tests/data/lynx/.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .shell import quote
from .transport import Transport

# Programs and the module names they go by
PROGRAM_ALIASES = {
    "qe": ("qe", "quantum-espresso", "quantumespresso", "espresso"),
    "yambo": ("yambo",),
}

# Spack's default projection: {name}/{version}-{compiler.name}-{compiler.version}-{hash:7}
_SPACK = re.compile(r"^(?P<version>[^-]+)-(?P<compiler>.+)-(?P<hash>[a-z2-7]{7})$")
_VERSION = re.compile(r"^(?P<version>\d+(?:\.\d+)*[a-z]?)(?:-(?P<build>.+))?$")


def _looks_spack(m: re.Match, directory: str) -> bool:
    """A Spack name has a numeric version and a compiler ending in its version
    ("intel-oneapi-compilers-2026.0.0") or "none-none"; a site's branch name
    like "devel-sc-rt-excitons-openmpi" fits the pattern but not this."""
    if "spack" in directory:
        return True
    compiler = m.group("compiler")
    return (m.group("version")[:1].isdigit()
            and (compiler == "none-none" or re.search(r"-\d[\w.]*$", compiler) is not None))


@dataclass(frozen=True)
class ModuleName:
    full: str              # what `module load` takes
    program: str           # first level: "qe", "yambo", "quantum-espresso"
    variant: str = ""      # middle levels: "gpl", "devel", "intel/mpi-omp"
    version: str = ""      # "7.3.1b"; empty when the name has none ("yambo/intel-libs")
    build: str = ""        # "intelmpi-omp", "intel-oneapi-compilers-2026.0.0"
    spack_hash: str = ""   # "ra7jhfo"
    directory: str = ""    # where it was found

    @classmethod
    def parse(cls, full: str, directory: str = "") -> "ModuleName":
        parts = full.split("/")
        program, last = parts[0], parts[-1]
        variant = "/".join(parts[1:-1])
        if len(parts) == 1:
            return cls(full, program, directory=directory)
        m = _SPACK.match(last)
        if m and _looks_spack(m, directory):
            compiler = m.group("compiler")
            return cls(full, program, variant, m.group("version"),
                       "" if compiler == "none-none" else compiler, m.group("hash"), directory)
        m = _VERSION.match(last)
        if m:
            return cls(full, program, variant, m.group("version"), m.group("build") or "",
                       directory=directory)
        # No version: "yambo/intel-libs", "qe/turboMagnon-intelmpi", "yambo/devel/magnons-intelmpi"
        return cls(full, program, variant, "", last, directory=directory)

    @property
    def from_spack(self) -> bool:
        return bool(self.spack_hash)

    def label(self, with_hash: bool = False) -> str:
        """ "7.3.1b intelmpi-omp", "5.2.4 single-omp (gpl)", "7.5 intel-oneapi-... [ra7jhfo]" """
        text = " ".join(x for x in (self.version, self.build) if x) or self.full
        if self.variant:
            text += f" ({self.variant})"
        if with_hash and self.spack_hash:
            text += f" [{self.spack_hash}]"
        return text


def labels(modules: list[ModuleName]) -> dict[str, str]:
    """Display labels; the Spack hash only where two builds would look the same."""
    plain = {m.full: (m.program, m.label()) for m in modules}
    seen: dict[tuple, int] = {}
    for key in plain.values():
        seen[key] = seen.get(key, 0) + 1
    return {m.full: m.label(with_hash=seen[plain[m.full]] > 1) for m in modules}


def _version_key(m: ModuleName):
    nums = [int(x) for x in re.findall(r"\d+", m.version)]
    return (bool(m.version), nums, m.version, m.build)


def for_program(modules: list[ModuleName], program: str) -> list[ModuleName]:
    """Modules of a program (qe, yambo), newest version first; those without a
    version (library bundles, development branches) last."""
    names = PROGRAM_ALIASES.get(program, (program,))
    found = [m for m in modules if m.program.lower() in names]
    versioned = sorted((m for m in found if m.version), key=_version_key, reverse=True)
    others = sorted((m for m in found if not m.version), key=lambda m: m.full)
    return versioned + others


# -- module system ------------------------------------------------------------------------

@dataclass
class ModuleSystem:
    kind: str = ""         # "environment-modules", "lmod", or "" if none
    version: str = ""

    @property
    def json_listing(self) -> bool:
        """`module -j avail`: Environment Modules >= 4.5."""
        if self.kind != "environment-modules":
            return False
        nums = [int(x) for x in re.findall(r"\d+", self.version)[:2]] + [0, 0]
        return tuple(nums[:2]) >= (4, 5)


def parse_module_version(text: str) -> ModuleSystem:
    """Output of `module --version 2>&1; echo LMOD_VERSION=$LMOD_VERSION`."""
    lmod = re.search(r"LMOD_VERSION=(\S+)", text)
    if lmod or "Lua" in text:
        m = re.search(r"Version\s+(\S+)", text)
        return ModuleSystem("lmod", lmod.group(1) if lmod else (m.group(1) if m else ""))
    m = re.search(r"Modules Release\s+(\S+)", text) or re.search(r"VERSION=(\S+)", text)
    if m:
        return ModuleSystem("environment-modules", m.group(1))
    return ModuleSystem()


def parse_avail_json(text: str) -> list[ModuleName]:
    """`module -j avail` (Environment Modules): {directory: {name: {...}}}."""
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])
    modules = []
    for directory, entries in data.items():
        for name, info in entries.items():
            if info.get("type", "modulefile") in ("modulefile", "virtual"):
                modules.append(ModuleName.parse(name, directory))
    return sorted(modules, key=lambda m: m.full)


def parse_avail_terse(text: str) -> list[ModuleName]:
    """`module -t avail 2>&1`, Environment Modules or Lmod: "directory:" lines,
    then one module per line, maybe with markers ("(default)", "<L>"). Lmod
    also lists each family ("yambo/", "qe/oneapi-2024.2.1/"): not modules."""
    modules, directory = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.endswith(":") and "/" in line:
            directory = line[:-1]
            continue
        name = re.split(r"\s|\(|<", line, maxsplit=1)[0]
        if not name or name.endswith((":", "/")) or ("/" not in name and not directory):
            continue
        modules.append(ModuleName.parse(name, directory))
    return sorted({m.full: m for m in modules}.values(), key=lambda m: m.full)


@dataclass
class ModuleDetails:
    name: str
    description: str = ""                             # module-whatis
    loads: list[str] = field(default_factory=list)    # `module load` and `depends-on`
    path: list[str] = field(default_factory=list)     # PATH additions
    error: str = ""


# Lmod writes `module show` as Lua: whatis("Version: 7.3.1"), load("a","b"),
# prepend_path("PATH","/x")
_LUA_CALL = re.compile(r"^\s*(\w+)\((.*)\)\s*$")
_LUA_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _lua_description(whatis: list[str]) -> str:
    """Lmod modules often split whatis in "Key: value" lines (Name, Version,
    Description, Category): "quantum espresso 7.3.1 (mpi+omp)"."""
    fields = {}
    for w in whatis:
        key, sep, value = w.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    if not fields:
        return " ".join(whatis)
    text = " ".join(fields[k] for k in ("name", "version") if fields.get(k))
    if fields.get("description"):
        text = f"{text} ({fields['description']})" if text else fields["description"]
    return text or " ".join(whatis)


def parse_module_show(text: str) -> dict[str, ModuleDetails]:
    """Several `module show` outputs, each after a "=== <name>" line, in
    Environment Modules' form (module-whatis, module load, prepend-path) or
    Lmod's Lua form (whatis(), load(), prepend_path())."""
    result: dict[str, ModuleDetails] = {}
    current = None
    whatis: list[str] = []

    def finish():
        if current is not None and whatis and not current.description:
            current.description = _lua_description(whatis)

    for line in text.splitlines():
        if line.startswith("=== "):
            finish()
            whatis = []
            current = result.setdefault(line[4:].strip(), ModuleDetails(line[4:].strip()))
            continue
        if current is None:
            continue
        lua = _LUA_CALL.match(line)
        if lua:
            func, args = lua.group(1), _LUA_STRING.findall(lua.group(2))
            if func == "whatis" and args:
                whatis.append(args[0])
            elif func in ("load", "always_load", "depends_on", "prereq", "try_load"):
                current.loads.extend(args)
            elif func in ("prepend_path", "append_path") and len(args) >= 2 and args[0] == "PATH":
                current.path.append(args[1])
            continue
        words = line.split()
        if not words:
            continue
        key = words[0]
        if key == "module-whatis":
            current.description = line.split(None, 1)[1].strip().strip("{}").strip()
        elif key == "module" and len(words) >= 3 and words[1] == "load":
            current.loads.extend(words[2:])
        elif key in ("depends-on", "prereq") and len(words) >= 2:
            current.loads.extend(w for w in words[1:] if not w.startswith("-"))
        elif key in ("prepend-path", "append-path"):
            args = [w for w in words[1:] if not w.startswith("-") and w != ":"]
            if len(args) >= 2 and args[0] == "PATH":
                current.path.append(args[1])
        elif "ERROR" in line or "Unable to locate" in line:
            current.error = line.strip()
    finish()
    return result


# -- load test -------------------------------------------------------------------------------

@dataclass
class LoadCheck:
    modules: list[str]
    loaded_ok: bool = False
    errors: list[str] = field(default_factory=list)          # from `module load`
    loaded: list[str] = field(default_factory=list)          # `module -t list` after loading
    executables: dict[str, str | None] = field(default_factory=dict)   # name -> path or None
    missing_libraries: dict[str, list[str]] = field(default_factory=dict)
    # Libraries found but too old: "libfabric.so.1 lacks FABRIC_1.9 (needed by libmpi.so.40)"
    wrong_versions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return (self.loaded_ok and not self.errors
                and all(self.executables.values())
                and not any(self.missing_libraries.values())
                and not any(self.wrong_versions.values()))

    def problems(self) -> list[str]:
        out = list(self.errors)
        if not self.loaded_ok and not self.errors:
            out.append("module load failed")
        out += [f"{exe}: not found after loading the modules"
                for exe, path in self.executables.items() if not path]
        out += [f"{exe}: missing libraries {', '.join(libs)} (a library module not loaded?)"
                for exe, libs in self.missing_libraries.items() if libs]
        out += [f"{exe}: wrong library versions: {'; '.join(v)} (a library module not loaded, "
                f"or modules that do not go together?)"
                for exe, v in self.wrong_versions.items() if v]
        return out


def _use_lines(module_use: list[str]) -> str:
    return "".join(f"module use {quote(d)} 2>&1; " for d in module_use)


def load_check_command(module_use: list[str], modules: list[str], executables: list[str]) -> str:
    """One shell line: load the modules from a clean slate, then find each
    executable and the shared libraries it lacks. Output lines start with @@."""
    exes = " ".join(quote(e) for e in executables)
    return (
        "module purge >/dev/null 2>&1; " + _use_lines(module_use)
        + f"module load {' '.join(quote(m) for m in modules)} 2>&1; echo \"@@load $?\"; "
        + "echo @@list; module -t list 2>&1; echo @@end; "
        + f"for e in {exes}; do p=$(command -v \"$e\"); echo \"@@exe|$e|$p\"; "
        + "if [ -n \"$p\" ]; then ldd \"$p\" 2>/dev/null | grep 'not found' "
        + "| sed \"s/^/@@missing|$e|/\"; fi; done"
    )


# ldd's line for a library found in the wrong version
_WRONG_VERSION = re.compile(
    r"(?P<lib>[^\s:]+): version `(?P<version>[^']+)' not found \(required by (?P<by>[^)]+)\)")


def _add_once(items: list[str], item: str):
    if item not in items:
        items.append(item)


def parse_load_check(modules: list[str], executables: list[str], text: str) -> LoadCheck:
    check = LoadCheck(list(modules), executables={e: None for e in executables},
                      missing_libraries={e: [] for e in executables},
                      wrong_versions={e: [] for e in executables})
    in_list = False
    for line in text.splitlines():
        if line.startswith("@@load "):
            check.loaded_ok = line.split()[1] == "0"
        elif line == "@@list":
            in_list = True
        elif line == "@@end":
            in_list = False
        elif line.startswith("@@exe|"):
            _, exe, path = line.split("|", 2)
            check.executables[exe] = path.strip() or None
        elif line.startswith("@@missing|"):
            # ldd lists indirect dependencies too: the same library can come twice
            _, exe, rest = line.split("|", 2)
            m = _WRONG_VERSION.search(rest)
            if m:
                lib, by = m.group("lib").rsplit("/", 1)[-1], m.group("by").rsplit("/", 1)[-1]
                _add_once(check.wrong_versions.setdefault(exe, []),
                          f"{lib} lacks {m.group('version')} (needed by {by})")
            elif "=>" in rest:
                _add_once(check.missing_libraries.setdefault(exe, []),
                          rest.split("=>")[0].strip())
        elif in_list:
            name = line.strip()
            if name and not name.endswith(":") and "No Modulefiles" not in name:
                check.loaded.append(name)
        elif re.search(r"error|unable to locate|not found|unknown", line, re.I) and line.strip():
            # Environment Modules: "ERROR: Unable to locate a modulefile for 'x'";
            # Lmod: "Lmod has detected the following error: ... unknown: "x""
            check.errors.append(line.strip())
    return check


class Modules:
    """The module system of one cluster, with its extra module directories."""

    def __init__(self, transport: Transport, module_use: list[str] = ()):
        self.transport = transport
        self.module_use = list(module_use)
        self._system: ModuleSystem | None = None

    async def system(self) -> ModuleSystem:
        if self._system is None:
            r = await self.transport.run(
                'module --version 2>&1; echo "LMOD_VERSION=${LMOD_VERSION:-}"')
            text = r.stdout.replace("LMOD_VERSION=\n", "")
            self._system = parse_module_version(text)
        return self._system

    async def available(self) -> list[ModuleName]:
        system = await self.system()
        if not system.kind:
            return []
        use = _use_lines(self.module_use)
        if system.json_listing:
            r = await self.transport.run(use + "module -j avail 2>&1")
            try:
                return parse_avail_json(r.stdout)
            except (ValueError, AttributeError):
                pass                       # fall back to the terse listing
        r = await self.transport.run(use + "module -t avail 2>&1")
        return parse_avail_terse(r.stdout)

    async def details(self, names: list[str]) -> dict[str, ModuleDetails]:
        if not names:
            return {}
        quoted = " ".join(quote(n) for n in names)
        r = await self.transport.run(
            _use_lines(self.module_use)
            + f'for m in {quoted}; do echo "=== $m"; module show "$m" 2>&1; done')
        return parse_module_show(r.stdout)

    async def check(self, modules: list[str], executables: list[str]) -> LoadCheck:
        r = await self.transport.run(load_check_command(self.module_use, modules, executables))
        return parse_load_check(modules, executables, r.stdout)
