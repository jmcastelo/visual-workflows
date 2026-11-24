# Questions for the QE and Yambo experts

As of 2026-11-24.

Visual Workflows builds and runs the whole chain from a structure to
excitons (pw.x scf → nscf → p2y → GW → BSE) on a cluster, and opens the
result in Visual Excitons. Every physics value it writes is a placeholder,
chosen cautiously. We need your judgement on which defaults to change, what
to offer, and how to check results.

Answers go into the [design](design.md) (its "Later" list holds the same
open items) and then into the program's defaults.

## 1. The silicon test

We ran bulk Si through the chain on Lynx with the program's defaults. It
ran end to end, and we read the results as right for an unconverged test.
Is that reading sound?

Setup: PBE (PseudoDojo standard, norm-conserving), ecutwfc 36 Ry, ecutrho
144 Ry, a = 10.26 bohr; scf and nscf on an unshifted 6×6×6 grid (16
irreducible k-points), the nscf with 24 bands; yambo 5.2.4; GW with a
plasmon pole and a BSE on bands 3–6.

| Quantity | This run | Measured |
| --- | --- | --- |
| Indirect gap on the grid, DFT → GW | 0.70 → 0.97 eV | 1.17 eV |
| GW shift, valence bands 3–4 | +0.04 to +0.08 eV | |
| GW shift, conduction bands 5–6 | +0.62 to +0.68 eV | |
| Lowest direct transition, DFT → GW (Γ) | 2.55 → 3.03 eV | |
| Lowest exciton | 2.92 eV (a dark group) | |
| E₁ peak | 3.12 eV, the stronger | ≈ 3.4 eV |
| E₂ peak | 3.90 eV, 0.89 of E₁ | ≈ 4.25 eV, the stronger |

