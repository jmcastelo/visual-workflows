"""Checks of pw.x and yambo inputs and command lines, before they are submitted.

What the programs would stop on at startup (errors), and what they would
silently do differently from what was asked (errors when the result would be
wrong, warnings when it is only worth a look). The rules are those of the
source code (QE 7.3.1, 7.5, 7.6; Yambo 5.1.1, 5.3.0, 5.4-beta), collected in
docs/quantum-espresso.md §3 and docs/yambo.md §2 and §4. The parallel
variables are checked in parallel.py; checks needing the pseudopotentials or
the SAVE folder come later (docs/design.md, Phase 2 plan, task 4b).
"""

from __future__ import annotations

import re

from ..yambopy_ext.io.yambo_input import YamboInput
from ..yambopy_ext.io.yambo_variables import ypp_names, canonical_name, yambo_names, yambo_options
from ..yambopy_ext.qepy.pw_input import PwInput, PwInputError
from ..yambopy_ext.qepy.pw_namelists import pw_namelists
from .problems import Problem

# -- pw.x -----------------------------------------------------------------------------

# The namelists pw.x reads, in the order it reads them (read_namelists.f90)
PW_NAMELIST_ORDER = ("control", "system", "electrons", "ions", "cell", "fcp", "rism")
CALCULATIONS = ("scf", "nscf", "bands", "relax", "md", "vc-relax", "vc-md", "ensemble")
OCCUPATIONS = ("fixed", "smearing", "tetrahedra", "tetrahedra_lin", "tetrahedra-lin",
               "tetrahedra_opt", "tetrahedra-opt", "from_input")
SMEARINGS = ("gaussian", "gauss", "Gaussian", "Gauss", "methfessel-paxton", "m-p", "mp",
             "Methfessel-Paxton", "M-P", "MP", "marzari-vanderbilt", "cold", "m-v", "mv",
             "Marzari-Vanderbilt", "M-V", "MV", "fermi-dirac", "f-d", "fd", "Fermi-Dirac",
             "F-D", "FD")
DIAGONALIZATIONS = ("david", "davidson", "cg", "paro", "ParO", "rmm", "rmm-diis",
                    "rmm-davidson", "rmm-paro")
DISK_IO = ("high", "medium", "low", "nowf", "minimal", "none")
BRAVAIS = (0, 1, 2, 3, -3, 4, 5, -5, 6, 7, 8, 9, -9, 91, 10, 11, 12, -12, 13, -13, 14)
POSITIONS = ("alat", "bohr", "angstrom", "crystal", "crystal_sg")
K_POINTS = ("tpiba", "automatic", "crystal", "gamma", "tpiba_b", "crystal_b", "tpiba_c",
            "crystal_c")
SCF_TYPES = ("scf", "relax", "md", "vc-relax", "vc-md", "ensemble")
# Still declared, but pw.x stops when any is set to something other than its
# default (read_namelists.f90, 7.3.1 to 7.6): DFT+U goes in the HUBBARD card
OBSOLETE_HUBBARD = ("lda_plus_u", "lda_plus_u_kind", "u_projection_type", "hubbard_parameters",
                    "hubbard_u", "hubbard_u_back", "hubbard_j0", "hubbard_j", "hubbard_v", "backall")


