# Quantum ESPRESSO for the suite

[← Documentation](../README.md#documentation)

What the Visual suite needs to know about Quantum ESPRESSO (QE) to write job scripts, check parallelisation, build inputs and read results: running pw.x, its parallelisation levels and their rules, the input grammar, the `.save` folder and what Yambo takes from it. It is a digest of the official documentation and source code, for developers; each section names its sources. What this means for Visual Workflows is set apart in **For the suite** paragraphs.

Versions: the website documentation is labelled 7.5; the latest release is 7.6 (July 2026). Facts read from the source code are from the `qe-7.6` tag. Statements marked *(not verified)* come from secondary sources or inference.

**Sources**

- General user guide: <https://www.quantum-espresso.org/Doc/user_guide/> (parallelism: sections 3.3–3.5).
- PWscf user guide 7.5: <https://www.quantum-espresso.org/Doc/user_guide_PDF/pw_user_guide.pdf>.
- Input references: [INPUT_PW](https://www.quantum-espresso.org/Doc/INPUT_PW.html), [INPUT_PH](https://www.quantum-espresso.org/Doc/INPUT_PH.html), [INPUT_PROJWFC](https://www.quantum-espresso.org/Doc/INPUT_PROJWFC.html), [INPUT_BANDS](https://www.quantum-espresso.org/Doc/INPUT_BANDS.html).
- PHonon user guide, parallelism: <https://www.quantum-espresso.org/Doc/ph_user_guide/node17.html>.
- Source, tag `qe-7.6`: <https://gitlab.com/QEF/q-e/-/tree/qe-7.6> (`Modules/command_line_options.f90`, `PW/src/setup.f90`, `run_pwscf.f90`, `stop_run.f90`, `divide_et_impera.f90`, `UtilXlib/error_handler.f90`, `Modules/mp_pools.f90`, `LAXlib/la_helper.f90`, `PW/src/punch.f90`, `Modules/open_close_input_file.f90`).
- Release notes: <https://gitlab.com/QEF/q-e/-/raw/develop/Doc/release-notes>.

---

## 1. Running pw.x

### Command-line options

All QE programs read these (`command_line_options.f90`). Unknown arguments are ignored silently.

| Option (aliases) | Meaning | Default |
|---|---|---|
| `-i`, `-in`, `-inp`, `-input` | input file | stdin |
| `-ni`, `-nimage`, `-nimages`, `-npot` | images | 1 |
| `-nk`, `-npool`, `-npools` | k-point pools | automatic (7.1+) |
| `-nb`, `-nband`, `-nbgrp`, `-nband_group` | band groups | 1 |
| `-nt`, `-ntg`, `-ntask_groups`, `-nyfft` | task groups | automatic (7.1+) |
| `-nd`, `-ndiag`, `-northo`, `-nproc_diag`, `-nproc_ortho` | linear-algebra group size | automatic |
| `-nh`, `-howmany` | FFTs batched over bands | 1 |
| `-pd`, `-use_pd` | pencil decomposition (undocumented) | false |

The user guide documents only `-ni -nk -nb -nt -nd`.

**Input from a file, not stdin.** Without `-i`, pw.x reads stdin (and copies it to `input_tmp.in` in the working folder). Some MPI implementations do not pass stdin to the processes: the run hangs at `Waiting for input...`. Use `-inp file`. An input named `*.xml`, or starting with `<?xml`, is read as XML.

**Environment.** `ESPRESSO_TMPDIR` is the default `outdir` (else `./`); `ESPRESSO_PSEUDO` the default `pseudo_dir` (else `$HOME/espresso/pseudo/`); `OMP_NUM_THREADS` the OpenMP threads per MPI process.

### Output

Header and setup:

- `Program PWSCF v.7.x starts on ...`
- `Parallel version (MPI & OpenMP), running on N processor cores`, `Number of MPI processes:`, `Threads/MPI process:`; `MPI processes distributed on N nodes`.
- When greater than 1: `K-points division:     npool     = N`, `R & G space division:  proc/nbgrp/npool/nimage = N`, `band groups division: nbgrp = N`, `wavefunctions fft division: ...`.
- `WARNING: too many processors for an effective parallelization!` (more processes per band group than FFT planes).
- `Subspace diagonalization in iterative solution of the eigenvalue problem:` then `a serial algorithm will be used` or `scalapack|ELPA|custom distributed-memory algorithm (size of sub-group: n* n procs)`.
- `number of k points=  N` (half the internal count for `nspin=2`), `number of Kohn-Sham states=`, `number of electrons =`, `N Sym. Ops., with inversion, found`, `Dense  grid: ... FFT dimensions: ( n1, n2, n3)`, `Estimated max dynamical RAM per process >`.

Results:

- scf: `convergence has been achieved in N iterations` or `convergence NOT achieved after N iterations: stopping`; `!    total energy = ... Ry`; `the Fermi energy is X ev` or `highest occupied, lowest unoccupied level (ev):`.
- nscf and bands: `End of band structure calculation`.
- End: `Writing all to output data dir <outdir>/<prefix>.save/ :`, `PWSCF : <cpu> CPU <wall> WALL` (e.g. `1h 5m CPU  1h 7m WALL`), `JOB DONE.`

### Errors and exit codes

- An error (`errore`) prints a block between `%%%%` lines with `Error in routine <name> (<code>):` and the message, **appends it to a file `CRASH`** in the working folder (removed at the next start), and calls `MPI_Abort`: the exit code is non-zero.
- pw.x's own exit status (0 success, 1 error, 2 scf not converged, 3 ionic relaxation not converged, 255 stopped by `max_seconds`, a signal or the EXIT file) is returned to the shell only in builds with `-D__RETURN_EXIT_STATUS`, off by default. **Otherwise every orderly stop exits with 0 and prints `JOB DONE.`**, an unconverged scf included.
- Clean stop: a file `<prefix>.EXIT` in the working folder or `outdir`, or `max_seconds`. A restart (`restart_mode='restart'`) needs the same number of processes and the same parallelisation.
- Signals (SIGTERM from Slurm at the time limit) are caught only in builds with `-D__TERMINATE_GRACEFULLY` (experimental).

**For the suite.** A job's Slurm state says little about a QE run: `COMPLETED` can hide an unconverged scf. The checks of outputs (Phase 5) read `convergence has been achieved` (scf), `End of band structure calculation` (nscf, bands), the `%%%%` error block and `CRASH`. The job script can set `max_seconds` somewhat below the Slurm time limit, leaving time for writing, so a run that would be killed stops cleanly and can restart.

---

## 2. Parallelisation

Sources: general guide §3.3, PW guide §4.4 and §4.5.2; `setup.f90`, `mp_pools.f90`, `divide_et_impera.f90`, `la_helper.f90`.

### Levels

world → images (`-ni`) → pools (`-nk`, k-points) → band groups (`-nb`, mainly hybrid functionals) → plane waves and FFT ("R & G space", the processes left in a band group) → task groups (`-nt`) and, apart, the linear-algebra group (`-nd`, a square grid).

Processes are split in order: with `-ni 2` on 2n processes, image 0 has ranks 0…n−1. Images and pools exchange little; the processes of a pool exchange a lot and need a fast network. Example from the guide: `mpirun -np 4096 neb.x -ni 8 -nk 2 -nt 4 -nd 144` gives 512 processes per image, 256 per pool, 64 per task group and a 12 × 12 linear-algebra grid.

### Rules that stop the run

| Rule | Error |
|---|---|
| images divide the processes | `n. of images must be divisor of nproc` |
| pools divide the processes of an image; 1 ≤ pools ≤ processes | `invalid number of pools, parent_nproc /= nproc_pool * npool` |
| band groups divide the processes of a pool | |
| task groups divide the processes of a band group | `nyfft value incompatible with nproc_bgrp` |
| the linear-algebra grid's side ≤ `nbnd` | `Too few bands for required ndiag` |
| no pools with a single k-point at Γ (`K_POINTS gamma`) | `Gamma-only calculations not allowed with pools` |
| no pools with Berry phase (`lberry`, `lelfield`, `lorbm`) | |
| hybrid functionals: every pool has k-points; no pools with `tstress` | |

### What is allowed but wasteful

- **k-points not divisible by the pools:** the first pools get one k-point more. Legal, unbalanced. Scaling is near-linear when the k-points are a multiple of the pools.
- **More pools than k-points:** `suboptimal parallelization: some nodes have no k-points`; the run continues (fatal only with hybrid functionals). With `nspin=2` the k-points count twice for pools.
- **`-nd`:** capped at the processes of a band group, then rounded **down** to a square n² without a message. Used only if n > 1.
- **Too many processes per pool:** more than the FFT grid's third dimension (nr3) gives the warning above. The guide's ideal is ½ to ¼ of nr3 per pool, and at most k-points × nr3 processes in all.

### Choices pw.x makes itself (7.1+)

When `-nk`, `-nt` or `-nd` are not given (`setup_para`):

- pools: if processes per image > nr3/2 and there is more than one k-point, the smallest divisor of the processes, from 2 to the k-points, that leaves at most nr3/2 processes per pool;
- task groups: if processes per band group > nr3, the smallest divisor from 2 to min(nbnd, 16) leaving fewer than nr3/4;
- `-nd`: the largest n with n² ≤ processes per band group and n ≤ nint(nbnd/100) (blocks of about 100 × 100 or more); 1 in GPU builds.

### Advice from the guides

- Pools first, especially without a fast network ("do not try FFT parallelization on more than 4 or 8 procs" then).
- `-nd` only with a few hundred bands or more.
- `-nt 4` or `-nt 8` when the FFT stops scaling.
- **Memory:** only the plane-wave/FFT level distributes memory (and `-nd` the nbnd × nbnd matrices). Pools, images, band groups and task groups do not. Out of memory: more processes or fewer pools, smaller `nbnd`, `diago_david_ndim`, `mixing_ndim`, `diagonalization='cg'`, `disk_io='medium'`.
- **OpenMP:** only with control over process and thread placement ("forget about mixed OpenMP-MPI" otherwise); a healthy run has CPU time ≈ wall time × threads.
- **GPU** *(not verified: NERSC and CINECA documentation)*: one MPI process per GPU, `OMP_NUM_THREADS=1`, pools first (`-nk` = number of GPUs), serial diagonalisation. pw.x warns `High GPU oversubscription detected` above two processes per GPU.

**For the suite.** The Phase 2 checks, for a pw.x run with P MPI processes:

- errors: P not divisible by `-ni`, P/ni by `-nk`, P/(ni·nk) by `-nb`, P/(ni·nk·nb) by `-nt`; `-nk` > P; `-nk` > 1 with `K_POINTS gamma`; a `-nd` grid side larger than `nbnd` when `nbnd` is set;
- warnings: `-nd` not a square or larger than the processes of a band group (QE rounds it down); more pools than k-points (from `K_POINTS`: a list's count; an automatic grid's product is an upper bound, symmetry reduces it, Phase 3 computes it exactly; × 2 with `nspin=2`); k-points not a multiple of the pools; `OMP_NUM_THREADS` > 1 without the threads' placement set in the job.

The output's "K-points division", "R & G space division" and "Subspace diagonalization" lines say what pw.x used, including what it chose itself; Phase 5 can show them.

---

## 3. The pw.x input

Sources: INPUT_PW, PW guide §3.1.

### Structure

`&CONTROL`, `&SYSTEM`, `&ELECTRONS`, then `&IONS` (relax, md, vc-*), `&CELL` (vc-*), `&FCP`, `&RISM` (7.1+), **in this order**, each ending with `/`; then the cards.

| Card | Notes |
|---|---|
| `ATOMIC_SPECIES` | `label mass pseudo-file`; the label is a chemical symbol plus at most one more character (3 in all) |
| `ATOMIC_POSITIONS` | `alat` (default, deprecated), `bohr`, `angstrom`, `crystal`, `crystal_sg`; optional 0/1 × 3 to fix coordinates; ignored by nscf and bands |
| `K_POINTS` | `tpiba` (default), `automatic` (`nk1 nk2 nk3 sk1 sk2 sk3`, shifts 0 or 1), `crystal`, `gamma` (no lines; real wave functions, half the cost), `tpiba_b`/`crystal_b` (a path: each point's weight is the number of points to the next), `tpiba_c`/`crystal_c` |
| `CELL_PARAMETERS` | `alat`, `bohr`, `angstrom`; required with `ibrav=0`, not allowed otherwise |
| `ADDITIONAL_K_POINTS`, `OCCUPATIONS`, `CONSTRAINTS`, `ATOMIC_VELOCITIES`, `ATOMIC_FORCES`, `SOLVENTS` | optional |
| `HUBBARD {atomic\|ortho-atomic\|norm-atomic\|wf\|pseudo}` | DFT+U since 7.1 (the old `Hubbard_U` variables are gone), values in eV |

### Syntax

- Plain ASCII: no tabs, no CRLF line ends, no typographic quotes. End with a newline.
- In namelists `!` starts a comment; in cards `!` or `#` in the first column does.
- A space between a card and its option: `ATOMIC_POSITIONS (bohr)` or `ATOMIC_POSITIONS bohr`.
- Unknown cards are ignored; an unknown namelist variable stops pw.x (`Error while reading XXX namelist`).
- Fortran namelist rules *(from the Fortran standard, not QE's documentation)*: case-insensitive names; `name = value` separated by commas or line ends; strings in `'…'` or `"…"`; logicals `.true.`, `.false.`, `.t.`, `.f.`, `T`, `F`; reals like `1.0d-8`; array elements `celldm(1)`, `starting_magnetization(2)`.

### Main variables

- **&CONTROL:** `calculation` (`scf` default, `nscf`, `bands`, `relax`, `md`, `vc-relax`, `vc-md`); `prefix` (`'pwscf'`); `outdir`; `pseudo_dir`; `verbosity` (`'low'` or `'high'`); `restart_mode`; `max_seconds`; `disk_io` (`low` default for scf, `medium` otherwise; **`nowf`, `minimal` and `none` write no wave functions**); `tstress`, `tprnfor`. `wf_collect` is obsolete (no longer implemented; wave functions are collected since 6.2).
- **&SYSTEM:** `ibrav`, `nat`, `ntyp`, `ecutwfc` required; `celldm(1..6)` in Bohr **or** `A, B, C, cosAB, cosAC, cosBC` in Å; `ecutrho` (4 × `ecutwfc`; 8–12 × for ultrasoft); `nbnd` (default: half the electrons for insulators; max(1.2·nelec/2, nelec/2 + 4) with smearing; twice that for noncollinear); `occupations` (`fixed` default, `smearing` with `smearing`/`degauss`, `tetrahedra*`, `from_input`); `nspin` 1 or 2 (noncollinear: `noncolin=.true.`, not `nspin=4`); spin–orbit: `noncolin` and `lspinorb` both true, with fully relativistic pseudopotentials; `starting_magnetization(i)` (since 7.3, |value| ≥ 1 is a moment in μB); symmetry: `nosym`, `noinv`, `no_t_rev`, `force_symmorphic`, `use_all_frac`.
- **&ELECTRONS:** `conv_thr` (1e-6, extensive; in nscf it sets the diagonalisation threshold); `electron_maxstep` (100); `diagonalization` (`david` default, `cg`, `paro`, `rmm-davidson`, `rmm-paro`; `ppcg` dropped in 7.5); `diago_full_acc` (false: empty states converged loosely; **true for GW and BSE**); `startingpot` (`file` for nscf and bands).

---

## 4. The `.save` folder, nscf, and Yambo's p2y

Sources: general guide §3.4, `punch.f90`; Yambo's h-BN tutorial <https://wiki.yambo-code.eu/wiki/index.php?title=Bulk_material%3A_h-BN> and paper <https://arxiv.org/abs/1902.03837>.

`outdir/prefix.save/` holds:

- `data-file-schema.xml`: structure, symmetries, k-points, eigenvalues, Fermi level, `nbnd` (schema: <https://github.com/QEF/qeschemas>); a copy is written as `outdir/prefix.xml`;
- `charge-density.dat` (or `.hdf5`), written only by scf;
- `wfcN.dat` per k-point (`wfcupN.dat`/`wfcdwN.dat` with `nspin=2`), independent of the parallelisation; portable between machines only when written as HDF5;
- copies of the pseudopotentials.

Outside `.save`, `outdir` holds scratch and restart files (`prefix.wfcN`, `prefix.mixN`) readable only with the same parallelisation. Everything is written at the end of the run.

**nscf and bands** read the scf's `.save` (same `prefix` and `outdir`): the XML and the charge density. Positions and cell come from it, not from the input. They overwrite the XML and the wave functions in the same `.save`: copy it first to keep the scf's. Hybrid functionals cannot run nscf. Two runs must never share `outdir` and `prefix` at the same time.

**Irreducible k-points.** pw.x keeps the lattice rotations that map the crystal onto itself (fractional translations within 1e-5) and reduces a Monkhorst–Pack grid with them and time reversal. The count is printed (`number of k points=`) and stored in the XML. It can change "for no apparent good reason" when a new cutoff or cell changes the FFT grid and so drops symmetries with fractional translations; a supercell drops all of them.

**For Yambo.** scf, then nscf on Yambo's k-grid with the empty bands Yambo will use (`nbnd`), then `p2y` inside `prefix.save`, which writes `SAVE/` (`ns.db1`, `ns.wf*`) and prints `P2Y completed`. The tutorial sets `force_symmorphic=.true.` (Yambo does not handle fractional translations) and `diago_full_acc=.true.`; nscf must write wave functions (no `disk_io='nowf'`, `'minimal'`, `'none'`). Whether `nosym` is needed: *(not verified)*, Yambo expands the irreducible k-points itself.

**For the suite.** A step running p2y links or copies the nscf's `.save` (never shares it with a running job). Phase 4's checks on a chain: nscf with `force_symmorphic=.true.`, `diago_full_acc=.true.`, a `disk_io` that writes wave functions, `nbnd` at least the bands asked of Yambo.

---

## 5. ph.x, projwfc.x and bands.x

- **ph.x** (INPUT_PH, PH guide §17): a title line, `&INPUTPH` (`prefix`, `outdir` as in pw.x, `fildyn`, `tr2_ph` 1e-12, `ldisp` with `nq1 nq2 nq3`, `epsil`, `recover`, `start_q`/`last_q`, `start_irr`/`last_irr`, `electron_phonon`), then a q-point line unless `ldisp`. Writes to `outdir/_ph0/prefix.phsave/`. Parallel over images (q-points and irreducible representations), pools and plane waves only: `mpirun -np 64 ph.x -ni 8 -nk 2`, after a `mpirun -np 8 pw.x -nk 2`; then the results are collected by running `ph.x -nk 2` again with `recover=.true.`.
- **projwfc.x** (INPUT_PROJWFC): `&PROJWFC` (`prefix`, `outdir`, `degauss`, `Emin`/`Emax`/`DeltaE` in eV, `filpdos`, `lsym`, `kresolveddos`, `lwrite_overlaps`); needs the wave functions; writes `atomic_proj.xml` and `filpdos.pdos_*`. `-nd 1` with `lwrite_overlaps`. Pools: *(not verified)*.
- **bands.x** (INPUT_BANDS): `&BANDS` (`prefix`, `outdir`, `filband`, `lsym`, `spin_component`, `lsigma`); after a `calculation='bands'` run; no pools unless `lsym` or `no_overlap`; not for Γ-only runs.

---

## 6. Pitfalls for job scripts

1. Exit code 0 and `JOB DONE.` do not mean success: read the output (§1).
2. A crash may lose its message from the output: read `CRASH` and the job's error file.
3. Divisibility rules stop the run at once (§2); more pools than k-points does not.
4. Give `-inp file`, never `< file`, under srun or mpirun; stdin also writes `input_tmp.in`, which two runs in one folder overwrite.
5. Never two runs with the same `outdir` and `prefix` at once; avoid NFS for `outdir`.
6. `max_seconds` below the time limit lets a run stop cleanly; restarting needs the same parallelisation.
7. `disk_io` `nowf`, `minimal` or `none` break p2y, ph.x and projwfc.x.
8. OpenMP: `OMP_NUM_THREADS` equal to Slurm's `--cpus-per-task`; one thread when placement is not controlled.
9. Version changes: HUBBARD card, RISM and automatic parallel choices since 7.1; `starting_magnetization` ≥ 1 in μB since 7.3; `symmetries_with_labels` since 7.4; PPCG removed in 7.5; no hybrid stress with ultrasoft or PAW in 7.6.1 (vc-relax stops); `wf_collect` a no-op since 6.2.

*Not verified from a primary source:* the relax convergence line (`bfgs converged`), pools in projwfc.x, whether Yambo needs `nosym`, the XML status element's name, and the GPU advice.
