#!/usr/bin/env python3
"""Regenerate the lists of input names per version, from clones of QE and Yambo:

    visual_workflows/yambopy_ext/qepy/pw_namelists.py     (pw.x namelists)
    visual_workflows/yambopy_ext/io/yambo_variables.py    (yambo names, and the
                                                          command-line options)

Usage: scripts/extract-input-names.py QE_REPO YAMBO_REPO
       [--qe qe-7.3.1 qe-7.5 qe-7.6] [--yambo 5.1.1 5.3.0 5.4-beta]

Tags are read with `git show`: the clones are not checked out or changed.
Each module's docstring says how its lists are made; this script does that
and writes the data part (the rest of each module is kept).
"""

import argparse
import json
import pprint
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QE_MODULE = ROOT / "visual_workflows/yambopy_ext/qepy/pw_namelists.py"
YAMBO_MODULE = ROOT / "visual_workflows/yambopy_ext/io/yambo_variables.py"


def show(repo, tag, path):
    r = subprocess.run(["git", "-C", repo, "show", f"{tag}:{path}"], capture_output=True, text=True)
    return r.stdout


def strip_comments(text):
    """Fortran free form: '!' outside strings ends the line; continuations joined."""
    lines = []
    for line in text.splitlines():
        out, quote = "", None
        for ch in line:
            if quote:
                out += ch
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
                out += ch
            elif ch == "!":
                break
            else:
                out += ch
        lines.append(out.rstrip())
    return re.sub(r"&\s*\n\s*&?", " ", "\n".join(lines))


# -- QE ---------------------------------------------------------------------------------

def qe_namelists(repo, tag):
    text = strip_comments(show(repo, tag, "Modules/input_parameters.f90"))
    names = {}
    for m in re.finditer(r"NAMELIST\s*/\s*(\w+)\s*/([^\n]*)", text, re.I):
        names.setdefault(m.group(1).lower(), []).extend(
            w.strip().lower() for w in m.group(2).split(",") if w.strip())
    return {k: sorted(set(v)) for k, v in names.items()}


# -- Yambo ------------------------------------------------------------------------------

YAMBO_FILES = ["src/interface/INIT_load.F", "src/interface/INIT_QP_ctl_load.F",
               "src/interface/INIT_RT_ctl_load.F", "src/interface/INIT_C_driver_runlevels.F"]


def call_args(text):
    out, depth, cur, quote = [], 0, "", None
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            cur += ch
            continue
        if ch == "(":
            depth += 1
        if ch == ")":
            if depth == 0:
                out.append(cur.strip())
                return out
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    out.append(cur.strip())
    return out


def yambo_names(repo, tag):
    literal, built, units = {}, [], {}
    for path in YAMBO_FILES:
        for m in re.finditer(r"call\s+it\s*\((.*)", strip_comments(show(repo, tag, path)), re.I):
            a = call_args(m.group(1))
            if len(a) < 2:
                continue
            if re.fullmatch(r"'[rf]'", a[0]):
                kind, name = ("runlevel" if a[0] == "'r'" else "flag"), a[2]
            else:
                kind, name = "variable", a[1]
            lit = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", name)
            unit = next((m.group(1) for x in a
                         for m in [re.fullmatch(r"(?:unit\s*=\s*)?(\w+_unit)", x)] if m), None)
            if lit:
                literal[(lit.group(1) or lit.group(2)).strip()] = kind
                if unit:
                    units[(lit.group(1) or lit.group(2)).strip()] = unit
            elif unit and name.startswith("nms("):
                units[name] = unit
            else:
                built.append((path.split("/")[-1], name))
    runlevels = {n for n, k in literal.items() if k == "runlevel"}
    flags = {n for n, k in literal.items() if k == "flag"}
    variables = {n for n, k in literal.items() if k == "variable"}
    files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", tag],
                           capture_output=True, text=True).stdout.split()
    for path in files:
        if re.search(r"driver/options_(yambo|projects|control)\.c$", path):
            runlevels |= set(re.findall(r'yambo_string="([^"]+)"', show(repo, tag, path))) - {"undef"}
    load = show(repo, tag, "src/interface/INIT_load.F")
    stems = re.findall(r"'(\w+)'", re.search(r"nms=\(/(.*?)/\)",
                                             re.sub(r"&\s*\n\s*&?", " ", load), re.S).group(1))
    suffixes = set(re.findall(r"sfx='(\w+)'", load))
    variables |= {s[:6] + x for s in stems for x in suffixes}
    for key, unit in list(units.items()):
        if key.startswith("nms("):
            stem = stems[int(re.search(r"\d+", key).group()) - 1]
            units.update({stem[:6] + x: unit for x in suffixes})
            del units[key]
    sections = re.findall(r'Short_Description="(\w+)"', show(repo, tag, "src/modules/mod_parallel.F"))
    for s in sections:
        variables |= {f"{s}_CPU", f"{s}_ROLEs", f"{s}_nCPU_LinAlg_INV", f"{s}_nCPU_LinAlg_DIAGO"}
    for path in ("src/interface/INIT_QP_ctl_load.F", "src/interface/INIT_RT_ctl_load.F"):
        text = show(repo, tag, path)
        prefixes = re.findall(r"'(\w)'", re.search(r"prefix=\(/(.*?)/\)", text).group(1))
        spin = re.search(r"spin_ch=\(/(.*?)/\)", text)
        spins = [s.strip() for s in re.findall(r"'([^']*)'", spin.group(1))] if spin else [""]
        for _, b in [x for x in built if x[0] == path.split("/")[-1]]:
            parts = re.findall(r"'([^']*)'", b)
            for p in prefixes:
                if "spin_ch" in b:
                    variables |= {p + parts[0] + sp + parts[1] for sp in spins}
                else:
                    variables.add(p + "".join(parts))
    grep = subprocess.run(["git", "-C", repo, "grep", "-hoE",
                           r"""PARSER_array\([^)]*VAR=["'][A-Za-z_0-9]+["']|call +parser\( *["'][A-Za-z_0-9]+["']""",
                           tag, "--", "src/*"], capture_output=True, text=True).stdout
    for name in re.findall(r"""["']([A-Za-z_0-9]+)["']\s*$""", grep, re.M):
        if name not in runlevels and name not in flags:
            variables.add(name)
    unresolved = [b for f, b in built if f == "INIT_load.F" and "trim(name)" not in b
                  and not b.startswith("nms(")]
    if unresolved:
        sys.exit(f"{tag}: names built in a way this script does not know: {unresolved}")
    return {"runlevels": sorted(runlevels), "flags": sorted(flags), "variables": sorted(variables),
            "patterns": [r"Field\d+_(Freq|NFreqs|DFreq|Int|Width|FWHM|kind|pol|Dir|Dir_circ|Tstart)"],
            "sections": sections,
            # The kind of unit each variable takes (E_unit: an energy, read in Ha
            # without one; G_unit: G vectors, a count without one)
            "units": {k: units[k] for k in sorted(units)}}