def check_pw_input(text: str, pw: PwInput, version: tuple[int, int] | None = None,
                   where: str = "") -> list[Problem]:
    """Problems of a pw.x input that pw.x reports only on the cluster, or
    silently works around. `version`: QE's (major, minor), None if unknown."""
    problems: list[Problem] = []

    def error(message):
        problems.append(Problem(message, where))

    def warning(message):
        problems.append(Problem(message, where, error=False))

    def get(namelist, name, default=None):
        return pw.get(namelist, name, default)

    if text.lstrip().startswith("<?xml"):
        error("an XML input: pw.x stops (xml input disabled).")
        return problems

    # Namelists: known, in order, the required ones there
    known = pw_namelists(version)
    order = [n for n in pw.namelist_order if n in PW_NAMELIST_ORDER]
    for name in pw.namelist_order:
        if name not in known:
            error(f"&{name.upper()} is not a pw.x namelist: pw.x stops.")
        elif name not in PW_NAMELIST_ORDER:
            warning(f"&{name.upper()} is not read by pw.x.")
    expected = [n for n in PW_NAMELIST_ORDER if n in order]
    if order != expected:
        error("namelists out of order (" + ", ".join(f"&{n.upper()}" for n in order)
              + "): pw.x reads them as " + ", ".join(f"&{n.upper()}" for n in expected)
              + " and stops.")
    calculation = get("control", "calculation", "scf")
    required = ["control", "system", "electrons"]
    if calculation in ("relax", "md", "vc-relax", "vc-md"):
        required.append("ions")
    if calculation in ("vc-relax", "vc-md"):
        required.append("cell")
    for name in required:
        if name not in pw.namelists:
            error(f"no &{name.upper()}: pw.x needs it" + (f" for {calculation}" if name in
                                                          ("ions", "cell") else "") + ".")
    if calculation in ("nscf", "bands") and "ions" in pw.namelists:
        warning(f"&IONS is not read in a {calculation} run (pw.x ignores it).")

    # Variables: names of this version, values pw.x accepts
    label = f"QE {version[0]}.{version[1]}" if version else "pw.x"
    for namelist, values in pw.namelists.items():
        allowed = known.get(namelist)
        if allowed is None:
            continue
        for key, value in values.items():
            base = key.split("(")[0]
            if base in OBSOLETE_HUBBARD and value not in (False, 0, -1, ""):
                error(f"{key} in &{namelist.upper()}: DFT+U is given in the HUBBARD card since QE 7.1; "
                      "pw.x stops.")
            elif base not in allowed:
                error(f"{key} is not a variable of &{namelist.upper()} in {label}: pw.x stops "
                      "(bad line in namelist).")
    _check_value(calculation, CALCULATIONS, "calculation", error, case=True)
    occupations = get("system", "occupations", "fixed")
    _check_value(occupations, OCCUPATIONS, "occupations", error)
    if occupations == "smearing":
        _check_value(get("system", "smearing", "gaussian"), SMEARINGS, "smearing", error)
        degauss = get("system", "degauss", 0)
        if not isinstance(degauss, (int, float)) or degauss <= 0:
            error("smearing without degauss: pw.x stops (smearing requires a value for "
                  "gaussian broadening).")
    elif occupations == "fixed" and get("system", "degauss", 0):
        warning("degauss with fixed occupations: pw.x ignores it.")
    diagonalizations = DIAGONALIZATIONS + (("ppcg",) if version and version < (7, 5) else ()) \
        + (("direct",) if version is None or version >= (7, 6) else ())
    _check_value(get("electrons", "diagonalization", "david"), diagonalizations,
                 "diagonalization", error)
    _check_value(get("control", "restart_mode", "from_scratch"), ("from_scratch", "restart"),
                 "restart_mode", error)
    disk_io = get("control", "disk_io")
    if disk_io is not None and disk_io not in DISK_IO:
        warning(f"disk_io = {disk_io!r} is not one of {', '.join(DISK_IO)}: pw.x silently uses "
                "its default.")
    max_seconds = get("control", "max_seconds")
    if isinstance(max_seconds, (int, float)) and max_seconds < 0:
        error("max_seconds is negative: pw.x stops.")

    # Folders: pw.x takes them as written (trimcheck.f90: no $ or ~ expansion)
    for name in ("pseudo_dir", "outdir", "wfcdir"):
        value = get("control", name)
        if not isinstance(value, str):
            continue
        if "$" in value or value.startswith("~"):
            error(f"{name} = {value!r}: pw.x does not expand $ or ~ (it would look for a folder "
                  f"named so); write the full path{', or use the project pseudos/ folder or the profile library' if name == 'pseudo_dir' else ''}.")
        elif len(value.strip()) > 255:
            error(f"{name} is longer than 255 characters: pw.x stops (input name too long).")

    _check_lattice(pw, error)
    _check_spin(pw, calculation, error, warning)

    ecutwfc, ecutrho = get("system", "ecutwfc"), get("system", "ecutrho")
    for name, value in (("ecutwfc", ecutwfc), ("ecutrho", ecutrho)):
        if isinstance(value, (int, float)) and value < 0:
            error(f"{name} is negative: pw.x stops.")
    if isinstance(ecutwfc, (int, float)) and isinstance(ecutrho, (int, float)) and ecutwfc > 0:
        if ecutrho <= ecutwfc:
            error(f"ecutrho ({ecutrho:g}) is not above ecutwfc ({ecutwfc:g}): pw.x stops.")
        elif ecutrho < 4 * ecutwfc:
            warning(f"ecutrho ({ecutrho:g}) is below 4 × ecutwfc: unusual (pw.x asks "
                    "\"are you sure?\").")

    _check_cards(pw, occupations, error, warning)
    return problems


