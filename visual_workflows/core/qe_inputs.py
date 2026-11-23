"""pw.x inputs made by the program: a structure and k-points put into a
`PwInput` (a new one, or one read from a file, keeping its other settings),
then written with `write_pw_input`.

A structure goes in with `ibrav = 0` (its exact cell in CELL_PARAMETERS, Å)
or with an ibrav and celldm (QE builds the cell, exact and in its own
orientation, so pw.x finds every symmetry operation), positions in crystal
coordinates. Species keep their order where they stay
(variables per species, as `starting_magnetization(i)`, follow them), new
ones come after. What changes beyond the cards is told in notes.
"""

from __future__ import annotations

import re

from ..yambopy_ext.qepy.pw_input import SPECIES_ARRAYS, PwInput
from .kpoints import BandPath, Grid
from .structure import Structure, StructureError, element

# The cell as an ibrav gives it: none of these with ibrav = 0
CELL_VARIABLES = ("celldm", "a", "b", "c", "cosab", "cosac", "cosbc", "space_group", "uniqueb",
                  "origin_choice", "rhombohedral")


def set_structure(pw: PwInput, structure: Structure, *, ibrav: int | str = 0,
                  pseudos: dict | None = None) -> list[str]:
    """Put the structure in `pw`: ibrav, its cell, nat, ntyp, ATOMIC_POSITIONS,
    ATOMIC_SPECIES. `ibrav`: 0 (CELL_PARAMETERS), "auto" (the lattice's own
    ibrav, or 0 where there is none here: monoclinic, triclinic), or a given
    one (StructureError if it is not the lattice's). `pseudos` gives a
    species' file ({"Mo": "Mo.upf"}); species already in the input keep
    theirs. Returns notes on what else changed or needs a look."""
    notes = []
    lattice = None
    if ibrav != 0:
        try:
            structure, lattice = structure.qe_lattice(None if ibrav == "auto" else int(ibrav))
        except StructureError as e:
            if ibrav != "auto":
                raise
            notes.append(f"written with ibrav = 0: {e}")
    system = pw.namelists.get("system", {})
    removed = [k for k in system if re.sub(r"\(.*", "", k) in CELL_VARIABLES]
    for key in removed:
        pw.unset("system", key)
    if removed:
        notes.append(f"the cell is now given in CELL_PARAMETERS (ibrav = 0): removed {', '.join(removed)}")

    old = _species(pw)
    kinds = list(dict.fromkeys(structure.kinds))
    order = [k for k in old if k in kinds] + [k for k in kinds if k not in old]
    notes += _renumber_species_arrays(pw, list(old), order)
    pseudos = pseudos or {}
    species = []
    for kind in order:
        if kind in old:
            mass, file = old[kind]
        else:
            from ase.data import atomic_masses, atomic_numbers
            mass = f"{atomic_masses[atomic_numbers[element(kind)]]:.4f}"
            file = pseudos.get(kind) or pseudos.get(element(kind)) or f"{kind}.upf"
            if kind not in pseudos and element(kind) not in pseudos:
                notes.append(f"{kind}: no pseudopotential chosen yet ({file} written)")
        species.append(f"{kind} {mass} {file}")
    gone = [k for k in old if k not in kinds]
    hubbard = pw.cards.get("HUBBARD")
    if gone and hubbard and any(k in line.split()[1].split("-")[0] for line in hubbard.lines
                                for k in gone if len(line.split()) > 1):
        notes.append(f"HUBBARD still names {', '.join(gone)}, no longer in the structure")

    flags = _position_flags(pw, structure.kinds)
    if flags is None:
        notes.append("fixed-coordinate flags of ATOMIC_POSITIONS dropped (the atoms changed)")
    cards = structure.pw_cards().splitlines()
    split = cards.index("ATOMIC_POSITIONS crystal")
    positions = cards[split + 1:]
    if flags:
        positions = [f"{line}  {flag}" if flag else line for line, flag in zip(positions, flags)]
    pw.set("system", "ibrav", lattice.ibrav if lattice else 0)
    if lattice:
        for i, value in enumerate(lattice.celldm, start=1):
            if value or i == 1:
                pw.set("system", f"celldm({i})", value)
    pw.set("system", "nat", len(structure.symbols))
    pw.set("system", "ntyp", len(order))
    pw.set_card("ATOMIC_SPECIES", "", species)
    if lattice:
        pw.remove_card("CELL_PARAMETERS")
    else:
        pw.set_card("CELL_PARAMETERS", "angstrom", cards[1:split])
        notes += orientation_notes(structure)
    pw.set_card("ATOMIC_POSITIONS", "crystal", positions)
    _structure_first(pw)
    return notes