# Strings an option may set without making yambo edit its input instead of
# running (INIT_read_command_line.F: strings_to_not_use_as_runlevels)
NOT_RUNLEVELS = {"jobstr", "ifile", "idir", "odir", "cdir", "com_path", "more_io_path",
                 "core_io_path", "wallt", "memorylimit"}
EXECUTABLES = ("yambo", "ypp", "p2y")


def yambo_options(repo, tag):
    """{executable: [{short, long, arg, kind}]}: kind "job" (fine in a job),
    "editing" (yambo writes the input file and exits), "exit" (prints and exits)."""
    files = subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", tag],
                           capture_output=True, text=True).stdout.split()
    result = {exe: [] for exe in EXECUTABLES}
    for path in files:
        if not re.search(r"driver/options_\w+\.c$", path):
            continue
        for block in re.split(r"\*i_opt=\*i_opt\+1;", show(repo, tag, path))[1:]:
            o = {k: v.strip().strip("\"'") for k, v in
                 re.findall(r"options\[\*i_opt\]\.(\w+)=([^;]+);", block)}
            if "short_opt" not in o and "long_opt" not in o:
                continue
            bins = o.get("bin", "all").split()      # options_maker.c: "all" by default
            arg = ("int" if o.get("int_var") else "char" if o.get("char_var")
                   else "float" if o.get("float_var") else "")
            string = o.get("yambo_string", "")
            for exe in EXECUTABLES:
                if exe not in bins and "all" not in bins:
                    continue
                if o.get("long_opt") in ("help", "version"):
                    kind = "exit"
                elif string and string not in NOT_RUNLEVELS and exe in bins and exe != "p2y":
                    kind = "editing"     # a runlevel of this executable: input editing
                    # (p2y has no input file: its strings are its own switches)
                else:
                    kind = "job"
                entry = {"short": o.get("short_opt", ""), "long": o.get("long_opt", ""),
                         "arg": arg, "kind": kind}
                if entry not in result[exe]:
                    result[exe].append(entry)
    return result


def write_data(module, name, data):
    text = module.read_text()
    start = text.index(f"{name} = ")
    end = text.index("\n\n\n", start)        # the data ends at the first gap
    module.write_text(text[:start] + f"{name} = " + pprint.pformat(data, width=100, compact=True)
                      + text[end:])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("qe_repo")
    ap.add_argument("yambo_repo")
    ap.add_argument("--qe", nargs="+", default=["qe-7.3.1", "qe-7.5", "qe-7.6"])
    ap.add_argument("--yambo", nargs="+", default=["5.1.1", "5.3.0", "5.4-beta"])
    args = ap.parse_args()
    write_data(QE_MODULE, "PW_NAMELISTS",
               {t.removeprefix("qe-"): qe_namelists(args.qe_repo, t) for t in args.qe})
    write_data(YAMBO_MODULE, "YAMBO_NAMES",
               {re.sub(r"-.*$", "", t): yambo_names(args.yambo_repo, t) for t in args.yambo})
    write_data(YAMBO_MODULE, "YAMBO_OPTIONS",
               {re.sub(r"-.*$", "", t): yambo_options(args.yambo_repo, t) for t in args.yambo})
    print(f"Written {QE_MODULE.relative_to(ROOT)} and {YAMBO_MODULE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
