"""Crystal structures for pw.x inputs: read from files (ASE), their symmetry
(spglib), and written as pw.x cards.

New structures are written with ibrav = 0, their exact cell in
CELL_PARAMETERS (Å) and positions in crystal coordinates, as ASE does: pw.x
finds the same symmetry as with an ibrav (it looks for the operations of the
cell it is given), without re-expressing the positions in QE's own choice of
cell vectors. Cells read from experiment carry noise that hides symmetry:
`primitive()` gives spglib's primitive, idealised cell. The Bravais lattice
is named as the YamboPy fork names it (lattice_data_for_cell: the
Setyawan–Curtarolo variants), as the Brillouin-zone paths are.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# spglib wants absolute tolerances; these are relative to the longest cell vector
DEFAULT_SYMPREC = 1e-5


def element(label: str) -> str:
    """The element of a species label (Fe, Fe1, Fe_up, W2 -> Fe, Fe, Fe, W)."""
    from ase.data import chemical_symbols
    import re
    letters = re.match(r"[A-Za-z]+", label)
    if letters:
        word = letters.group()
        for candidate in (word[:2].capitalize(), word[:1].upper()):
            if candidate in chemical_symbols[1:]:
                return candidate
    raise StructureError(f"species {label!r}: no element in its name")


class StructureError(ValueError):
    """A file that is not a structure ASE can read, or a structure without a cell."""


@dataclass
class Symmetry:
    """What spglib finds in a structure (in its own cell)."""
    number: int                      # space group, 1-230
    international: str               # e.g. "Fd-3m"
    point_group: str                 # e.g. "m-3m"
    operations: int                  # in the cell given (pw.x's count, without fractional ones dropped)
    fractional: int                  # of those, with a fractional translation
    lattice: str = ""                # Bravais lattice, e.g. "FCC", "HEX" ("" when not covered)

    @property
    def symmorphic(self) -> bool:
        """No operation needs a fractional translation (in this cell): p2y takes it as it is."""
        return self.fractional == 0


@dataclass
class Structure:
    cell: np.ndarray                 # lattice vectors as rows, Å
    symbols: list[str]
    positions: np.ndarray            # fractional (crystal) coordinates
    name: str = ""
    periodic: tuple = field(default=(True, True, True))
    # Species labels where they differ from the elements (Fe1, Fe2: two kinds of
    # Fe, as pw.x's ATOMIC_SPECIES has them): symmetry tells kinds apart
    labels: list = field(default_factory=list)

    # -- reading ------------------------------------------------------------------------

    @classmethod
    def read(cls, path, format: str | None = None) -> "Structure":
        """A structure from a file ASE reads (CIF, POSCAR, XYZ with a cell, a pw.x
        input or output...), the last image if there are several."""
        import ase.io
        path = Path(path)
        try:
            atoms = ase.io.read(path, format=format, index=-1)
        except Exception as e:
            raise StructureError(f"{path.name}: not a structure ASE can read ({e})") from None
        return cls.from_atoms(atoms, name=path.stem)

    @classmethod
    def from_pw_input(cls, text: str, name: str = "") -> "Structure":
        """The structure of a pw.x input, as pw.x builds it: any ibrav, celldm or
        A, B, C, positions in any units (QE's latgen, ported: ASE reads only
        ibrav = 0)."""
        from ..yambopy_ext.qepy.lattice import BOHR_ANGSTROM, LatticeError, pw_cell, pw_positions
        from ..yambopy_ext.qepy.pw_input import PwInputError, read_pw_input
        try:
            pw = read_pw_input(text)
            cell, _ = pw_cell(pw)
            labels, cartesian = pw_positions(pw)
        except (PwInputError, LatticeError, ValueError, IndexError) as e:
            raise StructureError(f"the pw.x input's structure cannot be read ({e})") from None
        cell = np.array(cell) * BOHR_ANGSTROM
        positions = np.array(cartesian) * BOHR_ANGSTROM @ np.linalg.inv(cell)
        symbols = [element(label) for label in labels]
        return cls(cell, symbols, positions % 1.0, name,
                   labels=list(labels) if labels != symbols else [])

    @classmethod
    def from_atoms(cls, atoms, name: str = "") -> "Structure":
        if not np.any(atoms.cell.array):
            raise StructureError("the structure has no cell (a molecule?): give it a box first")
        return cls(np.array(atoms.cell.array, dtype=float), list(atoms.get_chemical_symbols()),
                   np.array(atoms.get_scaled_positions(wrap=True), dtype=float), name,
                   tuple(bool(x) for x in atoms.pbc))

    def to_atoms(self):
        from ase import Atoms
        return Atoms(symbols=self.symbols, scaled_positions=self.positions, cell=self.cell,
                     pbc=True)

    # -- what it is ---------------------------------------------------------------------

    @property
    def species(self) -> list[str]:
        """Each element once, in order of appearance (ATOMIC_SPECIES)."""
        return list(dict.fromkeys(self.symbols))

    @property
    def formula(self) -> str:
        return self.to_atoms().get_chemical_formula(mode="hill")

    @property
    def volume(self) -> float:
        return float(abs(np.linalg.det(self.cell)))

    @property
    def kinds(self) -> list[str]:
        """Each atom's kind: its species label, or its element."""
        return list(self.labels) if self.labels else list(self.symbols)

    def _spglib_cell(self):
        kinds = self.kinds
        order = list(dict.fromkeys(kinds))
        return (self.cell, self.positions, [order.index(k) + 1 for k in kinds])

    def _dataset(self, symprec: float):
        import spglib
        scale = float(np.max(np.linalg.norm(self.cell, axis=1)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            dataset = spglib.get_symmetry_dataset(self._spglib_cell(), symprec=symprec * scale)
        if dataset is None:
            raise StructureError("spglib could not find this structure's symmetry")
        return dataset

    def symmetry(self, symprec: float = DEFAULT_SYMPREC) -> Symmetry:
        dataset = self._dataset(symprec)
        translations = np.asarray(dataset.translations)
        wrapped = translations - np.round(translations)
        fractional = int(np.sum(np.any(np.abs(wrapped) > 1e-6, axis=1)))
        lattice = ""
        try:
            from yambopy.bz.lattice_data import lattice_data_for_cell
            lattice = lattice_data_for_cell(self.cell, symprec=symprec)[1]
        except (ImportError, ValueError):
            pass                         # monoclinic, triclinic: no name there
        return Symmetry(dataset.number, dataset.international, dataset.pointgroup,
                        len(dataset.rotations), fractional, lattice)

    def pw_operations(self, nofrac: bool = False) -> list:
        """The operations pw.x will use for this cell as given, [(M, t)] in crystal
        coordinates (QE's own search, ported: only rotations of a lattice in
        QE's standard orientation, translations 0 or 1/n). Fewer than
        `symmetry().operations` when the cell is turned or a translation is odd."""
        from ..yambopy_ext.qepy.symmetry import pw_operations
        return pw_operations(self.cell.tolist(), self.positions.tolist(), self.kinds, nofrac=nofrac)

    def primitive(self, symprec: float = DEFAULT_SYMPREC) -> "Structure":
        """spglib's primitive cell, idealised (exact symmetry, noise removed), in its
        standard orientation."""
        import spglib
        scale = float(np.max(np.linalg.norm(self.cell, axis=1)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            found = spglib.standardize_cell(self._spglib_cell(), to_primitive=True,
                                            no_idealize=False, symprec=symprec * scale)
        if found is None:
            raise StructureError("spglib could not find a primitive cell")
        cell, positions, numbers = found
        kinds = list(dict.fromkeys(self.kinds))
        new_kinds = [kinds[n - 1] for n in numbers]
        symbols = [element(k) for k in new_kinds]
        return Structure(np.array(cell), symbols, np.array(positions) % 1.0, self.name,
                         self.periodic, labels=new_kinds if self.labels else [])

    # -- pw.x ---------------------------------------------------------------------------

    def pw_cards(self, digits: int = 10) -> str:
        """CELL_PARAMETERS (Å) and ATOMIC_POSITIONS (crystal), for ibrav = 0."""
        width = digits + 5
        lines = ["CELL_PARAMETERS angstrom"]
        lines += ["  " + " ".join(f"{x:{width}.{digits}f}" for x in row) for row in self.cell]
        lines.append("ATOMIC_POSITIONS crystal")
        lines += [f"  {s:<3}" + " ".join(f"{x:{width}.{digits}f}" for x in p)
                  for s, p in zip(self.symbols, self.positions)]
        return "\n".join(lines) + "\n"