def _check_value(value, allowed, name, error, case=False):
    if value is not None and value not in allowed:
        hint = ""
        if case and isinstance(value, str) and value.lower() in allowed:
            hint = f" (pw.x compares values with their case: {value.lower()!r})"
        error(f"{name} = {value!r} is not one pw.x accepts{hint}: pw.x stops.")


def _check_lattice(pw: PwInput, error):
    ibrav = pw.get("system", "ibrav")
    celldm1, a = pw.get("system", "celldm(1)"), pw.get("system", "a")
    cell = pw.cards.get("CELL_PARAMETERS")
    nat, ntyp = pw.get("system", "nat"), pw.get("system", "ntyp")
    if isinstance(nat, int) and nat < 0:
        error("nat is negative: pw.x stops.")
    if isinstance(ntyp, int) and ntyp > 10:
        error(f"ntyp = {ntyp}: pw.x takes at most 10 species (ntyp too large).")
    if ibrav is None:
        return
    if ibrav not in BRAVAIS:
        error(f"ibrav = {ibrav} is not a Bravais lattice pw.x knows: it stops.")
    if celldm1 is not None and a is not None:
        error("both celldm(1) and A: pw.x stops (do not specify both).")
    if ibrav == 0:
        if cell is None:
            error("ibrav = 0 without CELL_PARAMETERS: pw.x stops.")
        elif cell.option in ("bohr", "angstrom") and (celldm1 is not None or a is not None):
            error(f"CELL_PARAMETERS in {cell.option} and celldm(1) or A: the lattice parameter "
                  "is given twice; pw.x stops.")
        elif cell.option == "alat" and celldm1 is None and a is None:
            error("CELL_PARAMETERS in alat without celldm(1) or A: pw.x stops.")
    else:
        if cell is not None:
            error(f"CELL_PARAMETERS with ibrav = {ibrav}: redundant, pw.x stops.")
        if celldm1 is None and a is None:
            error(f"ibrav = {ibrav} without celldm(1) or A: pw.x stops.")


def _check_spin(pw: PwInput, calculation, error, warning):
    nspin = pw.get("system", "nspin", 1)
    noncolin = pw.get("system", "noncolin", False) is True
    if nspin not in (1, 2, 4):
        error(f"nspin = {nspin}: pw.x takes 1, 2 or 4.")
    if noncolin and nspin == 2:
        error("noncolin with nspin = 2: pw.x stops (conflicting flags).")
    if pw.get("system", "lspinorb", False) is True and not noncolin:
        error("lspinorb without noncolin: spin–orbit needs a noncollinear calculation; pw.x stops.")
    if noncolin:
        try:
            kpoints = pw.k_points()
        except PwInputError:
            kpoints = None
        if kpoints is not None and kpoints.kind == "gamma":
            error("noncolin with K_POINTS gamma: pw.x stops.")
    magnetisations = [k for k in pw.namelists.get("system", {}) if k.startswith("starting_magnetization")]
    total = pw.get("system", "tot_magnetization")
    if total is not None and nspin != 2:
        error("tot_magnetization without nspin = 2: pw.x stops.")
    if nspin == 2 and calculation in SCF_TYPES:
        occupations = pw.get("system", "occupations", "fixed")
        if occupations == "fixed" and total is None:
            error("nspin = 2 with fixed occupations needs tot_magnetization: pw.x stops.")
        elif occupations == "smearing" and total is None and not magnetisations:
            error("nspin = 2 with smearing needs some starting_magnetization (or "
                  "tot_magnetization): pw.x stops.")
    if magnetisations and nspin == 1 and not noncolin:
        warning("starting_magnetization with nspin = 1 and no noncolin: not used.")


