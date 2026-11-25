# Decisions and questions for the QE and Yambo experts

As of 2026-11-24.

Visual Workflows builds and runs the whole chain from a structure to
excitons (pw.x scf → nscf → p2y → GW → BSE) on a cluster, and opens the
result in Visual Excitons. The physics choices it makes were decided by its
developers from standard practice (Yambo's tutorials, the GW and BSE
literature), each with its reason below. Please correct any you disagree
with: a correction is quicker than a decision from scratch. A few questions
stay open at the end.

The [design](design.md) (Phase 5) tracks how each decision is built.

## The silicon test

We ran bulk Si through the chain on Lynx. It ran end to end, and we read the
results as right for an unconverged test.

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
the 6×6×6 grid overbinds (about 0.1 eV, against silicon's 15 meV) and gets
the peaks' heights wrong; with only 4 BSE bands nothing above about 4.5 eV
means anything. The peak heights are rebuilt from yambo's residuals with
0.1 eV Lorentzians, not taken from yambo's own spectrum. The window 3–6 also
splits degenerate states at Γ (band 2 with 3–4 at the valence top, band 7
with 5–6 at 2.55 eV): see decision 2.

## Decisions

| # | Decision | Reason |
| --- | --- | --- |
| 1 | With spin–orbit (spinor bands), the default band window counts twice the bands: 4 below and 4 above the gap. | A spinor band holds one electron, not two: the same states take twice the band numbers. |
| 2 | The default window (2 below, 2 above) grows to whole degenerate sets at the gap's edges: at the k-points of the valence top, the conduction bottom and the lowest direct transition, every band level with the top valence band and with the bottom conduction band. Done once the SAVE exists (`vwf project bands --fit`), as the chain makes its inputs before. Degeneracies away from the gap are listed, not followed. | A window keeping part of a degenerate set breaks the crystal's symmetry in the BSE (spurious splittings). Si: 3–6 becomes 2–7. Measured on Lynx (BSEBands 3:6, 2:7, 2:8): energies within 7 meV and the spectrum unchanged, but with 3:6 the lowest excitons form a group of four spread over 7 meV, which Si's symmetry forbids; with 2:7, clean groups of three and two. |
| 3 | GW: one-shot G₀W₀, Godby–Needs plasmon pole (PPAPntXp 27.21 eV, yambo's), Newton solver. | The standard first GW for sp semiconductors and insulators (Yambo's tutorials). Known to fall short with localised d electrons (ZnO, transition-metal oxides): multipole (MPA) or full frequency, and evGW, are planned as options. |
| 4 | BSE: its own static screening, SEX kernel (exchange and screened direct term), Tamm–Dancoff, full diagonalisation with eigenvectors kept. | Standard for semiconductors. Diagonalisation gives each exciton's wave function, which Visual Excitons needs (Haydock gives only the spectrum). Tamm–Dancoff can shift strongly bound excitons in small or 2D systems slightly. |
| 5 | Above about 20 000 transitions, the program warns and suggests SLEPc (lowest excitons, with eigenvectors), without switching by itself. | Diagonalisation's cost grows as the size cubed and its memory as the size squared. |
| 6 | For 2D materials (vacuum found along an axis): `rim_cut` with `CUTGeo = "slab <axis>"`, `RandQpts = 1000000`, `RandGvec = 100`, with a note that they are to converge. The axis is cartesian, as Yambo's slab cutoff is: a layer tilted in its cell gets no cutoff, with a note to turn it. | Without a cutoff a layer's electrons and holes feel its copies across the vacuum. The values are those of Yambo's 2D tutorials (hBN, MoS₂). |
| 7 | Convergence: GW in Yambo's tutorials' order (exchange cutoff; screening cutoff and its bands together; self-energy bands), the BSE's k-grid first, then BSENGexx and its bands. Tolerances: 0.05 eV on gaps and exciton energies, 0.1 eV on peak positions. | Common targets; studies of two variables together (screening cutoff with its bands) are planned. On Si (6×6×6), the BSE's screening cutoff (NGsBlkXs 1–5 Ry) moved the lowest bright exciton by 5 meV: converged from 1 Ry. |
| 8 | A scissor can be taken from a GW: the shift and stretching fitted to its `ndb.QP`, on the BSE's bands, with the fit's errors and the gaps it gives against the GW's. | The scissor stands in for GW; its sensible value is the GW's own correction (the fit yambo makes for bands a QP database lacks). On Si (bands 2–7): 0.464 eV, errors 0.06 eV typical; the lowest direct transition within 0.06 eV of GW's, the indirect gap 0.2 eV above it. |
| 9 | The Bruneval–Gonze terminator is offered (`GTermKind`), off by default until a study of the self-energy bands shows what it saves on Si. | It lets GW converge with far fewer empty bands, at little risk. |
| 10 | DFT defaults kept: PseudoDojo standard tables (semicore states included for most elements), the "normal" cutoff hint, ecutrho 4 × ecutwfc, the nscf with 4 × the occupied bands (at least 20 more) on the scf's grid, unshifted. | Adequate starting points; the cutoff matters less for GW than the bands and the screening. |
| 11 | The BSE at every Q (exciton dispersion) is offered, off by default. | Not needed for absorption, but Visual Excitons shows dispersions; it costs about nk_ibz BSEs. |
| 12 | No ypp inputs. | Visual Excitons shows the excitons' amplitudes from the databases. |
| 13 | Not supported yet: spin-polarised systems (nspin = 2) and metals. | Rare for excitons; added when someone needs them. |
| 14 | The next test after Si: a 2D material (hBN or MoS₂), with decision 6. | Yambo's tutorials give reference values for both. |

## Open questions

- [ ] Does our reading of the silicon test hold, or does anything point to a
  wrong setup rather than poor convergence?
- [ ] Which materials does your group study? If d-electron materials
  (transition-metal oxides, ZnO, perovskites) are among them, the multipole
  (MPA) or full-frequency GW option comes next (decision 3).
- [ ] Is the exciton dispersion (decision 11) something your group would
  use?
- [ ] For your systems, what accuracy do your papers need: would "converged
  enough" presets per kind of system (bulk semiconductor, 2D layer) help, and
  with which values?
- [ ] Any decision above you would change?
