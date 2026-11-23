"""PseudoDojo's tables: the library on this PC, copies into a project with
their source and hints, the low-hint warning. Downloads are faked."""

import json
from pathlib import Path

import pytest

from fakes import upf
from visual_workflows.core import pseudodojo
from visual_workflows.core.input_checks import check_pseudopotentials
from visual_workflows.core.pseudodojo import (TABLES, Hints, Library, PseudoDojoError, add_to_project,
                                              read_sources, suggested_cutoffs, table, table_by_id)
from visual_workflows.core.pseudos import known_hints, pseudo_files
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input

# Hints as the tables give them (Ha); Ge without, as in PBE FR stringent
TABLE = {"pseudos_metadata": {
    "Si": {"basename": "Si.psp8", "Z_val": 4.0,
           "hints": {"low": {"ecut": 14.0}, "normal": {"ecut": 18.0}, "high": {"ecut": 24.0}}},
    "Mo": {"basename": "Mo-sp.psp8", "Z_val": 14.0,
           "hints": {"low": {"ecut": 36.0}, "normal": {"ecut": 40.0}, "high": {"ecut": 46.0}}},
    "Ge": {"basename": "Ge-d.psp8", "Z_val": 14.0, "hints": {}},
}}


class FakeWeb:
    """PseudoDojo's files, by URL; counts the downloads."""

    def __init__(self, files=None, fr=False):
        self.calls = []
        self.files = files if files is not None else {
            "Si": upf("Si", 4.0, has_so=fr), "Mo": upf("Mo", 14.0, has_so=fr),
            "Ge": upf("Ge", 14.0, has_so=fr)}

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith(".djson"):
            return json.dumps(TABLE).encode()
        element = url.rsplit("/", 1)[1].split("_")[0]
        if element not in self.files:
            raise OSError("404")
        return self.files[element].encode()


def test_tables():
    assert len(TABLES) == 8 and len({t.id for t in TABLES}) == 8
    t = table("pbe", "sr", "standard")
    assert t.id == "nc-sr-04_pbe_standard" and table_by_id(t.id) is t
    assert t.upf_path("Si") == "Si/Si_std.upf" and table("PBEsol", "FR", "stringent").upf_path("Si") == "Si/Si_str.upf"
    assert t.url("Si/Si_std.upf").startswith("https://raw.githubusercontent.com/PseudoDojo/ONCVPSP-PBE-SR/")
    assert t.commit in t.url("x")                                  # pinned, not a branch
    with pytest.raises(PseudoDojoError, match="no PseudoDojo table"):
        table("LDA", "SR", "standard")


def test_library_downloads_once(tmp_path):
    web = FakeWeb()
    lib = Library(tmp_path, web)
    t = table()
    entries = lib.get(t, ["Mo", "Si", "Mo"])
    assert [e.element for e in entries] == ["Mo", "Si"]
    assert entries[0].hints == Hints(72.0, 80.0, 92.0, 14.0)      # Ry: twice the table's Ha
    assert entries[0].path == tmp_path / t.id / "Mo.nc-sr-04_pbe_standard.upf"
    assert len(web.calls) == 3                                    # the table, Mo, Si
    lib.get(t, ["Si", "Mo"])
    assert len(web.calls) == 3                                    # all from the library now
    assert entries[0].url == t.url("Mo/Mo_std.upf")


@pytest.mark.parametrize("files, fr, message", [
    ({"Si": upf("Ge", 4.0)}, False, "the file is for Ge"),
    ({"Si": upf("Si", 3.0)}, False, "valence 3"),
    ({"Si": upf("Si", 4.0, has_so=True)}, False, "is fully relativistic"),
    ({"Si": upf("Si", 4.0)}, True, "is not fully relativistic"),
    ({"Si": "<html>Not Found</html>"}, False, "not a UPF file"),
    ({}, False, "could not download"),
])
def test_files_are_checked(tmp_path, files, fr, message):
    lib = Library(tmp_path, FakeWeb(files))
    with pytest.raises(PseudoDojoError, match=message):
        lib.get(table(relativistic="FR" if fr else "SR"), ["Si"])
    assert not any(tmp_path.rglob("*.upf"))                      # nothing bad kept