def _check_cards(pw: PwInput, occupations, error, warning):
    cards = list(pw.cards)
    for name, card in pw.cards.items():
        for line in card.misread:
            error(f"{name}: the line {line!r} looks like a comment, but pw.x reads it as data "
                  "(only blank lines and '#' in the first column are skipped).")
    species = pw.cards.get("ATOMIC_SPECIES")
    positions = pw.cards.get("ATOMIC_POSITIONS")
    ntyp, nat = pw.get("system", "ntyp"), pw.get("system", "nat")
    labels = []
    if species is None:
        error("no ATOMIC_SPECIES: pw.x stops.")
    else:
        labels = [line.split()[0] for line in species.lines if line.split()]
        if isinstance(ntyp, int):
            if len(labels) < ntyp:
                error(f"ATOMIC_SPECIES has {len(labels)} species for ntyp = {ntyp}: pw.x stops.")
            elif len(labels) > ntyp:
                warning(f"ATOMIC_SPECIES has {len(labels)} lines for ntyp = {ntyp}: the extra "
                        "ones are ignored.")
                labels = labels[:ntyp]
        repeated = sorted({x for x in labels if labels.count(x) > 1})
        if repeated:
            error(f"ATOMIC_SPECIES: {', '.join(repeated)} given twice: pw.x stops.")
        for line in species.lines[: len(labels)]:
            if len(line.split()) < 3:
                error(f"ATOMIC_SPECIES: {line!r} is not a label, a mass and a file: pw.x stops.")
    if positions is None:
        error("no ATOMIC_POSITIONS: pw.x stops.")
    else:
        if species is not None and cards.index("ATOMIC_POSITIONS") < cards.index("ATOMIC_SPECIES"):
            error("ATOMIC_POSITIONS before ATOMIC_SPECIES: pw.x stops.")
        if not positions.option:
            warning("ATOMIC_POSITIONS without units: pw.x takes alat (deprecated).")
        elif positions.option not in POSITIONS:
            error(f"ATOMIC_POSITIONS {positions.option}: not one of {', '.join(POSITIONS)}; "
                  "pw.x stops.")
        lines = positions.lines
        if isinstance(nat, int):
            if len(lines) < nat:
                error(f"ATOMIC_POSITIONS has {len(lines)} atoms for nat = {nat}: pw.x stops.")
            elif len(lines) > nat:
                warning(f"ATOMIC_POSITIONS has {len(lines)} lines for nat = {nat}: the extra ones "
                        "are ignored.")
                lines = lines[:nat]
        for line in lines:
            words = line.split()
            if len(words) not in (4, 7):
                error(f"ATOMIC_POSITIONS: {line!r} does not have 4 or 7 columns: pw.x stops.")
            elif labels and words[0] not in labels:
                error(f"ATOMIC_POSITIONS: species {words[0]} is not in ATOMIC_SPECIES: pw.x stops.")
    kcard = pw.cards.get("K_POINTS")
    if kcard is None:
        warning("no K_POINTS card: pw.x computes at Γ only, with gamma tricks.")
    else:
        option = kcard.option or "tpiba"
        if option not in K_POINTS:
            warning(f"K_POINTS {option}: not an option pw.x knows; it silently takes tpiba.")
        try:
            kpoints = pw.k_points()
        except PwInputError as e:
            error(f"{e}: pw.x stops.")
            kpoints = None
        if kpoints is not None and kpoints.kind == "automatic":
            if any(n <= 0 for n in kpoints.grid):
                error("K_POINTS automatic: grid sizes must be positive; pw.x stops.")
            if any(s not in (0, 1) for s in kpoints.shift):
                error("K_POINTS automatic: shifts must be 0 or 1; pw.x stops.")
        if str(occupations).startswith("tetrahedra") and option != "automatic":
            error("tetrahedra need an automatic k-point grid: pw.x stops.")
        if "ADDITIONAL_K_POINTS" in pw.cards and (
                cards.index("ADDITIONAL_K_POINTS") < cards.index("K_POINTS") or option == "automatic"):
            error("ADDITIONAL_K_POINTS must come after a K_POINTS list (not automatic): pw.x stops.")


# -- yambo ----------------------------------------------------------------------------

# Unit spellings yambo recognises, as substrings (output/OUTPUT_unit_conversion.F)
YAMBO_UNITS = ("RL", "rl", "mHa", "mha", "Ha", "ha", "mRy", "mry", "Ry", "ry", "meV", "mev", "eV",
               "ev", "K", "THz", "thz", "GHz", "ghz", "MHz", "mhz", "Gb", "Mb", "fs", "ps", "as")
