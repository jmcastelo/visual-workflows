"""Module listing, details and load tests, on Lynx's recorded outputs and on a
fake module system run through a real login shell."""

import textwrap

import pytest

from fakes import FakeTransport, recorded
from visual_workflows.core.modules import (ModuleName, Modules, for_program, labels,
                                           parse_avail_json, parse_avail_terse,
                                           parse_load_check, parse_module_show,
                                           parse_module_version)
from visual_workflows.core.transport import LocalTransport

SPACK = "/apps/spack/share/spack/modules/linux-arch-x86_64"


def lynx(name):
    return recorded("lynx", name)


@pytest.mark.parametrize("full, directory, expected", [
    ("qe/7.3.1b-intelmpi-omp", "", ("qe", "", "7.3.1b", "intelmpi-omp", "")),
    ("qe/6.0.0-intelmpi", "", ("qe", "", "6.0.0", "intelmpi", "")),
    ("yambo/gpl/5.2.4-single-omp", "", ("yambo", "gpl", "5.2.4", "single-omp", "")),
    ("yambo/gpl/5.2-single-omp", "", ("yambo", "gpl", "5.2", "single-omp", "")),
    ("yambo/intel-libs", "", ("yambo", "", "", "intel-libs", "")),
    ("qe/turboMagnon-intelmpi", "", ("qe", "", "", "turboMagnon-intelmpi", "")),
    # A branch name shaped like a Spack name is not one
    ("yambo/devel/devel-sc-rt-excitons-openmpi", "",
     ("yambo", "devel", "", "devel-sc-rt-excitons-openmpi", "")),
    ("quantum-espresso/7.5-intel-oneapi-compilers-2026.0.0-ra7jhfo", SPACK,
     ("quantum-espresso", "", "7.5", "intel-oneapi-compilers-2026.0.0", "ra7jhfo")),
    ("cmake/4.3.4-none-none-tc4tigz", SPACK, ("cmake", "", "4.3.4", "", "tc4tigz")),
    # Spack names are recognised outside a "spack" folder too
    ("hdf5/1.14.6-gcc-13.2.0-abcdefg", "/opt/mods", ("hdf5", "", "1.14.6", "gcc-13.2.0", "abcdefg")),
    ("dot", "/etc/modules/modulefiles", ("dot", "", "", "", "")),
])
def test_names(full, directory, expected):
    m = ModuleName.parse(full, directory)
    assert (m.program, m.variant, m.version, m.build, m.spack_hash) == expected
    assert m.full == full


def test_labels_show_spack_hash_only_when_needed():
    mods = parse_avail_terse(lynx("module-avail.txt"))
    names = labels(mods)
    # Two fftw 3.3.11 builds with the same compiler differ only by hash
    fftw = [n for m, n in names.items() if m.startswith("fftw/3.3.11-intel-oneapi")]
    assert sorted(fftw) == ["3.3.11 intel-oneapi-compilers-2026.0.0 [6zxivco]",
                            "3.3.11 intel-oneapi-compilers-2026.0.0 [neee2no]"]
    # Different compilers: no hash needed
    assert names["hdf5/1.14.6-intel-oneapi-compilers-2025.3.2-zcwpglx"] == \
        "1.14.6 intel-oneapi-compilers-2025.3.2"
    assert names["yambo/gpl/5.2.4-single-omp"] == "5.2.4 single-omp (gpl)"


def test_terse_and_json_listings_agree():
    terse = parse_avail_terse(lynx("module-avail.txt"))
    js = parse_avail_json(lynx("module-avail.json"))
    assert [m.full for m in terse] == [m.full for m in js]
    assert len(js) == 185
    qe = next(m for m in js if m.program == "quantum-espresso")
    assert qe.directory == SPACK and qe.from_spack


def test_modules_for_a_program():
    mods = parse_avail_json(lynx("module-avail.json"))
    qe = [m.full for m in for_program(mods, "qe")]
    assert qe[0] == "quantum-espresso/7.5-intel-oneapi-compilers-2026.0.0-ra7jhfo"
    assert qe[1:3] == ["qe/7.3.1b-intelmpi-omp", "qe/7.3.1-intelmpi-omp"]
    assert qe[-1] == "qe/turboMagnon-intelmpi"          # no version: last
    yambo = for_program(mods, "yambo")
    assert yambo[0].full == "yambo/gpl/5.2.4-single-omp"
    assert "yambo/intel-libs" in [m.full for m in yambo if not m.version]


def test_module_system():
    em = parse_module_version(lynx("module-version.txt"))
    assert (em.kind, em.version, em.json_listing) == ("environment-modules", "5.3.1", True)
    lmod = parse_module_version("Modules based on Lua: Version 8.7.37  2024-04-09\n"
                                "LMOD_VERSION=8.7.37\n")
    assert (lmod.kind, lmod.version, lmod.json_listing) == ("lmod", "8.7.37", False)
    assert parse_module_version("bash: module: command not found\n").kind == ""


def test_module_show():
    shown = parse_module_show(lynx("module-show.txt"))
    assert len(shown) == 45
    qe = shown["qe/7.3.1-intelmpi-omp"]
    assert qe.description == "Quantum Espresso 7.3.1 INTELMPI OMP"
    assert "libxc/intel/6.2.2-no-fhc" in qe.loads and "intel/mpi/2021.5.0" in qe.loads
    assert qe.path == ["/apps/packages/qe/7.3.1-intelmpi-omp/bin"]
    spack = shown["quantum-espresso/7.5-intel-oneapi-compilers-2026.0.0-ra7jhfo"]
    assert spack.description.startswith("Quantum ESPRESSO is an integrated suite")
    assert len(spack.loads) == 6 and spack.loads[0].startswith("glibc/")
    assert spack.path[0].endswith("/bin")
    # Yambo builds load MKL and MPI, not the I/O libraries
    y = shown["yambo/gpl/5.1.1-intelmpi"]
    assert y.loads == ["intel/mkl/2022.0.1", "intel/mpi/2021.5.0"]
    assert "netcdf-fortran/4.5.4-intelmpi" in shown["yambo/intel-libs"].loads