def _structure_first(pw: PwInput) -> None:
    """ibrav, celldm, nat, ntyp at the top of &system, as people write them;
    the rest where it was."""
    values = pw.namelists["system"]
    first = ["ibrav"] + sorted(k for k in values if k.startswith("celldm(")) + ["nat", "ntyp"]
    pw.namelists["system"] = {**{k: values[k] for k in first if k in values},
                              **{k: v for k, v in values.items() if k not in first}}


def set_kpoints(pw: PwInput, kpoints: Grid | BandPath) -> None:
    """Put a grid (K_POINTS automatic) or a band path (K_POINTS crystal_b) in `pw`."""
    lines = kpoints.card().splitlines()
    header = lines[0].split()
    pw.set_card(header[0], header[1] if len(header) > 1 else "",
                [re.sub(r"\s*!.*", "", line) for line in lines[1:]])


def orientation_notes(structure: Structure) -> list[str]:
    """A note when pw.x would find fewer symmetry operations in this cell than
    the crystal has (pw.x tries only rotations about QE's standard axes)."""
    found = len(structure.pw_operations())
    full = structure.symmetry().operations
    if found >= full:
        return []
    turned = len(structure.oriented_for_pw().pw_operations())
    better = f"; turned to the standard orientation it finds {turned}" if turned > found else ""
    return [f"pw.x will find {found} of the crystal's {full} symmetry operations in this "
            f"cell's orientation{better} (Structure.oriented_for_pw)"]


def _species(pw: PwInput) -> dict:
    """{label: (mass, file)} of ATOMIC_SPECIES, in order."""
    card = pw.cards.get("ATOMIC_SPECIES")
    species = {}
    for line in card.lines if card else []:
        words = line.split()
        if len(words) >= 3:
            species[words[0]] = (words[1], words[2])
    return species


def _renumber_species_arrays(pw: PwInput, old: list, new: list) -> list[str]:
    """Move per-species values (`starting_magnetization(2)`...) with their
    species; drop those of species no longer there."""
    notes = []
    for namelist, values in pw.namelists.items():
        moved, dropped = {}, []
        for key in list(values):
            m = re.fullmatch(r"(\w+)\(([\d,\s]+)\)", key)
            if not m or m.group(1) not in SPECIES_ARRAYS:
                continue
            indices = [int(i) for i in m.group(2).split(",")]
            position = SPECIES_ARRAYS[m.group(1)]
            if position >= len(indices) or not 1 <= indices[position] <= len(old):
                continue
            value = values.pop(key)
            label = old[indices[position] - 1]
            if label not in new:
                dropped.append(key)
                continue
            indices[position] = new.index(label) + 1
            moved[f"{m.group(1)}({','.join(str(i) for i in indices)})"] = value
        values.update(moved)
        if dropped:
            notes.append(f"&{namelist}: removed {', '.join(dropped)} (species no longer there)")
    return notes


def _position_flags(pw: PwInput, kinds: list) -> list | None:
    """The fixed-coordinate flags (`0 0 1`) of the input's atoms when the atoms
    are the same kinds in the same order; [] without flags, None when they
    cannot be kept."""
    card = pw.cards.get("ATOMIC_POSITIONS")
    if not card:
        return []
    rows = [line.split() for line in card.lines]
    flags = [" ".join(words[4:7]) if len(words) >= 7 else "" for words in rows]
    if not any(flags):
        return []
    if [words[0] for words in rows] != list(kinds):
        return None
    return flags