# Columns of common blocks (yambo keeps the defaults for missing ones)
YAMBO_BLOCK_COLUMNS = {"BSEBands": 2, "GbndRnge": 2, "QPkrange": 4, "QPerange": 4,
                       "BSEEhEny": 2, "BEnRange": 2, "BDmRange": 2, "BSEQptR": 2, "BLongDir": 3,
                       "KfnQP_E": 3, "XfnQP_E": 3, "GfnQP_E": 3,
                       **{f"BndsRn{x}": 2 for x in ("Xx", "Xs", "Xd", "Xp", "Xm")},
                       **{f"EnRnge{x}": 2 for x in ("Xx", "Xs", "Xd", "Xp", "Xm")},
                       **{f"DmRnge{x}": 2 for x in ("Xx", "Xs", "Xd", "Xp", "Xm")},
                       **{f"LongDr{x}": 3 for x in ("Xx", "Xs", "Xd", "Xp", "Xm")}}
BSK_MODES = ("ip", "hartree", "hf", "alda", "sex", "bsfxc")
CUT_GEOMETRIES = ("box", "cylinder", "sphere", "ws", "slab")
_YAMBO4 = re.compile(r"^(X_all_q|X_finite_q|X_q_0)_", re.I)
_ARITHMETIC = re.compile(r"^(?=.*\d)[\d.eEdD+\-*/() ]+$")     # with a digit, not a lone "E"


def check_yambo_input(inp: YamboInput, version: tuple[int, int] | None = None,
                      where: str = "", executable: str = "yambo") -> list[Problem]:
    """Problems of a yambo (or ypp) input: what it would silently ignore or do
    differently. `version`: Yambo's (major, minor), None if unknown."""
    problems: list[Problem] = []

    def error(message):
        problems.append(Problem(message, where))

    def warning(message):
        problems.append(Problem(message, where, error=False))

    ypp = executable == "ypp"
    names = ypp_names(version) if ypp else yambo_names(version)
    program = "ypp" if ypp else "yambo"
    label = f"{'ypp' if ypp else 'Yambo'} {version[0]}.{version[1]}" if version else program
    canonical = lambda n: canonical_name(n, version, program)            # noqa: E731
    for name in inp.flags + inp.names():
        if canonical(name) is None and not _YAMBO4.match(name):
            warning(f"{name} is not read by {label}: ignored without a message.")
    for name in dict.fromkeys(inp.duplicates):
        warning(f"{name} is given more than once: {program} takes "
                + ("the first block." if inp.block(name) is not None else "the last value."))

    for name, variable in inp.variables.items():
        spelt = canonical(name)
        if isinstance(variable.value, str) and not variable.quoted \
                and not _ARITHMETIC.match(variable.value):
            error(f"{name}= {variable.value}{' ' + variable.unit if variable.unit else ''}: a "
                  "string without quotes; yambo keeps the default. Write "
                  f"{name}= \"{variable.value}{' ' + variable.unit if variable.unit else ''}\".")
            continue
        kind = names["units"].get(spelt) if spelt else None
        if variable.unit:
            if spelt and spelt != name:
                error(f"{name}: yambo finds the unit ({variable.unit}) only where the name is "
                      f"spelt {spelt}; here the value is read in its internal units.")
            elif kind and not any(u in variable.unit for u in YAMBO_UNITS):
                error(f"{name}: unit {variable.unit!r} is not one yambo knows (e.g. "
                      f"{'RL, Ry, Ha, eV' if kind == 'G_unit' else 'eV, meV, Ha, Ry'}); the value "
                      "is read in its internal units.")
        elif kind == "E_unit" and isinstance(variable.value, (int, float, complex)) and variable.value:
            warning(f"{name} has no unit: yambo reads it in Ha.")
        elif kind == "G_unit" and isinstance(variable.value, (int, float)) and variable.value:
            warning(f"{name} has no unit: yambo reads it as a number of G vectors (RL).")

    for name, block in inp.blocks.items():
        columns = None if ypp else YAMBO_BLOCK_COLUMNS.get(canonical(name) or name)
        if columns is None:
            continue
        for row in block.rows:
            if len(row) < columns:
                error(f"{name}: a row with {len(row)} of its {columns} columns; yambo keeps the "
                      "defaults for the missing ones.")
            elif len(row) > columns:
                warning(f"{name}: a row with {len(row)} columns; yambo reads the first {columns}.")

    if not ypp:
        _check_runlevels(inp, version, error, warning)
    return problems