async def test_listing_commands_add_module_directories():
    t = FakeTransport({"module --version": (0, lynx("module-version.txt")),
                       f"module use {SPACK} 2>&1; module -j avail": (0, lynx("module-avail.json"))})
    mods = Modules(t, [SPACK])
    assert len(await mods.available()) == 185
    assert t.commands[-1].startswith(f"module use {SPACK} 2>&1; module -j avail")


async def test_listing_falls_back_to_terse():
    t = FakeTransport({"module --version": (0, lynx("module-version.txt")),
                       "module -j avail": (0, "ERROR: Invalid option '-j'\n"),
                       "module -t avail": (0, lynx("module-avail.txt"))})
    assert len(await Modules(t).available()) == 185


async def test_no_module_system():
    t = FakeTransport({"module --version": (0, "bash: module: command not found\n")})
    assert await Modules(t).available() == []


def test_parse_load_check():
    out = textwrap.dedent("""\
        ERROR: Unable to locate a modulefile for 'yambo/nope'
        @@load 1
        @@list
        intel/mkl/2022.0.1
        intel/mpi/2021.5.0
        @@end
        @@exe|yambo|/apps/packages/yambo/5.1.1-intelmpi/bin/yambo
        @@missing|yambo|\tlibnetcdff.so.7 => not found
        @@missing|yambo|\tlibhdf5_fortran.so.200 => not found
        @@exe|p2y|
        """)
    c = parse_load_check(["yambo/nope"], ["yambo", "p2y"], out)
    assert not c.ok and not c.loaded_ok
    assert c.errors == ["ERROR: Unable to locate a modulefile for 'yambo/nope'"]
    assert c.loaded == ["intel/mkl/2022.0.1", "intel/mpi/2021.5.0"]
    assert c.missing_libraries["yambo"] == ["libnetcdff.so.7", "libhdf5_fortran.so.200"]
    assert c.executables["p2y"] is None
    problems = "\n".join(c.problems())
    assert "p2y: not found" in problems and "libnetcdff.so.7" in problems


# -- the load test through a real shell --------------------------------------------------

FAKE_MODULES = r"""
loaded=""
module() {
    case "$1" in
        purge) loaded="" ;;
        use) ;;
        load) shift; for m in "$@"; do
                case "$m" in
                    qe/*|yambo/*|yambo-libs) loaded="$loaded $m"
                        export PATH="$HOME/apps/${m%%/*}/bin:$PATH" ;;
                    *) echo "ERROR: Unable to locate a modulefile for '$m'" >&2; return 1 ;;
                esac; done ;;
        -t) for m in $loaded; do echo "$m"; done ;;
    esac
}
export PATH="$HOME/fakebin:$PATH"
"""

FAKE_LDD = r"""#!/bin/bash
# Yambo needs libnetcdff, which only the yambo-libs module provides
case "$1" in
    */yambo/*) case " $LOADED_LIBS " in *yambo-libs*) ;; *) printf '\tlibnetcdff.so.7 => not found\n' ;; esac ;;
esac
printf '\tlibc.so.6 => /usr/lib/libc.so.6\n'
"""


@pytest.fixture
async def fake_cluster(tmp_path):
    home = tmp_path / "home"
    (home / "fakebin").mkdir(parents=True)
    (home / ".bash_profile").write_text(
        FAKE_MODULES.replace('loaded="$loaded $m"', 'loaded="$loaded $m"; export LOADED_LIBS="$loaded"'))
    ldd = home / "fakebin" / "ldd"
    ldd.write_text(FAKE_LDD)
    ldd.chmod(0o755)
    for program, exes in (("qe", ["pw.x", "ph.x"]), ("yambo", ["yambo", "p2y"])):
        (home / "apps" / program / "bin").mkdir(parents=True)
        for e in exes:
            f = home / "apps" / program / "bin" / e
            f.write_text("#!/bin/bash\n")
            f.chmod(0o755)
    async with LocalTransport(home) as t:
        yield t, home


async def test_load_check_ok(fake_cluster):
    t, home = fake_cluster
    c = await Modules(t, ["/opt/spack/modules"]).check(["qe/7.3.1"], ["pw.x", "ph.x"])
    assert c.ok, c.problems()
    assert c.loaded == ["qe/7.3.1"]
    assert c.executables["pw.x"] == str(home / "apps/qe/bin/pw.x")


async def test_load_check_finds_a_forgotten_library_module(fake_cluster):
    t, _ = fake_cluster
    c = await Modules(t).check(["yambo/5.1.1"], ["yambo", "p2y"])
    assert not c.ok
    assert c.missing_libraries["yambo"] == ["libnetcdff.so.7"]
    c = await Modules(t).check(["yambo-libs", "yambo/5.1.1"], ["yambo", "p2y"])
    assert c.ok, c.problems()


async def test_load_check_unknown_module_and_missing_executable(fake_cluster):
    t, _ = fake_cluster
    c = await Modules(t).check(["qe/7.3.1", "nope/1.0"], ["pw.x", "yambo"])
    assert not c.loaded_ok
    assert any("nope/1.0" in e for e in c.errors)
    assert c.executables["yambo"] is None
