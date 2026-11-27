# Tutorial 1: silicon

[← Tutorials](README.md)

<!-- DRAFT. Numbers marked "earlier run" come from the Si-bse project
(6×6×6, 24 bands); they are replaced by the tutorial run's own. -->

Silicon is the standard first test for GW and BSE: a simple crystal, well
measured, and cheap to compute. In this tutorial you take it from its
crystal structure to its excitons:

1. a DFT calculation with Quantum ESPRESSO (pw.x), which gives the bands;
2. a GW calculation with Yambo, which corrects the band energies;
3. a BSE calculation with Yambo, which gives the excitons and the absorption
   spectrum;
4. convergence studies, which tell how far the numbers can be trusted;
5. the excitons' dispersion, which shows the lowest exciton of all.

You will learn to make a project, set up a chain of steps, submit them and
follow their jobs, download and read the results, and run convergence
studies. Plan about an hour for parts 1–5 (mostly waiting in the queue),
and a few hours more for parts 6 and 7.

## 1. The question

Silicon's electrons fill a set of bands (the valence bands) up to an energy
gap; above the gap lie the empty conduction bands. The gap decides how
silicon absorbs light. Experiment gives:

| | Experiment |
|---|---|
| Band gap (indirect: from Γ to near X) | 1.17 eV |
| Lowest direct gap (at Γ) | 3.4 eV |
| Absorption peaks E₁ and E₂ | 3.4 eV and 4.25 eV |

*(Gaps at low temperature; the peaks from ellipsometry, Lautenschlager et
al., Phys. Rev. B 36, 4821 (1987).)*

The gap is **indirect**: the top of the valence bands is at the centre of
the Brillouin zone (Γ), the bottom of the conduction bands is elsewhere
(85 % of the way to the point X). Light alone, which carries almost no
momentum, cannot make that jump; it needs a phonon's help. So silicon
absorbs weakly up to about 3.4 eV, where direct transitions start.

The aim is to compute these numbers from first principles, and see where
each method stands.

## 2. The project and the structure

### The structure file

Save this as `Si.vasp` (a POSCAR file: silicon's face-centred cubic cell,
a = 5.431 Å, with its two atoms):

```text
Si, diamond structure
5.431
0.0 0.5 0.5
0.5 0.0 0.5
0.5 0.5 0.0
Si
2
Direct
0.00 0.00 0.00
0.25 0.25 0.25
```

Any CIF from a database (the Materials Project, COD) does as well.

### A new project

**In the window.** Select your cluster in the toolbar. In the **Project**
tab, **New project…**: name it `Si-tutorial`, choose where its folder goes
on this computer, and **Create**. Its folder on the cluster is shown before
you create it.

**On the command line.**

```bash
vwf project new Si-tutorial --cluster Lynx
cd Si-tutorial
```

A project is a folder with one sub-folder per step, mirrored on the cluster
([Projects](../projects.md)).

### The scf input

**In the window.** **Add ▾ › QE chain to excitons…** opens a window with
five sections, listed on the left. In **1. scf: the structure**, press
**Make the scf input…**. In the pw.x editor:

1. **Structure › Import structure…**: choose `Si.vasp`. The editor shows
   space group Fd-3m (227), 48 symmetry operations, and writes the cell as
   `ibrav = 2` with `celldm(1) = 10.263`.
2. **Pseudopotentials › PseudoDojo…**: PBE, scalar relativistic, standard.
   This copies `Si.nc-sr-04_pbe_standard.upf` into the project's `pseudos/`.
   Then **Use suggested cutoffs**: `ecutwfc = 36`, `ecutrho = 144` Ry.
3. **k-points › Grid from spacing** with 0.2 Å⁻¹: an 11 × 11 × 11 grid
   (pw.x computes 56 k-points).
4. **Settings**: set `prefix` to `si`; leave the rest.

**OK** brings the input back to the chain's window.

**On the command line.**

```bash
vwf pseudos get --from ../Si.vasp
vwf structure ../Si.vasp --input --prefix si > scf.in
```

The first command fetches the pseudopotential; the second prints the
structure's symmetry and writes the start of an scf input, with the
pseudopotential and its suggested cutoffs.