def _check_runlevels(inp: YamboInput, version, error, warning):
    gw = [r for r in ("gw0", "ppa", "mpa", "cohsex") if inp.has_flag(r)]
    bse = [r for r in ("bse", "bss", "optics") if inp.has_flag(r)]
    if inp.has_flag("gw0") and bse:
        error(f"GW ({', '.join(gw)}) and BSE ({', '.join(bse)}) runlevels together: yambo drops "
              "the BSE ones and runs only the GW. Run them in separate inputs.")
    elif inp.has_flag("bse") and not (inp.has_flag("optics") or inp.has_flag("bss")):
        error("bse without optics or bss: yambo runs no BSE.")
    if inp.has_flag("bss"):
        _check_solver(inp, version, error, warning)
    lkind = inp.get("Lkind")
    if isinstance(lkind, str) and lkind:
        if version is not None and version < (5, 3):
            if lkind not in ("Lbar", "full"):
                error(f"Lkind = {lkind!r}: Yambo {version[0]}.{version[1]} takes Lbar or full and "
                      "stops otherwise.")
        elif not any(word in lkind.lower() for word in ("bar", "full", "default")):
            error(f"Lkind = {lkind!r}: yambo takes BAR or FULL (tilde stops it, despite its help).")
    bskmod = inp.get("BSKmod")
    if isinstance(bskmod, str) and bskmod and bskmod.lower() not in BSK_MODES:
        error(f"BSKmod = {bskmod!r} is not a kernel yambo knows (IP, Hartree, HF, ALDA, SEX, "
              "BSfxc): it computes an IP-like kernel without a message.")
    geometry = inp.get("CUTGeo")
    if isinstance(geometry, str) and geometry.strip() and geometry.strip().lower() != "none":
        _check_cutoff(inp, geometry, error)
    for prefix in ("X", "K", "G"):
        action = inp.get(f"{prefix}fnQPdb")
        if isinstance(action, str) and action.strip() and action.strip().lower() != "none":
            words = action.split()
            if "<" not in words:
                error(f"{prefix}fnQPdb = {action!r}: '<' must stand apart (\"E < path\"); yambo "
                      "applies no QP correction.")
            elif not any(w.upper() in ("E", "W", "Z") for w in words[:words.index("<")][-3:]):
                error(f"{prefix}fnQPdb = {action!r}: no action (E, W or Z) before '<'; yambo "
                      "applies nothing.")
    if version is not None and version >= (5, 4) and (inp.has_flag("HF_and_locXC") or inp.has_flag("gw0")):
        if (inp.variable("SE_CPU") or inp.variable("SE_ROLEs")) and not (
                inp.variable("SX_CPU") or inp.variable("SX_ROLEs")):
            warning("Yambo 5.4 parallelises the exchange (HF) part with SX_CPU and SX_ROLEs; "
                    "SE_* no longer applies to it.")


def _check_solver(inp: YamboInput, version, error, warning):
    letters = {"h": "Haydock", "d": "diagonalisation", "s": "SLEPc", "i": "inversion", "t": "TDDFT"}
    if version is None or version >= (5, 4):
        letters.update({"x": "heevx", "c": "ChASE"})
    mode = inp.get("BSSmod")
    if not isinstance(mode, str) or not mode.strip():
        error("bss without BSSmod: yambo builds the kernel and runs no solver.")
        return
    text = mode if version is not None and version < (5, 3) else mode.lower()
    found = [name for letter, name in letters.items() if letter in text]
    if not found:
        hint = (" (Yambo 5.1 reads only lowercase letters)" if version is not None
                and version < (5, 3) and any(c in mode.lower() for c in letters) else "")
        error(f"BSSmod = {mode!r} has none of yambo's solver letters ({''.join(letters)}){hint}: "
              "no solver runs.")
    elif len(found) > 1:
        warning(f"BSSmod = {mode!r}: yambo looks for each letter, so it runs "
                f"{' and '.join(found)}.")


def _check_cutoff(inp: YamboInput, geometry: str, error):
    words = geometry.split()
    kind = words[0]
    if kind not in CUT_GEOMETRIES:
        error(f"CUTGeo = {geometry!r}: yambo takes box, cylinder, sphere, ws or slab, in lowercase; "
              "the cutoff is off.")
        return
    direction = words[1] if len(words) > 1 else ""
    if kind != "sphere" and not re.search("[xyzXYZ]", direction):
        error(f"CUTGeo = {geometry!r}: no direction (x, y, z); the cutoff is off.")

    def zero(name):
        variable = inp.variable(name)
        block = inp.block(name)
        if block is not None:
            return all(not v for row in block.rows for v in row if isinstance(v, (int, float)))
        return variable is None or not variable.value

    if kind in ("sphere", "cylinder") and zero("CUTRadius"):
        error(f"CUTGeo {kind} without CUTRadius: the cutoff is off.")
    if kind == "box" and zero("CUTBox"):
        error("CUTGeo box without CUTBox: the cutoff is off.")
    if kind == "ws" and zero("CUTwsGvec"):
        error("CUTGeo ws without CUTwsGvec: the cutoff is off.")


