# Yambo for the suite

[← Documentation](../README.md#documentation)

What the Visual suite needs to know about Yambo to write job scripts, check parallelisation, build inputs and read the databases Visual Excitons uses: the executables and their command lines, the input grammar, the parallel variables and their rules, BSE and GW settings, the databases, the usual chain from QE, and pitfalls. It is a digest of Yambo's source code and documentation, for developers; each section names its sources. What this means for Visual Workflows is set apart in **For the suite** paragraphs. The QE side of the chain is in [Quantum ESPRESSO](quantum-espresso.md).

Versions: most of this was read in the source of Yambo 5.3 (`master` at 19c1241, October 2025, "5.3.0 r.23959"), with key files compared with the tags from 4.0.4 to 5.2.4. The repository's `ChangeLog` is empty; the release notes are the change log. Statements marked *(not verified)* could not be confirmed from the source or the official documentation.

**Sources**

- Source: <https://github.com/yambo-code/yambo> (paths below are relative to `src/` at tag 5.3.0 unless written otherwise).
- Release notes: <https://github.com/yambo-code/yambo/releases>.
- Wiki: <https://wiki.yambo-code.eu/wiki/> (pages "Initialization", "Input file generation and command line options 5.0", "Using Yambo in parallel", "GW parallel strategies"); new wiki: <https://yambo-code.github.io/yambo-wiki/> (tutorial "BSE on MoS2 on HPC").
- Paper: Sangalli et al., J. Phys.: Condens. Matter 31, 325902 (2019), <https://arxiv.org/abs/1902.03837>.
- BSE solvers on GPUs and SLEPc: <https://arxiv.org/abs/2504.10096>.

---

## 1. Executables and command lines

### p2y

Converts a QE run to Yambo (`interfaces/p2y/`). It runs inside `<prefix>.save`, or with `-I <dir>`, reads `data-file-schema.xml` (QE 6.2 and later, with or without HDF5; the older `data-file.xml` too) and writes `SAVE/`:

- `ns.db1`: cell, symmetries, k-points, Kohn–Sham energies;
- `ns.wf` and `ns.wf_fragments_<ik>_<i>`: wave functions;
- `ns.kb_pp_pwscf` and its fragments: pseudopotential projectors.

Options (5.x): `-I` input folder, `-O` output folder, `-nofrag`/`-U` (no fragments, serial), `-fragbands`/`-b <n>`, `-alat_factor`/`-a`, `-notr`/`-t`, `-nosym`/`-n`, `-nowf`/`-w` (no wave functions), `-verbio`/`-v`. p2y runs under MPI since 4.3.

**It stops on symmetries with fractional translations:** `Non-symmorphic symmetry operations are not supported! Use force_symmorphic=.true. in PWSCF` (`interfaces/p2y/mod_p2y.F`). A format it does not recognise: `QE format not recognised/compatible (was wf_collect set to true?)`. Each run gives `ns.db1` a new random serial number (§7).

### Initialisation (setup)

`yambo` with no runlevel, in the folder that contains `SAVE` (not inside it), runs the setup: `SAVE/ndb.gops` and `SAVE/ndb.kindx`, report `r_setup`. These always go to `SAVE`, never to a job folder (`io/io_QINDX.F`). Run from inside `SAVE`, yambo stops with `cannot access CORE database (SAVE/*db1 and/or SAVE/*wf)`.

### yambo

Options in long form with one dash, or a letter (`driver/options_yambo.c`, `options_control.c`).

| Runlevel option | Meaning | Written to the input |
|---|---|---|
| `-setup`, `-i` | initialisation | `setup` |
| `-hf`, `-x` | Hartree–Fock self-energy and Vxc | `HF_and_locXC` |
| `-gw0`, `-p <p\|m\|c\|r>` | GW: plasmon pole, MPA, COHSEX, real axis | `gw0` (+ `ppa`, `mpa`, `cohsex`, `em1d`) |
| `-dyson`, `-g <n\|s\|g>` | Dyson solver: Newton, secant, Green | `dyson` |
| `-lifetimes`, `-l` | QP lifetimes | |
| `-coulomb`, `-r` | Coulomb cutoff and RIM | `rim_cut` |
| `-rw`, `-w` | RIM-W (5.2+) | `rim_w` |
| `-optics`, `-o <c\|b>` | optics in G space (`c`) or transition space, BSE (`b`) | `optics`, `chi` or `bse` |
| `-X`, `-d <s\|p\|m\|d\|X>` | screening: static, plasmon pole, MPA, dynamic | `em1s`, `ppa`, `mpa`, `em1d`, `Xx` |
| `-dipoles`, `-q` | dipoles | `dipoles` |
| `-kernel`, `-k <hartree\|alda\|lrc\|hf\|sex\|bsfxc>` | kernel (hf, sex only for BSE; lrc only in G space) | |
| `-Ksolver`, `-y <h\|d\|s\|pi\|fi>` | BSE solver: Haydock, diagonalisation, SLEPc (if built with it), inversion | `bss` |

| Control option | Meaning |
|---|---|
| `-F`, `-Input <file>` | input file |
| `-J`, `-Job <jobs>` | job names (below) |
| `-I`, `-Idir` / `-O`, `-Odir` / `-C`, `-Cdir` | folder with `SAVE` / folder for databases / folder for `r-`, `l-`, `o-` files |
| `-V`, `-Verbosity <RL,kpt,sc,qp,io,gen,resp,X,ph,rt,par,nl,all>` | show more variables when generating an input (one category per option) |
| `-E`, `-parenv <file>` | parallel variables from a file |
| `-nompi`, `-noopenmp` | no MPI, no OpenMP |
| `-Q`, `-Quiet` | do not open the editor |
| `-walltime DdHhMm`, `-memory <n>Gb` | stop cleanly before a time; memory per process |
| `-D`, `-DBlist` | describe the databases |

**Generating an input:** `yambo -F file <runlevel options>` writes `file` (or adds to it, keeping the values there, rounding G-vector cutoffs to closed shells: 1000 RL becomes 1009 RL) and opens an editor unless `-Q`. Examples: `-hf -gw0 p -dyson n` gives `HF_and_locXC gw0 ppa em1d`; a BSE: `yambo -o b -X p -k sex -y d -r -V par -F bse.in`. Yambo 4 used other letters (`-b` static screening, `-M` no MPI, `-N` no OpenMP, `-W` walltime).

### Job names and folders (`-J`)

`-J` takes up to 10 comma-separated names (`setup/SET_job_strings_and_dirs.F`). The first is the job written: its new databases go to `<Odir>/<job>/`, its `r-`, `l-` and `o-` files to the run folder (or `-C`). A name may have a path (`dir/job`). The others are read-only.

Databases are looked for in this order (`Yio/io_utilities.F`): `<Odir>/<job>`, `<Odir>/SAVE`, `<Idir>/<job>`, `<Idir>/SAVE`, then each further job. **`SAVE` comes before the further jobs**, so a stale `ndb.QP` or `ndb.em1s` left in `SAVE` hides the one in the second or third job.

### Output and how a run ends

- Report `r-<job>_<runlevels>` (`r_setup` without `-J`); logs `l-<job>_<runlevels>`, or with MPI `LOG/l-<job>_<runlevels>_CPU_<n>` (`NLogCPUs` limits how many processes write one; 0, the default, means all). Results `o-<job>.<what>`: `o-<job>.qp`, `o-<job>.eps_q1_diago_bse`, `o-<job>.eel_*`, `o-<job>.alpha_*`, `o-<job>.exc_*`. Existing files are not overwritten: a suffix `_01` is added (`interface/INIT_report_and_log_files.F`).
- A run that ends normally writes the report's last section, **`Game Over & Game summary`** (`stop_and_restart/CLOSE_the_run.F`).
- Errors are written as `[ERROR] <message>` to the report and log, followed by `MPI_Abort`: the exit code is not zero. Warnings: `[WARNING] <message>`.

**For the suite.** A Yambo run succeeded if its report ends with `Game Over & Game summary` and neither it nor `LOG/l-*` has `[ERROR]`. The job script can pass `-walltime` a little below the Slurm limit. Yambo reads `-walltime` as days, hours and minutes from the positions of `d`, `h` and `m` (`GET_user_WALL_time` in `src/modules/mod_LIVE_t.F`), a part only where the letters around it are there: the full form `0d11h50m` is the safe one. The time counts from Yambo's start; how often Yambo checks it is not documented, so the margin left for writing databases is a guess. A Yambo run line is `yambo -F <input> -J <jobs>` in the folder that contains `SAVE` (or a link to it).

### ypp and the other executables

- `ypp` (`driver/options_ypp.c`): `-e`, `-exciton <s|sp|a|w|i>` (sort, spin, amplitudes, wave functions, interpolate), `-b`, `-BSiq <n>` (exciton momentum), `-s`, `-electron <w|d|m|s|b|c>`, `-k`, `-grid`, `-qpdb <g|m|e>` (generate, merge, expand QP databases; `-q` in 4.x), `-dipoles`, `-y` (remove symmetries).
- Project builds: `yambo_rt`/`ypp_rt` (real time), `yambo_nl`/`ypp_nl` (non-linear), `yambo_ph`/`ypp_ph` (electron–phonon), `yambo_sc` (self-consistent), `yambo_qed`.

---

## 2. The input file

Sources: `parser/PARSER.c`, `PARSER_symbols.c`, `PARSER_convert_INT.F`, `PARSER_convert_REAL.F`, `interface/INIT_load.F`.

- One statement per line; `#` starts a comment (generated files write `# [TAG] description` after each value).
- A bare name on its own line switches a **runlevel** on (`optics`, `bse`, `bss`, `chi`, `em1s`, `em1d`, `ppa`, `mpa`, `Xx`, `cohsex`, `dipoles`, `HF_and_locXC`, `gw0`, `dyson`, `rim_cut`, `rim_w`, `setup`, `life`), or a logical **flag** (`WRbsWF`, `DegFix`). A commented-out flag (`#WRbsWF`) is off.
- Scalars: `Name = value [unit]`. Strings in double quotes: `Chimod= "Hartree"`. Some are normalised (`Chimod`, `BSKmod` to upper case; `BSEmod`, `DysSolver` to lower case).
- Blocks: values separated by `|`, one row per line:

  ```
  % BSEBands
    59 | 66 |
  %
  ```

  e.g. `QPkrange` (`k1|k2|b1|b2|`), `QPerange`, `BndsRnXp`, `GbndRnge`, `BSEEhEny`, `BEnRange`, `BSEQptR`.
- Names are case-insensitive. A block defined twice: the first wins. Values may be arithmetic expressions.
- Integers are read as numbers and rounded (`parse_int`: `rint`), so `4.000000` is 4.
- **Unknown names are ignored without a message**: the parser stores every name and the program reads only the ones it knows. A misspelt or Yambo 4 variable has no effect.
- Units: energies `eV`, `meV`, `Ha`, `mHa`, `Ry`, `mRy`, `K`, `GHz`, `THz`; times `fs`, `ps`, `as`; G vectors as a count `RL` or an energy cutoff, rounded to the closed shell that contains it.

**For the suite.** Since Yambo says nothing about unknown names, the checks warn about any variable Visual Workflows does not know for the runlevels present, and name the Yambo 5 replacement of Yambo 4 names (`X_all_q_CPU` → `X_and_IO_CPU`).

---

## 3. Parallelisation

Sources: `interface/INIT_load.F` (`CPU_structure_load`), `parallel/PARALLEL_get_user_structure.F`, `PARALLEL_global_defaults.F`, `PARALLEL_check_phase_space.F`, `PARALLEL_assign_LA_COMMs.F`; wiki "Using Yambo in parallel", "GW parallel strategies"; the paper.

### Variables

For each section `<S>`: `<S>_CPU` (processes per role), `<S>_ROLEs` (the roles), `<S>_nCPU_LinAlg_INV` and `<S>_nCPU_LinAlg_DIAGO` (DIAGO, not DIAG). Generated inputs show them only with `-V par` or `-V all`.

| Section | Roles (5.3) | Used by |
|---|---|---|
| `DIP` | k c v | dipoles |
| `X_and_IO` | q g k c v | screening for GW and BSE (em1s, em1d, ppa) |
| `X` | q g k c v | optics in G space (`-o c`) |
| `BS` | k eh t | BSE kernel and solver |
| `SE` | q g qp b (g since 5.3; q qp b before) | HF and GW self-energy |
| `RT` | k b q qp | real time |
| `SLK` | p d | ScaLAPACK |
| `NL` | w k | non-linear |
| `BZINDX` | k | Brillouin-zone indexes (5.1+) |
| `PH_SE` | q k b | phonon self-energy (5.0+) |
| `OSCLL` | k b | oscillators (5.3) |

Yambo 4 had other sections: `X_q_0`, `X_finite_q` and `X_all_q` (4.0–4.4), then `DIP`, `X_finite_q`, `X` (4.5). `X_and_IO` exists since 5.0. The wiki's tutorials still show Yambo 4 names.

OpenMP: `K_Threads` (BSE), `X_Threads`, `DIP_Threads`, `SE_Threads`, `RT_Threads`, `NL_Threads`, `OSCLL_Threads`. 0 or unset uses `OMP_NUM_THREADS`; a positive value overrides it for that section; `-noopenmp` forces 1.

`PAR_def_mode`: `"balanced"` (default), `"memory"`, `"workload"`, `"KQmemory"` (5.2+). `NLogCPUs`: processes writing logs.

### Rules

- In `_CPU` and `_ROLEs`, dots, commas and spaces all separate: `"1 2 4"`, `"1.2.4"`, `"1,2,4"`. Role order does not matter. An empty string means the defaults.
- **The product of `<S>_CPU` must equal the MPI processes.** Otherwise (or when empty) Yambo writes `<ENVIRONMENT> parallel ENVIRONMENT is incomplete. Switching to defaults` to the log and uses its own distribution: not an error, the settings are simply ignored. The exception, `BZ_Indexes`, warns "Assuming expert user".
- **A role with more processes than it has elements** (more processes on q than q-points, on c than conduction bands, and so on) leaves a process empty: `NULL parallel world for CPU#n`, then `[ERROR] USER parallel structure does not fit the current run parameters`, fatal. Lifetimes with q > 1 stop the same way.
- Defaults spread the processes' prime factors over: dipoles v, c, k; response c, v, k, q, g; self-energy b, qp, g, q; BSE eh, k (never t); real time k, b, qp, q; ScaLAPACK p = all, d = 1. If none fits: `[ERROR] Impossible to define an appropriate parallel structure`.
- Environment variables `YAMBO_<S>_CPU`, `YAMBO_<S>_ROLEs` (dot separators) are read only when the input's string is empty.
- Linear algebra: −1 is automatic (with ScaLAPACK: 1 below 8 processes, 4 from 8, 16 from 32, 36 from 64); below 4 means serial; the value is capped by the communicator; the grid is **square**, floor(√n)² processes (8 gives 2 × 2). A failed grid: `[WARNING] SLK setup failed ... Switching to standard LK`. ScaLAPACK needs a build with `--enable-par-linalg`, SLEPc `--enable-slepc-linalg`. ELPA is not in the official 5.3.
- The log shows the distribution: `[PARALLEL <section> for <role> on N CPU] Loaded/Total`.

### Advice

- **Response (q g k c v):** k, c and v first: near-linear and they divide the memory (wave functions dominate it); q duplicates memory and balances poorly; g communicates most, for very large G sets (layers, surfaces). The c and v counts should be multiples of their processes.
- **Self-energy (q qp b):** b divides memory and work evenly; qp divides work but copies the wave functions; q balances poorly. `SE_Threads` costs no memory. Example: `SE_ROLEs="q.qp.b"`, `SE_CPU="1.4.15"`, `SE_Threads=8` on 60 processes × 8 threads.
- **BSE (k eh t):** the transitions form N_t = n_eh × n_k groups; the kernel has N_t(N_t+1)/2 blocks with the Tamm–Dancoff approximation (N_t² otherwise); k × eh make the process groups and t divides the blocks within each. Avoid eh > 1 when the irreducible and full k-points differ (symmetric elements computed twice). The kernel scales well, the solver less: build the kernel on many processes, solve on fewer.
- MPI × threads = cores; threads do not cross nodes; above about 100 cores, mix MPI and OpenMP; short of memory, fewer processes and more threads. One process per GPU.

**For the suite.** The Phase 2 checks for a yambo run with P MPI processes and T threads, for each section the runlevels use:

- errors: an unknown role for the section; as many factors as roles not given; a role with more processes than its size when the size is known from the input (c and v from `BndsRnX*`/`BSEBands` and the occupied bands when known, qp from `QPkrange`, b from `GbndRnge`; k and q wait for Phase 3);
- warnings: a product different from P (Yambo will ignore the settings); `*_Threads` different from T; a linear-algebra count that is not a square or is below 4 (serial); Yambo 4 names; eh > 1 in `BS_CPU` (when symmetries reduce k).

---

## 4. BSE

Sources: help strings in `interface/INIT_load.F`; `io/io_BSS_diago.F`, `io_parallel/io_BS_header.F`, `io_serial/io_BS.F`, `interface/INIT_QP_ctl_load.F`; release notes 5.3.

**Variables**

- `BSEmod`: resonant, retarded, coupling. `BSKmod`: IP, Hartree, HF, ALDA, SEX, BSfxc. `Lkind`: bar (default), full, tilde (shown with `-V resp`).
- `BSSmod`: `h` Haydock, `d` diagonalisation, `s` SLEPc, `i` inversion, `t` TDDFT.
- `BSEBands`; `BSENGBlk` (screening block; −1 all of it); `BSENGexx` (exchange); `BSEEhEny` (transition energy window); `BSEQptR` (range of transferred momenta, by index); `BSKIOmode`.
- Haydock: `BSHayTrs`, `BSHayItrMAX`. SLEPc: `BSSNEig` (number of states; about 10 % of the kernel's size to start), `BSSEnTarget` (energy target), `BSSSlepcMaxIt`, `BSSSlepcApproach`, `BSSSlepcMatrixFormat` (`shell`: Yambo's distributed matrix, slower; `explicit`: faster, more memory).
- **`WRbsWF`**: flag to write the excitons' eigenvectors, needed by `ypp -e a|w` and by Visual Excitons. It works with `d` and `s`, not with Haydock, and is written commented out in generated inputs.
- QP corrections: `KfnQPdb= "E < GW/ndb.QP"` (actions E, W, Z; path relative to the run folder). A scissor instead: `KfnQP_E= shift | c-stretch | v-stretch |`.
- Coulomb cutoff: runlevel `rim_cut`, `RandQpts`, `RandGvec`, `CUTGeo` (`box`, `cylinder`, `sphere`, `ws`, `slab z` since 5.1), giving `ndb.RIM` and `ndb.cutoff`.
- 5.3: the kernel is split into exchange, ALDA and correlation with their own cutoffs; a SLEPc solver for the coupling case; SLEPc on GPUs; Lbar/Lfull fixes for low-dimensional systems.

**Databases** (in the job folder):

- Kernel: with parallel HDF5 I/O, `ndb.BS_head_Q<iq>` and `ndb.BS_PAR_Q<iq>` (reusable with another distribution; a partial one restarts the kernel); without, `ndb.BS_Q<iq>_CPU_<n>` per process (reusable only with the same distribution).
- Solver: **`ndb.BS_diago_Q<iq>`** (and `_ares` for an anti-resonant part). Variables (dimensions in Fortran order, reversed in Python): `BS_Energies` (2, n states), `BS_left_Residuals`, `BS_right_Residuals`, `BS_TABLE` (dimension, 5: k, v, c, spins; 5.0+), `BS_EIGENSTATES` (2, dimension, n states) only with eigenvectors written.
- `iq` is the transferred momentum's index in Yambo's irreducible list; Q1 is the optical limit. *(Not verified: details of the finite-Q indexing.)*

**For the suite.** Visual Excitons reads only `ndb.BS_diago_Q*` with `BS_EIGENSTATES`: a BSE meant for it uses `BSSmod` `d` or `s` and `WRbsWF`. Haydock gives spectra but nothing for Visual Excitons. A dispersion needs every irreducible Q (`BSEQptR` over all of them).

---

## 5. GW

Sources: `interface/INIT_load.F`, `setup/QP_state_table_setup.F`, `io/io_QP_and_GF.F`, `Yio/io_fragment.F`; release notes 4.2, 5.2.

- `EXXRLvcs` (exchange), `VXCRLvcs` (Vxc), `GbndRnge` (bands in the Green's function), `GTermKind` (none, BG, BRS), `DysSolver` (n, s, g), `GDamping`, `QPkrange` (`k1|k2|b1|b2|`; all k and bands when absent), `QPerange`.
- Response variables: a stem and a suffix for the kind, `Xx` optics, `Xs` em1s, `Xd` em1d, `Xp` plasmon pole, `Xm` MPA: `BndsRnXp`, `NGsBlkXp`, `PPAPntXp`, `LongDrXp`, `NGsBlkXs`. `Chimod`: IP, Hartree, ALDA, LRC, PF, BSfxc.
- evGW was removed from the code in 5.2 (chain runs instead).
- Databases: `ndb.dipoles`, `ndb.HF_and_locXC`, `ndb.em1s`, `ndb.em1d`, `ndb.pp` (a header plus a `_fragment_<iq>` per q), **`ndb.QP`** (`QP_kpts`, `QP_table`, `QP_E`, `QP_Eo`, `QP_Z`; older files `QP_E_Eo_Z`), text `o-<job>.qp`.
- A large GW can be split by k-points or bands and the QP databases merged: `ypp -qpdb m` (5.x), `ypp -q m` (4.x).

**For the suite.** For a BSE with GW in Visual Excitons, the QP run covers every irreducible k-point of the same `SAVE` and the BSE's bands (`QPkrange`). Visual Excitons reads split databases (`ndb.QP_140_146`), YamboPy does not.

---

## 6. The chain from QE

From the tutorials and the new wiki:

1. pw.x scf, with `force_symmorphic=.true.`;
2. pw.x nscf with the bands Yambo needs (`diago_full_acc=.true.`);
3. `cd <prefix>.save; p2y` → `SAVE/`;
4. `SAVE` moved or linked to Yambo's folder; `yambo` → setup (`SAVE/ndb.gops`, `SAVE/ndb.kindx`, `r_setup`);
5. GW: `yambo -F gw.in -dyson n -gw0 p -r -V par` to generate, `yambo -F gw.in -J GW` to run → `GW/ndb.QP`, `ndb.pp*`, `ndb.HF_and_locXC`, `ndb.dipoles`, `ndb.RIM`, `ndb.cutoff`;
6. BSE kernel: `yambo -F bse.in -J BSE,GW` with `KfnQPdb="E < GW/ndb.QP"`, reusing the screening and dipoles of GW → `BSE/ndb.BS_head_Q1`, `BSE/ndb.BS_PAR_Q1`;
7. solver: `bss` and `BSSmod` added, `yambo -F bse.in -J diago,BSE,GW` → `diago/ndb.BS_diago_Q1`, `o-diago.eps_q1_*`;
8. `ypp -e s -J diago,BSE,GW` and so on.

Separate stages in separate folders are joined with links: `ln -s ../01/SAVE`, `ln -s ../01/GW`, `ln -s ../01/BSE` (MoS₂ tutorial).

**For the suite.** A Visual Workflows step running Yambo links `SAVE` from the p2y step, and its earlier steps' job folders it reads through `-J` (`links` in the step's `[job]`). Setup can be the first run of the p2y step, so `ndb.gops` and `ndb.kindx` are made once. The p2y step runs `p2y -I <prefix.save of the nscf> -O .` (`-I` is where QE's files are read, `-O` where `SAVE` is made: `interfaces/p2y/mod_p2y.F`, `Yio/IO_make_directories.F`; to be tried on a cluster), so that `SAVE` is at the top of its folder rather than inside `prefix.save`: Visual Excitons finds it there when the project folder is opened (it looks at most 3 levels down).

---

## 7. Pitfalls

1. **Serial numbers:** each p2y run gives `ns.db1` a new random serial number, stored in every database. Dipoles, BSE kernels and others with another number are refused (`[WARNING] Incompatible serial number for <file>`) and computed again or ignored. Running p2y again makes the old databases useless.
2. **Search order:** `SAVE` comes before the further `-J` jobs; keep `SAVE` free of `ndb.QP`, `ndb.em1s`, `ndb.pp` and the like.
3. **Parallel settings that do not match the job** are dropped silently (§3); roles larger than their sizes are fatal.
4. **Spin:** databases with another spin setting are refused (except screening without spin–orbit used with it, 5.1+). Databases older than certain revisions are "no longer supported".
5. **netCDF/HDF5:** 5.x writes netCDF-4/HDF5 unless built for classic netCDF (2 GB per file). In 5.3 parallel HDF5 I/O is on by default when the HDF5 found is parallel; without it the BSE kernel is written per process and cannot be reused with another distribution.
6. **Versions:** `X_and_IO_*` (5.x) against `X_all_q_*`, `X_finite_q_*`, `X_q_0_*` (4.x); `g` in `SE_ROLEs` only in 5.3; `KQmemory` from 5.2; command-line letters changed in 5.0.
7. p2y stops on non-symmorphic symmetries (`force_symmorphic=.true.` in QE).
8. `-walltime` lets Yambo stop cleanly before the Slurm limit.

*Not verified from the source or the documentation:* the finite-Q indexing of `ndb.BS_diago_Q*` beyond "the irreducible q list", the meaning of `BS_TABLE`'s columns 4 and 5, `DysSolver="q"`, and whether `-Q` is needed in batch jobs.