Our reading: the GW gap, short by about 0.2 eV, shifts every exciton down;
the 6×6×6 grid overbinds (about 0.1 eV, against silicon's 15 meV) and swaps
the heights of E₁ and E₂; with only 4 BSE bands nothing above about 4.5 eV
means anything. The peak heights are rebuilt from yambo's residuals with
0.1 eV Lorentzians, not taken from yambo's own spectrum.

- [ ] Does this reading hold, or does anything point to a wrong setup
  rather than poor convergence?
- [ ] What would you check first in any new result, so that the program can
  show it automatically (gap, QP shifts, lowest bright exciton, peaks)?
- [ ] Is silicon the right test for the program, or should it be another
  system (a 2D material, for instance)?

## 2. Defaults the program writes

Each value is written with a note saying it must be converged; most are
YamboPy tutorial values or yambo's own defaults. Should any default change,
and should the program refuse or warn below some value?

| Step | Variable | Default now | Why we chose it |
| --- | --- | --- | --- |
| scf | ecutwfc, ecutrho | PseudoDojo's "normal" hint of the hardest species; ecutrho = 4 × ecutwfc | Norm-conserving tables; a warning below the "low" hint |
| scf | pseudopotentials | PseudoDojo PBE or PBEsol, standard (stringent on request), scalar or fully relativistic | One consistent, citable set |
| nscf | k-grid | The scf's grid, unshifted (Γ-centred) | p2y and yambo need an unshifted grid with full symmetry |
| nscf | nbnd | 4 × the occupied bands, at least occupied + 20 | Room for the GW sums and the BSE |
| nscf | force_symmorphic | Set when pw.x would use fractional translations | p2y needs symmorphic operations |
| GW | BndsRnXp, GbndRnge | All the nscf's bands | Yambo's default; raised through nbnd |
| GW | NGsBlkXp | 3 Ry | Yambo's default is 1 G-vector, silently unconverged |
| GW | EXXRLvcs, VXCRLvcs | All the SAVE's G-vectors | Yambo's default, exact |
| GW | QPkrange | All irreducible k-points, 2 bands below and 2 above the gap | Covers the BSE's bands |
| BSE | BSEBands | 2 below and 2 above the gap | The lowest excitons only |
| BSE | BSENGexx | 10 Ry | Tutorial value |
| BSE | BSENGBlk | All of the screening (-1) | Yambo's default |
| BSE | BndsRnXs, NGsBlkXs | All the nscf's bands, 3 Ry | As GW's screening |
| BSE | BEnRange, BDmRange | 0–10 eV, 0.1 eV broadening | Yambo's defaults |

- [ ] Which of these would you change as a default, and to what?
- [ ] Should defaults scale with the system (bands per atom, a k-spacing in
  Å⁻¹) rather than be fixed numbers?
- [ ] Is 2 + 2 bands around the gap a sensible start, or should the window
  be set by energy (transitions up to some eV)?
- [ ] Would stringent pseudopotentials or a higher cutoff level be a better
  default for GW?

## 3. Convergence studies

We plan to let a user scan one parameter at a time: one step per value, each
result read back and plotted against the parameter. The mechanism is
generic; what we need from you is which scans matter and what to watch.

- [ ] Which parameters, in which order? Our guess: k-grid, nscf bands
  (BndsRnXp, GbndRnge), screening cutoff (NGsBlkXp, NGsBlkXs), BSENGexx,
  BSE bands.
- [ ] Which result to watch for each: the GW gap at Γ, the indirect gap, the
  first bright exciton, the E₁ peak?
- [ ] What tolerance counts as converged (0.05 eV on a gap, 0.1 eV on a
  peak)?
- [ ] Are some parameters coupled, so that they must be scanned together
  (bands and screening cutoff in GW, for instance)?
- [ ] For the k-grid: dense Γ-centred grids, shifted grids, or yambo's
  double grid? Which do you use for the BSE in practice?
- [ ] Should the program offer a "converged enough" preset per kind of
  system (bulk semiconductor, 2D layer), and who would set its values?

## 4. Method choices in GW and BSE

The chain makes a one-shot G₀W₀ with a plasmon pole, then a Tamm–Dancoff BSE
solved by full diagonalisation. Are these the right defaults, and which
alternatives should a user be able to pick?

| Choice | Now | Alternatives we could offer |
| --- | --- | --- |
| Screening in GW | Godby–Needs plasmon pole, PPAPntXp 27.21 eV (yambo's) | Full frequency, multipole (MPA) |
| Self-consistency | One-shot G₀W₀, Newton solver (DysSolver n) | Eigenvalue self-consistent (evGW) |
| Band terminator | None (GTermKind none) | Bruneval–Gonze, to need fewer bands |
| BSE kernel | SEX (exchange and screened direct term) | Hartree only (RPA with local fields) |
| BSE coupling | Resonant only (Tamm–Dancoff) | Full, with coupling |
| BSE solver | Full diagonalisation (BSSmod d), eigenvectors kept | Haydock (spectrum only), SLEPc (the lowest excitons) |
| BSE screening | Its own static screening (em1s) | Reusing GW's (not done: it would take the plasmon-pole one) |
| QP energies in the BSE | GW's ndb.QP, on the GW's bands | A scissor on DFT energies (the user gives the shift) |

- [ ] Do these defaults suit excitons in your systems?
- [ ] When the BSE matrix grows (many k-points), should the program switch
  from diagonalisation to SLEPc or Haydock by itself, and from what size?
- [ ] With the scissor: how should the shift be chosen (a GW gap,
  experiment, a known value)? Should stretching factors be offered too?
- [ ] Is a BSE computing its own static screening the right way, or would
  you reuse a screening database?

## 5. Features to add, or not

These are on our list but none is built. Is each worth it, and what should
its defaults be?

| Feature | What it would do | Cost or catch |
| --- | --- | --- |
| BSE at every Q | BSEQptR over all irreducible q-points, for a real exciton dispersion (today only Q = 0, so Visual Excitons draws flat lines) | About nk_ibz times the BSE's cost (16× for our Si) |
| Coulomb cutoff for layers | rim_cut with CUTGeo "slab z" in GW and BSE, when the program finds vacuum along an axis | The cutoff kind and the RIM sampling (RandQpts, RandGvec) to choose |
| Spin–orbit (noncollinear) | The chain with fully relativistic pseudopotentials; the band window counted in spinor bands | Is "2 below, 2 above" still right, or 4 + 4? |
| Spin-polarised (nspin = 2) | Occupied bands per spin channel | Not supported today: the user gives the bands |
| Metals | Smearing in the scf, and a band window | Not supported today: the occupied bands are not known |
| ypp inputs | Exciton amplitudes and wave functions in real space, bands, DOS | Visual Excitons already shows amplitudes from the databases |

- [ ] Which of these do you need first, for the systems you study?
- [ ] For 2D materials: the cutoff kind and RIM settings you use, and the
  vacuum you consider enough?
- [ ] For spin–orbit: does the BSE need anything beyond spinor bands (a
  different band window or kernel)?
- [ ] Is there any ypp output you would still want from the program, given
  Visual Excitons?
- [ ] Anything missing from this list that you would expect such a program
  to do?