def check_yambo_args(executable: str, args: str, version: tuple[int, int] | None = None,
                     where: str = "") -> list[Problem]:
    """Problems of the arguments given to yambo, ypp or p2y in a job:
    options that make it edit its input instead of running, print and exit,
    or that it does not have (it then exits with 1)."""
    import shlex
    problems = []
    try:
        words = shlex.split(args)
    except ValueError as e:
        return [Problem(f"the arguments cannot be read ({e})", where)]
    options = yambo_options(executable, version)
    i = 0
    while i < len(words):
        word = words[i]
        i += 1
        if not word.startswith("-") or word == "-":
            problems.append(Problem(f"{word!r}: not an option of {executable}, which stops "
                                    "(Use -h to list the options).", where))
            continue
        name = word.lstrip("-")
        match = [o for o in options if (len(name) == 1 and o["short"] == name) or o["long"] == name]
        if not match:                        # getopt_long_only: unique prefixes of long names
            match = [o for o in options if o["long"] and o["long"].startswith(name)]
            if len(match) > 1:
                problems.append(Problem(f"{word}: ambiguous for {executable} (" + ", ".join(
                    "-" + o["long"] for o in match) + "); it stops.", where))
                continue
        if not match:
            problems.append(Problem(f"{word}: {executable} has no such option and stops "
                                    "(Use -h to list the options).", where))
            continue
        option = match[0]
        if option["arg"]:
            i += 1                           # its value
        if option["kind"] == "editing":
            problems.append(Problem(f"{word}: a runlevel option; {executable} writes its input "
                                    "file and exits without computing. Switch runlevels on in the "
                                    "input instead.", where))
        elif option["kind"] == "exit":
            problems.append(Problem(f"{word}: {executable} prints and exits without computing.",
                                    where))
    return problems


# -- pseudopotentials -------------------------------------------------------------------

# Functionals written differently in UPF headers, compared as QE would see them
_FUNCTIONAL_ALIASES = {"pbe": "pbe", "sla pw pbx pbc": "pbe", "sla pw pbe pbe": "pbe",
                       "pbesol": "pbesol", "sla pw psx psc": "pbesol",
                       "pz": "pz", "lda": "pz", "sla pz nogx nogc": "pz", "sla-pz": "pz",
                       "sla pw nogx nogc": "pw", "sla-pw": "pw"}


def _functional(name: str) -> str:
    key = " ".join(name.lower().replace("-", " ").split())
    return _FUNCTIONAL_ALIASES.get(key, _FUNCTIONAL_ALIASES.get(name.lower(), key))


