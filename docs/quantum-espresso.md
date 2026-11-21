# Quantum ESPRESSO for the suite

[← Documentation](../README.md#documentation)

What the Visual suite needs to know about Quantum ESPRESSO (QE) to write job scripts, check parallelisation, build inputs and read results: running pw.x, its parallelisation levels and their rules, the input grammar, the `.save` folder and what Yambo takes from it. It is a digest of the official documentation and source code, for developers; each section names its sources. What this means for Visual Workflows is set apart in **For the suite** paragraphs.

Versions: the website documentation is labelled 7.5; the latest release is 7.6 (July 2026). Facts read from the source code are from the `qe-7.6` tag; on 2026-11-21 the parallel setup, the command line, `max_seconds`, errors and exit status were checked again at `qe-7.3.1` and `qe-7.5` (the versions on Lynx and Tirant), where those files are the same unless noted. Statements marked *(not verified)* come from secondary sources or inference.

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

All QE programs read these (`command_line_options.f90`). Options must be spelled exactly: anything else (`--nk`, `-nk=4`, `-nk4`) is ignored silently. A value that is not a number, or missing, prints `unexpected argument # N` and the option is ignored.

| Option (aliases) | Meaning | Default |
|---|---|---|
| `-i`, `-in`, `-inp`, `-input` | input file | stdin |
| `-ni`, `-nimage`, `-nimages`, `-npot` | images | 1 |
| `-nk`, `-npool`, `-npools` | k-point pools | automatic (7.1+) |
| `-nb`, `-nband`, `-nbgrp`, `-nband_group` | band groups | 1 |
| `-nt`, `-ntg`, `-ntask_groups`, `-nyfft` | task groups (sets both task groups and nyfft) | automatic (7.1+) |
| `-nd`, `-ndiag`, `-northo`, `-nproc_diag`, `-nproc_ortho` | linear-algebra group size | automatic |
| `-nh`, `-nhw`, `-n_howmany`, `-howmany` | FFTs batched over bands | 1 |
| `-pd`, `-use_pd`, `-pencil_decomposition`, `-use_pencil_decomposition` | pencil decomposition (undocumented; takes a logical: `-pd .true.`) | false |

The user guide documents only `-ni -nk -nb -nt -nd`.

**Input from a file, not stdin.** Without `-i`, only the first process reads stdin: it prints `Waiting for input...`, copies it to `input_tmp.in` in the working folder, reads that, and deletes it. Some MPI implementations do not pass stdin to the processes: the run hangs at `Waiting for input...`. Use `-inp file`. An input named `*.xml`, or starting with `<?xml`, stops pw.x (`xml input disabled`, `read_input.f90`, 7.3.1 to 7.6).

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

- An error (`errore`) prints, on every process that raises it, a block between `%%%%` lines with `Error in routine <name> (<code>):` and the message, **appends it to a file `CRASH`** in the working folder (with `task #`; removed at the next start; 7.3.1 serial builds write none), and calls `MPI_Abort(…, 1)`: the exit code is non-zero (its value depends on the launcher).
- pw.x's own exit status (0 success, 1 error, 2 scf not converged, 3 ionic relaxation not converged, 255 stopped by `max_seconds`, a signal or the EXIT file) is returned to the shell only in builds with `-D__RETURN_EXIT_STATUS` (autoconf's `--enable-exit-status`; CMake has no switch), off by default. **Otherwise every orderly stop exits with 0 and prints `JOB DONE.`**, an unconverged scf included.
- Clean stop: a file `<prefix>.EXIT` in the working folder or `outdir` (`Program stopped by user request`), or `max_seconds` (`check_stop.f90`):
  - **wall** time from the start of the run (after start-up and reading the input), though the message says `Maximum CPU time exceeded`, with `max_seconds =` and `elapsed seconds =`; the first process decides and tells the others;
  - checked before the run, at each scf iteration, after each k-point of the scf and nscf diagonalisation, and at the end; **with more pools than k-points it is never checked inside the k-point loop**, so an nscf sees it only when all bands are done;
  - on a stop, pw.x writes `Calculation stopped in scf loop at iteration #` (`prefix.restart_scf`) or `… in k-point loop, point #` (`prefix.restart_k`), per process in `outdir`, then the `.save` XML and the **unconverged** charge density (an nscf writes only the XML, overwriting the scf's), keeps `prefix.wfcN`, prints `JOB DONE.`, exit status 255 (0 without the flag above);
  - a restart (`restart_mode='restart'`, `startingwfc` and `startingpot` left as `'file'`) needs the same `prefix` and `outdir` and the same number of processes, else the wave functions are recomputed without a word (`Cannot read wfcs: file not found`); the same pools and plane-wave split too *(inferred)*; `disk_io` `nowf`, `minimal` or `none` make it impossible.
- Signals: autoconf's `--enable-signals` catches SIGUSR1 only (`__TRAP_SIGUSR1`); SIGTERM from Slurm at the time limit is caught only with `-D__TERMINATE_GRACEFULLY`, set by hand.
- 7.5 GPU builds stop on `-nt` > 1 (`No task groups for GPUs`). From 7.4 on, Intel builds no longer lift the stack limit themselves, so `ulimit -s unlimited` may be needed *(inferred)*.

**For the suite.** A job's Slurm state says little about a QE run: `COMPLETED` can hide an unconverged scf. The checks of outputs (Phase 5) read `convergence has been achieved` (scf), `End of band structure calculation` (nscf, bands), the `%%%%` error block and `CRASH`. The job script can set `max_seconds` somewhat below the Slurm time limit, leaving time for writing, so a run that would be killed stops cleanly and can restart.

---

## 2. Parallelisation

Sources: general guide §3.3, PW guide §4.4 and §4.5.2; `setup.f90`, `mp_pools.f90`, `divide_et_impera.f90`, `la_helper.f90`.

### Levels

world → images (`-ni`) → pools (`-nk`, k-points) → band groups (`-nb`, mainly hybrid functionals) → plane waves and FFT ("R & G space", the processes left in a band group) → task groups (`-nt`) and, apart, the linear-algebra group (`-nd`, a square grid).

Processes are split in order: with `-ni 2` on 2n processes, image 0 has ranks 0…n−1. Images and pools exchange little; the processes of a pool exchange a lot and need a fast network. Example from the guide: `mpirun -np 4096 neb.x -ni 8 -nk 2 -nt 4 -nd 144` gives 512 processes per image, 256 per pool, 64 per task group and a 12 × 12 linear-algebra grid.

### Rules that stop the run

In this order (`pwscf.f90`, `mp_images.f90`, `setup.f90`'s `setup_para`, `mp_pools.f90`, `mp_bands.f90`, `set_para_diag.f90`):

| Rule | Error |
|---|---|
| **pw.x: one image only** (`-ni` > 1 works only in `manypw.x`, NEB, PHonon) | `image parallelization not allowed` (after `invalid number of images, out of range` / `n. of images must be divisor of nproc`) |
| pools divide the processes of an image; 1 ≤ pools ≤ processes | `invalid number of pools, out of range`; `invalid number of pools, parent_nproc /= nproc_pool * npool` |
| band groups divide the processes of a pool; 1 ≤ band groups ≤ them | `invalid number of band groups, out of range`; `n. of band groups  must be divisor of parent_nproc` |
| task groups divide the processes of a band group | `nyfft value incompatible with nproc_bgrp` |
| the linear-algebra grid's side n = ⌊√min(nd, processes of a band group)⌋ ≤ `nbnd` (the default `nbnd` when not set) | `Too few bands for required ndiag` |
| no pools with a single k-point at Γ: `K_POINTS gamma` with `nspin=1` (with `nspin=2` there are 2 k-points, so `-nk 2` is allowed) | `Gamma-only calculations not allowed with pools` |
| no pools with Berry phase (`lberry`, `lelfield`, `lorbm`) | |
| hybrid functionals: every pool has k-points; no pools with `tstress` | |

### What is allowed but wasteful

- **k-points not divisible by the pools:** the first pools get one k-point more. Legal, unbalanced. Scaling is near-linear when the k-points are a multiple of the pools.
- **More pools than k-points:** `suboptimal parallelization: some nodes have no k-points`; the run continues (fatal only with hybrid functionals). With `nspin=2` the k-points count twice for pools.
- **`-nd`:** capped at the processes of a band group, then rounded **down** to a square n² without a message (`la_helper.f90`, `distools.f90`). Used only if n > 1.
- **Too many processes per pool:** more than the FFT grid's third dimension (nr3) gives the warning above. The guide's ideal is ½ to ¼ of nr3 per pool, and at most k-points × nr3 processes in all.

### Choices pw.x makes itself (7.1+)

When `-nk`, `-nt` or `-nd` are not given (`setup_para`):

- pools: if processes per image > nr3/2 and there is more than one k-point (counted twice with `nspin=2`; taken as 1 with Berry phase, `lelfield`, `lorbm`, or hybrid functionals with stress), the smallest divisor of the processes, from 2 to the k-points, that leaves at most nr3/2 processes per pool; if none does, the largest divisor tried (not 1);
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

- errors: options QE would ignore (misspelled, `-nk=4`, a value that is not a number); `-ni` other than 1; `-nk` < 1, above P or not dividing it; `-nb` < 1, above P/nk or not dividing it; `-nt` < 1 or not dividing P/(nk·nb); ⌊√min(nd, P/(nk·nb))⌋ larger than `nbnd` when `nbnd` is set; `-nk` > 1 with `K_POINTS gamma` and `nspin=1`; `-nk` > 1 with Berry phase (`lberry`, `lelfield`, `lorbm`), or with a hybrid functional and `tstress`;
- warnings: `-nd` not a square or larger than the processes of a band group (QE rounds it down; serial below 4); more pools than k-points (idle pools, and `max_seconds` not checked within the k-point loop) (from `K_POINTS`: a list's count; an automatic grid's product is an upper bound, symmetry reduces it, Phase 3 computes it exactly; × 2 with `nspin=2`); k-points not a multiple of the pools; `OMP_NUM_THREADS` > 1 without the threads' placement set in the job.

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
- In namelists `!` starts a comment. In cards only blank lines and `#` in the first column are skipped: a whole-line `!` comment inside a card's data is read as data (a trailing `!` after positions is fine). Card lines are cut at 256 characters (`parser.f90`).
- A space between a card and its option: `ATOMIC_POSITIONS (bohr)` or `ATOMIC_POSITIONS bohr`. Card names and options are case-insensitive; **string values are not** (`calculation='SCF'` is refused).
- Unknown cards, and data lines beyond `ntyp` species or `nat` atoms, are ignored with only `Warning: card … ignored`.
- **An unknown or misspelt namelist variable stops pw.x** with `bad line in namelist &system: " ecutwfx=30" (error could be in the previous line)` (`read_namelists.f90`). The accepted names change between versions (added after 7.3.1, by 7.5: `symmetry_with_labels` (7.4), `use_spinflip`, `orbital_resolved`, `tgdiis_step`; 7.6: `exx_type`; `lkpoint_dir` is gone, so it stops pw.x; `wf_collect` is accepted and ignored); `yambopy_ext/qepy/pw_namelists.py` lists them per version. Namelists out of order (`&electrons` before `&system`), or a required one missing (`&CONTROL`, `&SYSTEM`, `&ELECTRONS`; `&IONS` for relax and md; `&CELL` for vc-relax and vc-md), give the same message naming the next namelist *(tested with gfortran)*.
- Silent defaults: no `K_POINTS` card means Γ only with gamma tricks; an unknown `K_POINTS` option means `tpiba`; an unknown `disk_io` means the default. Pseudopotential file names are cut at 80 characters.
- Fortran namelist rules *(from the Fortran standard, not QE's documentation)*: case-insensitive names; `name = value` separated by commas or line ends; strings in `'…'` or `"…"`; logicals `.true.`, `.false.`, `.t.`, `.f.`, `T`, `F`; reals like `1.0d-8`; array elements `celldm(1)`, `starting_magnetization(2)`.

### Main variables

- **&CONTROL:** `calculation` (`scf` default, `nscf`, `bands`, `relax`, `md`, `vc-relax`, `vc-md`); `prefix` (`'pwscf'`); `outdir`; `pseudo_dir`; `verbosity` (`'low'` or `'high'`); `restart_mode`; `max_seconds`; `disk_io` (`low` default for scf, `medium` otherwise; **`nowf`, `minimal` and `none` write no wave functions**); `tstress`, `tprnfor`. `wf_collect` is obsolete (no longer implemented; wave functions are collected since 6.2). `ecutwfc` may be left out when the pseudopotentials give `wfc_cutoff` (the largest is used). 7.6 adds `diagonalization='direct'` (7.5 stops on it) and `exx_type` (`bands` or `band_pairs`).

**Startup checks from the input alone** (7.5; `read_namelists.f90`'s `*_checkin`, `input.f90`, `setup.f90`, `set_occupations.f90`, `cell_base.f90`, `latgen.f90`), each stopping pw.x: `calculation` not one of the seven; `ion_dynamics`/`cell_dynamics` not supported by the calculation; `max_seconds` < 0; ibrav ≠ 0 without `celldm(1)` or `A`, or both given; an unknown ibrav or `celldm(n)` out of range for it; ibrav = 0 without `CELL_PARAMETERS`, or `CELL_PARAMETERS` with ibrav ≠ 0; `nspin` not 1, 2 or 4; `noncolin` with `nspin=2`; `ecutrho` ≤ `ecutwfc` (below 4 × only a remark); `occupations` or `smearing` unknown, smearing without `degauss`; an unknown `diagonalization` (`ppcg` refused since 7.5) or `restart_mode`; `tot_magnetization` without `nspin=2`; `nspin=2` with fixed occupations and no `tot_magnetization`, or with smearing and neither `starting_magnetization` nor `tot_magnetization` (scf-type runs); `lspinorb` without `noncolin`; `noncolin` with `K_POINTS gamma`; the pre-7.1 Hubbard variables; positions before species, an unknown positions option, too few positions, a label not among the species, two species with one label; an automatic grid with shifts other than 0/1 or a non-positive size; tetrahedra without an automatic grid. With the pseudopotentials: a file not found (for nscf, bands and restarts looked for in `outdir/prefix.save/` first, then `pseudo_dir`), spin–orbit with no spin–orbit pseudopotential (7.5; only a remark in 7.3.1), and **`nbnd` below the occupied bands, checked in scf-type runs only** (nelec = Σ `z_valence` − `tot_charge`; at least nelec/2 bands, nelec with `noncolin`). `starting_magnetization`: if any value is ≥ 1 in magnitude, all are divided by `z_valence` (and clamped to [−1, 1] in 7.3.1 and 7.5). From a UPF, the checks need `z_valence`, `pseudo_type`, `is_ultrasoft`, `is_paw`, `has_so`, `functional`, `wfc_cutoff`.
- **&SYSTEM:** `ibrav`, `nat`, `ntyp`, `ecutwfc` required; `celldm(1..6)` in Bohr **or** `A, B, C, cosAB, cosAC, cosBC` in Å; `ecutrho` (4 × `ecutwfc`; 8–12 × for ultrasoft); `nbnd` (default: half the electrons for insulators; max(1.2·nelec/2, nelec/2 + 4) with smearing; twice that for noncollinear); `occupations` (`fixed` default, `smearing` with `smearing`/`degauss`, `tetrahedra*`, `from_input`); `nspin` 1 or 2 (noncollinear: `noncolin=.true.`, not `nspin=4`); spin–orbit: `noncolin` and `lspinorb` both true, with fully relativistic pseudopotentials; `starting_magnetization(i)` (since 7.3, |value| ≥ 1 is a moment in μB); symmetry: `nosym`, `noinv`, `no_t_rev`, `force_symmorphic`, `use_all_frac`.
- **&ELECTRONS:** `conv_thr` (1e-6, extensive; in nscf it sets the diagonalisation threshold); `electron_maxstep` (100); `diagonalization` (`david` default, `cg`, `paro`, `rmm-davidson`, `rmm-paro`; `ppcg` dropped in 7.5); `diago_full_acc` (false: empty states converged loosely; **true for GW and BSE**); `startingpot` (`file` for nscf and bands).

---

## 4. The `.save` folder, nscf, and Yambo's p2y

Sources: general guide §3.4, `punch.f90`; Yambo's h-BN tutorial <https://wiki.yambo-code.eu/wiki/index.php?title=Bulk_material%3A_h-BN> and paper <https://arxiv.org/abs/1902.03837>.

`outdir/prefix.save/` holds:

- `data-file-schema.xml`: structure, symmetries, k-points, eigenvalues, Fermi level, `nbnd` (schema: <https://github.com/QEF/qeschemas>); a copy is written as `outdir/prefix.xml`;
- `charge-density.dat` (or `.hdf5`), written only by scf-type runs (not with `disk_io` `minimal` or `none`);
- `wfcN.dat` per k-point (`wfcupN.dat`/`wfcdwN.dat` with `nspin=2`), independent of the parallelisation; portable between machines only when written as HDF5;
- copies of the pseudopotentials (not with `disk_io='nowf'`).

What `disk_io` writes (`punch.f90`): `none` nothing; `minimal` the XML only; `nowf` the XML and the density, no wave functions; the default is `low` for scf-type runs, `medium` otherwise.

Outside `.save`, `outdir` holds scratch and restart files (`prefix.wfcN`, `prefix.mixN`) readable only with the same parallelisation. Everything is written at the end of the run.

**nscf and bands** read the scf's `.save` (same `prefix` and `outdir`): the XML (with the Fermi energy and the number of electrons) and the charge density. Positions and cell come from it, not from the input. They overwrite the XML and the wave functions in the same `.save`: copy it first to keep the scf's. A missing `outdir` stops the run (`bands or non-scf calculation not possible: needed files are missing`), but **a missing charge density does not**: `Cannot read rho : file not found`, and the run goes on from atomic charges. `nspin` or `noncolin` different from the scf's only prints `some spin components not found`. Each run, nscf included, finds its symmetries again from its own input (`nosym`, `noinv`, `force_symmorphic`). Hybrid functionals and nscf: the check is commented out in 7.5 (`setup.f90`) *(not verified otherwise)*. Two runs must never share `outdir` and `prefix` at the same time.

**Irreducible k-points.** pw.x keeps the lattice rotations that map the crystal onto itself (fractional translations within 1e-5) and reduces a Monkhorst–Pack grid with them and time reversal. The count is printed (`number of k points=`) and stored in the XML. It can change "for no apparent good reason" when a new cutoff or cell changes the FFT grid and so drops symmetries with fractional translations; a supercell drops all of them.

**For Yambo.** scf, then nscf on Yambo's k-grid with the empty bands Yambo will use (`nbnd`), then `p2y` inside `prefix.save`, which writes `SAVE/` (`ns.db1`, `ns.wf*`) and prints `P2Y completed`. The tutorial sets `force_symmorphic=.true.` (Yambo does not handle fractional translations) and `diago_full_acc=.true.`; nscf must write wave functions (no `disk_io='nowf'`, `'minimal'`, `'none'`). Whether `nosym` is needed: *(not verified)*, Yambo expands the irreducible k-points itself.

**What p2y needs** (from QE's side): `data-file-schema.xml` and `wfc1.dat` (or `wfcup1.dat`, or `.hdf5`), not the density; symmetries without fractional translations, which come from **the nscf's** input (`force_symmorphic` matters there; in the scf it is optional); norm-conserving pseudopotentials (`is_ultrasoft` and `is_paw` false, `pseudo_type` NC or SL); no DFT+U; of hybrids only HF, PBE0, B3LYP and GAUPBE (p2y's own checks, see [Yambo](yambo.md) §6).

**For the suite.** A step running p2y links or copies the nscf's `.save` (never shares it with a running job). Chain checks: nscf with `force_symmorphic=.true.` (or `nosym`), `diago_full_acc=.true.`, a `disk_io` that writes wave functions, `nbnd` set and at least the bands asked of Yambo; the scf writing its density; the same `prefix`, `outdir`, `nspin` and `noncolin` in both; norm-conserving pseudopotentials.

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
6. `max_seconds` (wall time, checked per scf iteration and per k-point) below the time limit lets a run stop cleanly, leaving room for writing the `.save`; restarting needs the same number of processes (else the wave functions are silently recomputed). With more pools than k-points it is not checked within the k-point loop.
7. `disk_io` `nowf`, `minimal` or `none` break p2y, ph.x and projwfc.x.
8. OpenMP: `OMP_NUM_THREADS` equal to Slurm's `--cpus-per-task`; one thread when placement is not controlled.
9. Version changes: HUBBARD card, RISM and automatic parallel choices since 7.1; `starting_magnetization` ≥ 1 in μB since 7.3; `symmetry_with_labels` since 7.4; PPCG removed in 7.5; `diagonalization='direct'` and `exx_type` in 7.6; no hybrid stress with ultrasoft or PAW in 7.6.1 (vc-relax stops); `wf_collect` a no-op since 6.2.

*Not verified from a primary source:* the relax convergence line (`bfgs converged`), pools in projwfc.x, whether Yambo needs `nosym`, the XML status element's name, and the GPU advice.