def test_element_not_in_the_table(tmp_path):
    with pytest.raises(PseudoDojoError, match="Og is not in"):
        Library(tmp_path, FakeWeb()).get(table(), ["Og"])


def test_project_copies_and_sources(tmp_path):
    lib = Library(tmp_path / "lib", FakeWeb())
    folder = tmp_path / "project" / "pseudos"
    names = add_to_project(lib.get(table(), ["Mo", "Ge"]), folder)
    assert names == ["Mo.nc-sr-04_pbe_standard.upf", "Ge.nc-sr-04_pbe_standard.upf"]
    assert all((folder / n).exists() for n in names)
    sources = read_sources(folder)
    assert sources[names[0]].hints.low == 72.0 and sources[names[0]].table == "nc-sr-04_pbe_standard"
    assert sources[names[1]].hints.low is None                    # no hints in the table
    assert "CC BY 4.0" in (folder / "sources.toml").read_text()
    # another table later: both recorded
    add_to_project(lib.get(table(accuracy="stringent"), ["Si"]), folder)
    assert len(read_sources(folder)) == 3
    # a file edited by hand is no longer the table's: no hints for it
    (folder / names[0]).write_text(upf("Mo", 14.0) + "<!-- edited -->")
    assert names[0] not in read_sources(folder) and names[0] in read_sources(folder, verify=False)


def test_suggested_cutoffs():
    hints = {"Mo": Hints(72.0, 80.0, 92.0, 14.0), "S": Hints(26.0, 30.0, 34.0, 6.0),
             "Ge": Hints(None, None, None, 14.0)}
    c = suggested_cutoffs(hints)
    assert (c.ecutwfc, c.ecutrho, c.without_hints) == (80.0, 320.0, ["Ge"])
    assert suggested_cutoffs(hints, "high").ecutwfc == 92.0
    assert suggested_cutoffs({"Ge": hints["Ge"]}).ecutwfc is None
    with pytest.raises(PseudoDojoError):
        suggested_cutoffs(hints, "medium")


def test_low_hint_warning(tmp_path):
    lib = Library(tmp_path / "lib", FakeWeb())
    project = tmp_path / "project"
    names = add_to_project(lib.get(table(), ["Mo", "Si"]), project / "pseudos")
    step = project / "scf"
    step.mkdir()
    text = ("&control\n/\n&system\n ibrav=1, celldm(1)=10, nat=2, ntyp=2, ecutwfc=60\n/\n"
            f"ATOMIC_SPECIES\n Mo 95.95 {names[0]}\n Si 28.09 {names[1]}\n"
            "ATOMIC_POSITIONS crystal\n Mo 0 0 0\n Si 0.5 0.5 0.5\nK_POINTS gamma\n")
    pw = read_pw_input(text)
    files = pseudo_files(pw, step)
    hints = known_hints(files, step)
    assert set(hints) == {"Mo", "Si"}
    headers = {}
    problems = check_pseudopotentials(pw, files, headers, hints=hints)
    assert len(problems) == 1 and not problems[0].error
    assert "below PseudoDojo's low hint for Mo (72 Ry)" in problems[0].message
    assert "Si" not in problems[0].message.split("for", 1)[1].split(":")[0]
    assert "normal: 80 Ry" in problems[0].message
    pw.set("system", "ecutwfc", 80)
    assert check_pseudopotentials(pw, files, headers, hints=hints) == []


def test_default_library_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert Library().root == tmp_path / "visual-suite" / "visual-workflows" / "pseudos"
    assert pseudodojo.Library(download=FakeWeb()).root.is_relative_to(Path(tmp_path))