> **The physics: DFT, plane waves, pseudopotentials, k-points.**
> Density functional theory (DFT) replaces the many interacting electrons by
> independent electrons moving in an average potential, the Kohn–Sham
> potential, chosen so that their density is the true one. Its orbitals and
> their energies give the bands. pw.x expands the orbitals in plane waves,
> up to a kinetic energy cutoff (`ecutwfc`; the density needs 4 × that,
> `ecutrho`). The tightly bound core electrons are replaced by a
> pseudopotential, which leaves only the 4 valence electrons of each
> silicon atom. In a crystal, each orbital has a wave vector **k** in the
> Brillouin zone; the scf ("self-consistent field") calculation finds the
> density by sampling the zone on a grid of k-points. The scf grid only
> needs to give a good density: 11 × 11 × 11 is generous for silicon.
>
> Read more: R. M. Martin, *Electronic Structure* (Cambridge, 2004),
> chapters 6–7 and 12; the
> [Quantum ESPRESSO tutorials](https://www.quantum-espresso.org/tutorials/).

## 3. The chain

The other sections of **QE chain to excitons** make the steps that follow
the scf. Each section is a page; **Next ›** goes through them, and the
summary of the steps to be made stays in view.

**2. nscf and p2y: Yambo's SAVE.** Tick **nscf**, with **Grid** 6 × 6 × 6
and **Bands (nbnd)** 100. Keep **Then p2y** ticked. The line below the
bands reads "4 occupied + 96 empty".

> **The physics: the nscf and Yambo's k-grid.** Yambo needs the bands on a
> grid of k-points, and many empty bands besides the occupied ones (the GW
> sums over them). The nscf ("non-self-consistent") calculation computes
> them in the scf's density, without changing it. Its grid is not shifted
> (it contains Γ), so that differences of k-points, the momenta q that
> Yambo uses, fall on the same grid. A 6 × 6 × 6 grid has 216 k-points, 16
> of them different by symmetry: coarse, but enough to learn with; part 6
> shows how much the results move with it. Silicon has 4 occupied bands;
> the program suggests 24 bands, enough for a first GW, but the GW's
> convergence study in part 6 needs more, hence 100.
>
> p2y converts QE's output into Yambo's database, the `SAVE` folder.
> Silicon's crystal has symmetries with fractional translations, which
> Yambo does not handle, so the nscf gets `force_symmorphic = .true.` (the
> program adds it): pw.x then uses only the other symmetries.

**3. Band structure.** Tick it and leave **Path** empty: the line below it
shows the lattice's standard path, `G X W K G L U W L K U X` for silicon's
fcc lattice (G stands for Γ), with 149 k-points at the default density.
Keep **Bands (nbnd)** at 12: "4 occupied + 8 empty".

This step computes the DFT bands along straight lines between the
Brillouin zone's special points, for a plot of the band structure; nothing
after it needs them. It runs after p2y, since it overwrites the nscf's
files.

> **The band structure's settings.** The **path** is a list of special
> points, the zone's corners and face centres, named by convention: Γ the
> centre, X the centre of a square face, L of a hexagonal face, and so on.
> Typing `GXL` gives a shorter path; a comma starts a new section. The
> **density** sets how many k-points go on each line. **nbnd** is how many
> bands pw.x computes at each k-point: the 4 occupied ones and some empty
> ones above them. pw.x's own default for an insulator is the occupied
> bands only, which would show no conduction bands at all; 12 shows the
> lowest 8 conduction bands, several eV above the gap, which is all this
> tutorial looks at. Raise it only to see higher bands; it costs little.

**4. GW and BSE.** Tick **Yambo, after p2y**, then **GW (plasmon pole): QP
energies**, and a **BSE** on the GW's energies. Set the bands to 2 to 7:
the three highest valence bands and the three lowest conduction bands.

> **Why 2 to 7, and not the default 3 to 6.** At Γ, silicon's three highest
> valence bands have the same energy (they are degenerate, by the
> crystal's symmetry), and so do its three lowest conduction bands. A
> window that takes only some states of a degenerate set breaks that
> symmetry, and the excitons built from it are wrong. The program cannot
> know the degeneracies before the nscf has run, so it suggests 2 below and
> 2 above the gap; once the SAVE is downloaded, **Bands** (`vwf project
> bands --fit`) names the windows that keep the sets whole, and warns
> about steps that split them.

> **The physics: GW.** DFT's band gaps are too small: for silicon about
> 0.6 eV instead of 1.17 eV. The Kohn–Sham energies are not meant to be the
> energies of adding or removing an electron, which is what a band gap
> measures. GW computes those: an added electron (or hole) repels the other
> electrons, which move away and **screen** it. The electron with its
> screening cloud is a **quasiparticle**, and its energy differs from the
> Kohn–Sham one by the self-energy Σ = iGW, the product of the electron's
> propagator G and the screened interaction W. In a one-shot G₀W₀
> calculation, the correction is computed once, from DFT's orbitals:
> E_QP = E_KS + Z ⟨Σ(E) − V_xc⟩. The **plasmon-pole** approximation models
> the screening's dependence on frequency with a single pole, which is
> cheap and good for sp semiconductors like silicon. Z, the
> quasiparticle's weight, is between 0 and 1 (0.7–0.8 for silicon).
>
> Read more: F. Aryasetiawan and O. Gunnarsson, Rep. Prog. Phys. 61, 237
> (1998); Yambo's [GW tutorials](https://wiki.yambo-code.eu/wiki/index.php?title=Tutorials).

> **The physics: the BSE and excitons.** When light lifts an electron
> into a conduction band, it leaves a hole behind in a valence band. The
> two attract each other, and can form a bound pair: an **exciton**, with an
> energy below the gap by its **binding energy**. Even unbound, their
> attraction mixes the transitions and moves the absorption's weight to
> lower energies. The Bethe–Salpeter equation (BSE) describes the pair: its
> kernel holds the screened attraction (W again, but static) and an
> exchange term. Its eigenvalues are the excitons' energies, and its
> eigenvectors tell which transitions each exciton is made of, and how
> strongly it absorbs light (**bright**) or not (**dark**). In silicon the
> binding is weak (tens of meV for the lowest excitons), but the effect on
> the spectrum is large: without it, the E₁ peak at 3.4 eV is much too
> weak.
>
> Read more: G. Onida, L. Reining and A. Rubio, Rev. Mod. Phys. 74, 601
> (2002); Yambo's [BSE tutorials](https://wiki.yambo-code.eu/wiki/index.php?title=Tutorials).

**5. Resources.** Partition `magma`, 1 node, 8 MPI tasks per node, memory
16G, time 1:00:00. For the GW and BSE steps: 1 MPI task per node, 16
threads per task (see [On Lynx](README.md#on-lynx-yambo-on-one-mpi-task)).

**Add steps** makes six steps, in order:

| Step | What it does |
|---|---|
| `01-scf` | pw.x: the density |
| `02-nscf` | pw.x: 100 bands on the 6 × 6 × 6 grid |
| `03-p2y` | p2y: Yambo's SAVE; then yambo's setup |
| `04-gw` | yambo: GW corrections for bands 2–7 |
| `05-bse` | yambo: the BSE on the GW's energies |
| `06-bands` | pw.x: bands along the path |

**On the command line**, the same in one command:

```bash
vwf project add-qe-chain scf.in --nscf-grid 6 6 6 --nscf-nbnd 100 \
    --gw --bse gw --yambo-bands 2 7 --partition magma --tasks-per-node 8 \
    --mem 16G --time 1:00:00 --yambo-tasks-per-node 1 --yambo-cpus-per-task 16
```

The steps are checked as they are made: no errors and no warnings expected.

**Look at what was made.** Select a step: the panel on the right shows its
inputs (**Input**) and its job script (**Script**). In `04-gw`'s `gw.in`,
for example, `NGsBlkXp = 3 Ry` and `BndsRnXp` and `GbndRnge` are the
settings that part 6 converges. On the command line, `vwf project script
04-gw` shows the script.

## 4. Submitting and following the jobs

**In the window.** **Check all** runs every check again, this time with the
cluster: the modules load, the pseudopotentials are there. Then **Submit
all**: each step is uploaded and submitted, each job waiting for the ones
before it (Slurm's dependencies). Tick **every 60 s** to follow them: the
**State** column turns from waiting to running and to completed. Select a
running step to follow its **Output** live. The **Jobs** tab shows the same
jobs as Slurm sees them.

**On the command line.**

```bash
vwf project check --on-cluster
vwf project submit
vwf project watch           # until all have ended; Ctrl+C stops watching, not the jobs
vwf project status
vwf project log 04-gw       # the end of a step's output
```

<!-- TO FILL: how long each step took on Lynx. -->

**If a step fails**, its job ends red. **Diagnose all**, which then appears in
the top bar (or **Diagnose** with the step selected; `vwf project diagnose`) reads its logs
and outputs and says what it recognises, with what to do. The
steps after it are not run: Slurm cancels them (`DependencyNeverSatisfied`).
Fix the cause, then **Submit all** again: the completed steps are not run
again. See [Troubleshooting](../troubleshooting.md).

## 5. The results

**In the window.** **Get results** downloads every step's results into its
`outputs/` folder, without the wave functions (which are large and not
needed here). Then **Results** shows what they hold.

**On the command line.**

```bash
vwf project get
vwf project results
```

### The DFT bands

Select `03-p2y` and **Bands** (`vwf project bands 03-p2y`): the SAVE's
bands, its gaps and the density of states.

<!-- TO FILL: the tutorial run's numbers, and a plot of 06-bands. -->

The DFT gap on the grid, earlier run: **0.70 eV** from Γ (k1) to X (k13:
the k-points' coordinates are in yambo's report),
and the lowest direct gap 2.55 eV at Γ. Two things lower the first number
in a better calculation: the true conduction minimum lies between Γ and X,
not on the 6 × 6 × 6 grid, and PBE's gap for silicon is about 0.6 eV.
Either way, half of experiment's 1.17 eV: the known underestimate of DFT.

### GW

`vwf project results` (or **Results**) for `04-gw`, earlier run:

```text
GW (PPA, Newton solver): bands 2–8 at 16 k-points; Z 0.66–0.80
  band 4: +0.08 eV (-0.07 to +0.22)
  band 5: +0.62 eV (+0.48 to +0.76)
  gap 0.70 → 0.97 eV; lowest direct transition 2.55 → 3.03 eV
```

The valence bands hardly move; the conduction bands go up by 0.6–0.8 eV,
so the gap opens: 0.97 eV, closer to the 1.17 eV of experiment. What is
left of the difference is mostly convergence (part 6): the screening's
size and bands are small here.

### The excitons and the spectrum

For `05-bse`, earlier run:

```text
BSE (Q = 0, bands 2–7 on QP energies): 1944 excitons, 1932 weak or dark
  lowest 2.92 eV, lowest bright 2.92 eV
  0.11 eV below the lowest direct transition (3.03 eV)
  absorption peaks: 3.12 eV (1.00), 3.51 eV (0.27), 3.91 eV (0.95), ...
```

<!-- TO FILL: a plot of the absorption against experiment's. -->

1944 excitons: one for each transition between the 3 valence and 3
conduction bands at each of the 216 k-points. Most absorb almost nothing.
The spectrum has its peaks at 3.1 and 3.9 eV, below experiment's 3.4 and
4.25 eV: the GW energies are still too low, and 6 × 6 × 6 k-points are far
too few for a smooth spectrum, which part 6 shows.

### In Visual Excitons

**Visual Excitons** (with `05-bse` selected) opens the step's SAVE, BSE and
GW databases in Visual Excitons, which shows each exciton: its energy and
brightness, and the electron and hole it is made of in the bands and the
Brillouin zone. **Get results ▾ › For Visual Excitons** first, which also
downloads the files it reads. On the command line: `vwf project excitons
05-bse`.

## 6. Convergence

Every number above depends on settings that a finite calculation must cut
off: how many k-points, how many bands, how many plane waves in the
screening. A result is trustworthy when raising each setting no longer
changes it, within a tolerance: here 0.05 eV for gaps and exciton
energies, 0.1 eV for peak positions.

Each setting is varied on its own, the others fixed, in an order where the
earlier ones do not depend on the later ones. **Studies** shows that order;
**Study…** on a GW or BSE step proposes the next variable in it. A study
copies the step once per value, with that one variable changed, and shows
the quantities read back against the values, with the tolerance shaded.

### GW

<!-- TO FILL: the values to study and the results. -->

1. **EXXRLvcs**, the exchange's G-vectors: cheap, first.
2. **NGsBlkXp**, the screening's size (1, 2, 3, 4, 5 Ry), with its bands
   **BndsRnXp** (20, 40, 60, 80, 100): raise one, then check the other.
3. **GbndRnge**, the self-energy's bands (20 to 100): the slowest to
   converge.

**In the window.** Select `04-gw`, **Study…**, choose the variable and type
the values; **Submit all**, then **Get results** and **Studies**.

**On the command line.**

```bash
vwf project add-study 04-gw NGsBlkXp 1 2 3 4 5
vwf project submit
vwf project get
vwf project studies gw-NGsBlkXp
```

### The BSE's k-grid

The k-grid matters most for the BSE, and changing it means a new nscf, so
it is not a study: it is a new chain. Repeating the GW on each grid would
be slow; a **scissor** fitted to the 6 × 6 × 6 GW stands in for it, the
conduction bands shifted and both sides stretched to match the GW.

**In the window.** **Add ▾ › QE chain to excitons…** again, with the same
scf input, **Grid** 8 × 8 × 8, no band structure, no GW, and a BSE with a
**Scissor › From a GW…**: choose `04-gw/outputs/gw/ndb.QP`. Then the same
with 10 × 10 × 10 and 12 × 12 × 12.

**On the command line.**

```bash
vwf project add-qe-chain scf.in --nscf-grid 8 8 8 --nscf-nbnd 24 --no-bands \
    --bse scissor --scissor-from 04-gw/outputs/gw/ndb.QP --yambo-bands 2 7 --partition magma \
    --tasks-per-node 8 --mem 16G --time 1:00:00 \
    --yambo-tasks-per-node 1 --yambo-cpus-per-task 16
```

<!-- TO FILL: the spectra on each grid in one plot. -->

### The BSE's other settings

**BSENGexx** (the exchange's G-vectors), **NGsBlkXs** (the screening's
size), **BSEBands** (the bands): studies on `05-bse`, as for the GW. In the
earlier run, NGsBlkXs from 1 to 5 Ry moved the lowest bright exciton by
5 meV: converged from 1 Ry.

## 7. The excitons' dispersion

The BSE above finds the excitons with no total momentum (Q = 0), the only
ones light makes by itself. Excitons can also carry momentum: an electron
at k + Q bound to a hole at k. Since silicon's gap is indirect, its lowest
exciton has the Q that joins the valence top to the conduction bottom.

**In the window.** Select `05-bse` and **Edit…**, then **Edit input…** on
its run: set `BSEQptR` to `1 | 16` (every irreducible Q of the grid: on an
unshifted grid, the Q-points are the k-points). Save, and **Submit again**.
The kernel of Q = 0 is kept in the step's folder and reused; each other Q
is a BSE of its own, so the step takes about 16 times as long as before.

**On the command line**, change the block in `05-bse/bse.in` by hand:

```text
% BSEQptR
 1 | 16 |
%
```

then `vwf project submit 05-bse`, and `vwf project get 05-bse` once it has
ended. `vwf project results 05-bse` lists the lowest exciton at each Q.

Earlier run: the lowest exciton at Q13 (the X point), **0.86 eV**, bound by
about 0.11 eV below the GW's indirect gap on the grid, the same binding as
at Q = 0. Visual Excitons shows the dispersion.

<!-- TO FILL: a plot of the lowest exciton against Q. -->

## 8. Against experiment

<!-- TO FILL: the converged numbers. -->

| | DFT (PBE) | GW | BSE | Experiment |
|---|---|---|---|---|
| Indirect gap | | | | 1.17 eV |
| Direct gap at Γ | | | | 3.4 eV |
| E₁, E₂ peaks | | | | 3.4, 4.25 eV |

## 9. All the commands

```bash
vwf project new Si-tutorial --cluster Lynx
cd Si-tutorial
vwf pseudos get --from ../Si.vasp
vwf structure ../Si.vasp --input --prefix si > scf.in
vwf project add-qe-chain scf.in --nscf-grid 6 6 6 --nscf-nbnd 100 \
    --gw --bse gw --yambo-bands 2 7 --partition magma --tasks-per-node 8 \
    --mem 16G --time 1:00:00 --yambo-tasks-per-node 1 --yambo-cpus-per-task 16
vwf project check --on-cluster
vwf project submit
vwf project watch
vwf project get
vwf project results
```

Next: [the hBN monolayer](README.md), a layer in vacuum.