def check_pseudopotentials(pw: PwInput, files: list, headers: dict,
                           version: tuple[int, int] | None = None,
                           where: str = "", hints: dict | None = None) -> list[Problem]:
    """Problems of a pw.x input with its pseudopotentials. `files`: the
    PseudoFile of each species (pseudos.pseudo_files); `headers`: for a file's
    `remote` path, its UpfHeader, a message (unreadable) or None (missing);
    files without an entry were not read (on the cluster, checked offline).
    `hints`: {species: pseudodojo.Hints} for files whose source is known."""
    problems: list[Problem] = []

    def error(message):
        problems.append(Problem(message, where))

    def warning(message):
        problems.append(Problem(message, where, error=False))

    calculation = pw.get("control", "calculation", "scf")
    scf_type = calculation in SCF_TYPES
    known: dict[str, object] = {}
    for f in files:
        if len(f.name) > 80:
            error(f"{f.name}: pw.x reads file names of at most 80 characters; it stops.")
        if f.remote not in headers:
            continue
        header = headers[f.remote]
        if header is None:
            place = f.local if f.local is not None else f.remote
            if not scf_type:
                warning(f"{f.name} not found at {place}: fine only if the scf's prefix.save has "
                        f"it (pw.x looks there first in a {calculation} run).")
            elif f.default_dir:
                warning(f"{f.name} not found in ~/{f.remote.split('/', 1)[-1].rsplit('/', 1)[0]}, "
                        "pw.x's default folder: fine only if $ESPRESSO_PSEUDO on the cluster "
                        "points elsewhere. Set pseudo_dir, a project pseudos/ folder or the "
                        "profile's library.")
            else:
                error(f"{f.name} not found at {place}: pw.x stops.")
        elif isinstance(header, str):
            error(f"{f.name} {header}.")
        else:
            known[f.species] = header
            label = re.match(r"[A-Za-z]+", f.species)
            if label and not label.group().lower().startswith(header.element.strip().lower()):
                warning(f"{f.name} is a pseudopotential for {header.element}, used for species "
                        f"{f.species}.")
    ecutwfc = pw.get("system", "ecutwfc")
    if hints and isinstance(ecutwfc, (int, float)) and not isinstance(ecutwfc, bool):
        below = {species: h for species, h in hints.items()
                 if h.low is not None and ecutwfc < h.low}
        if below:
            normal = max((h.normal for h in hints.values() if h.normal is not None), default=None)
            warning(f"ecutwfc = {ecutwfc:g} Ry is below PseudoDojo's low hint for "
                    + ", ".join(f"{s} ({h.low:g} Ry)" for s, h in below.items())
                    + ": results will not be converged"
                    + (f" (normal: {normal:g} Ry)." if normal is not None else "."))

    complete = len(known) == len(files) and files
    if not complete:
        return problems

    headers_ = list(known.values())
    if pw.get("system", "ecutwfc") is None and not any(h.wfc_cutoff > 0 for h in headers_):
        error("no ecutwfc, and no pseudopotential suggests one: pw.x stops (ecutwfc not set).")

    if pw.get("system", "lspinorb", False) is True and not any(h.has_so for h in headers_):
        if all(h.has_so is not None for h in headers_):
            message = ("lspinorb with no spin–orbit (fully relativistic) pseudopotential")
            if version is not None and version < (7, 5):
                warning(message + ": pw.x 7.3 goes on without spin–orbit.")
            else:
                error(message + ": pw.x stops.")

    if not pw.get("system", "input_dft"):
        functionals = {_functional(h.functional) for h in headers_ if h.functional}
        if len(functionals) > 1:
            warning("the pseudopotentials have different functionals ("
                    + ", ".join(sorted({h.functional for h in headers_})) + "): pw.x stops "
                    "unless input_dft is set (inconsistent DFT read from PP files).")

    # nbnd against the occupied bands (setup.f90: checked in scf-type runs)
    nbnd = pw.get("system", "nbnd")
    positions = pw.cards.get("ATOMIC_POSITIONS")
    if isinstance(nbnd, int) and positions is not None:
        counts: dict[str, int] = {}
        for line in positions.lines:
            if line.split():
                counts[line.split()[0]] = counts.get(line.split()[0], 0) + 1
        if all(s in known for s in counts):
            charge = pw.get("system", "tot_charge", 0) or 0
            nelec = sum(known[s].z_valence * n for s, n in counts.items()) - charge
            noncolin = pw.get("system", "noncolin", False) is True
            occupied = round(nelec) if noncolin else round(nelec / 2)
            if nbnd < occupied:
                text = (f"nbnd = {nbnd}, below the {occupied} occupied bands ({nelec:g} valence "
                        "electrons)")
                if scf_type:
                    error(text + ": pw.x stops (too few bands).")
                else:
                    warning(text + ": pw.x does not check it here, but the bands are wrong.")
    return problems


def check_for_visual_excitons(inp: YamboInput, where: str = "") -> list[Problem]:
    """What Visual Excitons reads from a BSE run: ndb.BS_diago_Q* with the
    excitons' eigenvectors, so a diagonalising solver and WRbsWF (warnings:
    a BSE may be meant for something else)."""
    if not inp.has_flag("bss"):
        return []
    mode = str(inp.get("BSSmod", "")).lower()
    if mode and not any(letter in mode for letter in "ds"):
        return [Problem(f"BSSmod = {inp.get('BSSmod')!r} writes no ndb.BS_diago_Q*: Visual "
                        "Excitons needs a diagonalising solver (d, or s for SLEPc).", where,
                        error=False)]
    if not inp.has_flag("WRbsWF"):
        return [Problem("no WRbsWF: the excitons' eigenvectors are not written, and Visual "
                        "Excitons needs them.", where, error=False)]
    return []
