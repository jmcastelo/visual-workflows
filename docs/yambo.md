# Yambo for the suite

[← Documentation](../README.md#documentation)

What the Visual suite needs to know about Yambo to write job scripts, check parallelisation, build inputs and read the databases Visual Excitons uses: the executables and their command lines, the input grammar, the parallel variables and their rules, BSE and GW settings, the databases, the usual chain from QE, and pitfalls. It is a digest of Yambo's source code and documentation, for developers; each section names its sources. What this means for Visual Workflows is set apart in **For the suite** paragraphs. The QE side of the chain is in [Quantum ESPRESSO](quantum-espresso.md).

Versions: most of this was read in the source of Yambo 5.3 (`master` at 19c1241, October 2025, "5.3.0 r.23959"), with key files compared with the tags from 4.0.4 to 5.2.4. The repository's `ChangeLog` is empty; the release notes are the change log. On 2026-11-21 the walltime stop, p2y's `-I`/`-O`, the parallel variables and how a run ends were checked again in the source at the tags 5.1.1 and 5.3.0 (the versions on Lynx and Tirant); differences between them are noted. On the same day, input handling at startup and the QE → Yambo chain were read at 5.3.0, and 5.4-beta compared with it: `-walltime`, how a run ends, the `-J` search order, p2y and the command-line options are unchanged; its changes are noted where they matter. Statements marked *(not verified)* could not be confirmed from the source or the official documentation.

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

Options (5.x): `-I` input folder, `-O` output folder, `-C`, `-J`, `-nompi`, `-nofrag`/`-U` (no fragments, serial), `-fragbands`/`-b <n>`, `-alat_factor`/`-a`, `-notr`/`-t`, `-nosym`/`-n`, `-nowf`/`-w` (no wave functions), `-verbio`/`-v`. p2y runs under MPI since 4.3 (wave-function I/O split over band groups and k-points). **p2y has no `-F` and no `-walltime`**; an unknown option ends any Yambo executable with ` Use -h to list the options`, exit code 1 (`driver/command_line.c`).

**It stops on symmetries with fractional translations:** `Non-symmorphic symmetry operations are not supported! Use force_symmorphic=.true. in PWSCF` (`interfaces/p2y/mod_p2y.F`). A format it does not recognise: `QE format not recognised/compatible (was wf_collect set to true?)`, also when `-I` has no `wfc1.dat` (or `wfcup1.dat`, or `.hdf5`), as after an nscf with `disk_io='nowf'`; detection builds file names in 256 characters, so a long `-I` path fails it. It also stops on DFT+U (`LDA+U. Hubbard correction is not considered in yambo.`), on hybrids other than HF, PBE0, B3LYP and GAUPBE, on ultrasoft pseudopotentials (`[PPs] Ultrasoft PP not supported`, unless built with `_USPP`, and then only setup, RIM and HF work) and PAW (`PAW not implemented`). HDF5 wave functions need p2y built with QE-HDF5. Each run gives `ns.db1` a new random serial number (§7).

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
- A run that ends normally writes the report's last section, **`[NN] Game Over & Game summary`** (`stop_and_restart/CLOSE_the_run.F`), then exits with 0. A stop by `-walltime` or `STOP_NOW` writes it too (see below).
- Errors are written as `[ERROR] STOP signal received while in <section>` and `[ERROR] <message>` to the report and log (`communicate/com_utilities_subroutines.F`), followed by `MPI_Abort(…, 1)` **only with more than one MPI process**: a serial run that fails exits with 0. Only the master process writes the report: an error raised on another process is only in its `LOG/l-*_CPU_<n>`. Warnings: `[WARNING] <message>`.

### `-walltime` (checked in 5.1.1 and 5.3.0)

- **Where:** only `STOP_now` (`stop_and_restart/STOP_now.F`) compares the time with the limit, and the standard `yambo` calls it only from the **Haydock** solver (once per iteration, `bse/K_Haydock.F`) and the **inversion** solver (once per frequency, `bse/K_inversion_driver.F`), besides the real-time, non-linear and self-consistent builds. Screening, the BSE kernel, diagonalisation, SLEPc and GW never look at it.
- **How:** the elapsed time is split into days, hours (0–23) and minutes (0–59), and **each is compared with its own limit**, a 0 meaning "any": `days >= D and hours >= H and minutes >= M`. So `11h50m` matches only in minutes 50–59 of each hour (a check at 12:05 does not stop the run), and `26h` never matches (hours never pass 23). Safe forms: `11h`, `1d2h`, `2d`, minutes only below an hour. A bare number sets no limit (`GET_user_WALL_time` in `modules/mod_LIVE_t.F` reads the numbers before `d`, `h`, `m`).
- **Clock:** from Yambo's start; wall time in builds with OpenMP (`gettimeofday`), otherwise Fortran `cpu_time` (`tools/ct_cptimer.c`).
- **At the limit:** `[WARNING] USER STOP signal`, the timing overview and `Game Over & Game summary`, then `MPI_Abort(…, 1)` (5.3.0: with more than one process). No database is written then: Haydock restarts from `ndb.Haydock`, written every `BSHayItrIO` iterations (default 100) and at convergence; the `o-` spectra are rewritten every `BSHayItrIO/10`.
- **After a kill by Slurm** (what matters for the other runlevels): screening resumes at the first q-point not in its database (`pol_function/X_dielectric_matrix.F`); the BSE kernel, with parallel I/O, resumes from a partial `ndb.BS_PAR_Q*` synced every 5 % of its blocks (`Partial BSE file found in … used as restart.`); GW: no restart found *(not searched thoroughly)*.

**For the suite.** A Yambo run succeeded if its report ends with `Game Over & Game summary`, neither it nor `LOG/l-*` has `[ERROR]`, and it has no `USER STOP signal`, no `Bethe Salpter section skipped` and no `I/O problems in reading QP database` (both end normally); the exit code is no proof (a serial run that fails exits with 0). Job scripts pass `-walltime` with the time left when the run starts, less 5 minutes, rounded down to whole hours (`11h`, `1d2h`; minutes only in the last hour), which helps the Haydock and inversion solvers only; the other runlevels rely on the restarts above. A Yambo run line is `yambo -F <input> -J <jobs>` in the folder that contains `SAVE` (or a link to it).

### ypp and the other executables

- `ypp` (`driver/options_ypp.c`): `-e`, `-exciton <s|sp|a|w|i>` (sort, spin, amplitudes, wave functions, interpolate), `-b`, `-BSiq <n>` (exciton momentum), `-s`, `-electron <w|d|m|s|b|c>`, `-k`, `-grid`, `-qpdb <g|m|e>` (generate, merge, expand QP databases; `-q` in 4.x), `-dipoles`, `-y` (remove symmetries).
- Project builds: `yambo_rt`/`ypp_rt` (real time), `yambo_nl`/`ypp_nl` (non-linear), `yambo_ph`/`ypp_ph` (electron–phonon), `yambo_sc` (self-consistent), `yambo_qed`.

---

## 2. The input file

Sources: `parser/PARSER.c`, `PARSER_symbols.c`, `PARSER_convert_INT.F`, `PARSER_convert_REAL.F`, `interface/INIT_load.F`.

- One statement per line; `#` starts a comment, **even inside a quoted string** (`parse_get_line` in `PARSER.c`; generated files write `# [TAG] description` after each value).
- A bare name on its own line switches a **runlevel** on (`optics`, `bse`, `bss`, `chi`, `em1s`, `em1d`, `ppa`, `mpa`, `Xx`, `cohsex`, `dipoles`, `HF_and_locXC`, `gw0`, `dyson`, `rim_cut`, `rim_w`, `setup`, `life`), or a logical **flag** (`WRbsWF`, `DegFix`). A commented-out flag (`#WRbsWF`) is off.
- Scalars: `Name = value [unit]`. Strings in double or single quotes: `Chimod= "Hartree"`. **An unquoted string** (`BSKmod= SEX`, `KfnQPdb= E < GW/ndb.QP`), or a value of the wrong type (`BSENGBlk= "2"`), **silently leaves the default** (the line is read as an expression). Some are normalised (`Chimod`, `BSKmod` to upper case; `BSEmod`, `DysSolver` to lower case).
- Blocks: values separated by `|`, one row per line:

  ```
  % BSEBands
    59 | 66 |
  %
  ```

  e.g. `QPkrange` (`k1|k2|b1|b2|`), `QPerange`, `BndsRnXp`, `GbndRnge`, `BSEEhEny`, `BEnRange`, `BSEQptR`.
- Names are case-insensitive for values and runlevels (`strcasecmp`, `PARSER_symbols.c`), **but a unit is found by searching the raw lines for Yambo's own spelling of the name**, on the last line containing it (`output/OUTPUT_unit_conversion.F`): `bsengexx= 30 Ry` gives 30 G vectors, not 30 Ry. A name that is part of a later one (`RandGvec`, `RandGvecW`) takes that line's unit. A block defined twice: the first wins; a scalar defined twice: the last wins. Values may be arithmetic expressions. A block row with missing columns keeps the defaults for them; extra columns are ignored. A block not closed takes the rest of the file.
- Integers are read as numbers and rounded (`parse_int`: `rint`), so `4.000000` is 4.
- **Unknown names are ignored without a message**: the parser stores every name and the program reads only the ones it knows. A misspelt or Yambo 4 variable has no effect.
- Units, spelt exactly so (or all lowercase): energies `eV`, `meV`, `Ha`, `mHa`, `Ry`, `mRy`, `K`, `GHz`, `THz`; memory `Gb`, `Mb`; times `fs`, `ps`, `as`; G vectors as a count `RL` or an energy cutoff, rounded to the closed shell that contains it. Other spellings (`RY`, `EV`) or no unit leave the value in internal units: Ha for energies, **a number of G vectors for cutoffs**. A capital `K` after an energy converts it as kelvin.
- **What runs.** No runlevel recognised (all misspelt), or a `-F` file that does not exist: yambo runs the **setup** and ends normally. GW runlevels (`gw0`, with `ppa`, `mpa` or `cohsex`) together with `bse`, `bss` or `optics`: the BSE ones are dropped silently (`INIT_barriers.F`). `bse` turns off `HF_and_locXC`, `chi` and `Xx`. `bse` without `optics` or `bss` runs no BSE. Errors are only written when the run reaches that part, often after dipoles, screening and HF.

**For the suite.** Since Yambo says nothing about unknown names, the checks warn about any variable Visual Workflows does not know for the runlevels present, and name the Yambo 5 replacement of Yambo 4 names (`X_all_q_CPU` → `X_and_IO_CPU`). They also catch what Yambo would silently change: unquoted strings, names spelt in another case with a unit, unknown unit spellings, cutoffs without a unit, blocks with missing columns, GW and BSE runlevels together, runlevel options in the job's yambo arguments (they make yambo rewrite the input and exit without computing).

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
| `SE` | q g qp b (g since 5.3, dropped for self-consistent and real-time runs; q qp b before) | HF and GW self-energy |
| `RT` | k b q qp | real time |
| `SLK` | p d | ScaLAPACK |
| `NL` | w k | non-linear |
| `BZINDX` | k | Brillouin-zone indexes (5.1+) |
| `PH_SE` | q k b | phonon self-energy (5.0+) |
| `OSCLL` | k b | oscillators (5.3) |

Which linear-algebra counts are read at all: `X_and_IO_nCPU_LinAlg_INV` and `X_nCPU_LinAlg_INV` (`PARALLEL_global_Response_G.F`), `BS_nCPU_LinAlg_DIAGO` and `BS_nCPU_LinAlg_INV` (`bse/K_solvers.F`), `SLK_nCPU_LinAlg_INV`, and `SE_nCPU_LinAlg_DIAGO` (self-consistent runs only). The `DIP_*`, `X*_DIAGO` and `SE_*_INV` ones are never read.

Yambo 4 had other sections: `X_q_0`, `X_finite_q` and `X_all_q` (4.0–4.4), then `DIP`, `X_finite_q`, `X` (4.5). `X_and_IO` exists since 5.0. The wiki's tutorials still show Yambo 4 names.

OpenMP: `K_Threads` (BSE), `X_Threads`, `DIP_Threads`, `SE_Threads`, `RT_Threads`, `NL_Threads`, `OSCLL_Threads`. 0 or unset uses `OMP_NUM_THREADS`; a positive value overrides it for that section; `-noopenmp` forces 1.

`PAR_def_mode`: `"balanced"` (default), `"memory"`, `"workload"`, `"KQmemory"` (5.2+). `NLogCPUs`: processes writing logs.

### Rules

- In `_CPU` and `_ROLEs`, dots, commas and spaces all separate: `"1 2 4"`, `"1.2.4"`, `"1,2,4"` (`PARALLEL_get_user_structure.F`). Role order does not matter. An empty string means the defaults; with one MPI process the strings are ignored. The number of `_CPU` entries sets the number of roles: missing roles are left blank, extra ones ignored. Each count is read as `i3`: **at most 999 processes per role**. **Unknown roles are not rejected**: they count in the product and are matched to no work.
- **The product of `<S>_CPU` must equal the MPI processes.** Otherwise (or when empty) Yambo writes `<Long name> parallel ENVIRONMENT is incomplete. Switching to defaults` to the **log only** (not the report) and uses its own distribution: not an error, the settings are simply ignored. Long names: `Response_G_space_and_IO`, `Response_T_space`, `Self_Energy`, and so on. The exception, `BZ_Indexes`, warns "Assuming expert user".
- **A role with more processes than it has elements** (more processes on q than q-points, on c than conduction bands, and so on) leaves a process empty: `NULL parallel world for CPU#n`, then `[ERROR] USER parallel structure does not fit the current run parameters. <n> CPU for <m> elements (ROLE is 'c')`, fatal (`PARALLEL_check_phase_space.F`). **The `eh` role is exempt.** Lifetimes with q > 1 stop the same way.
- Defaults spread the processes' prime factors over: dipoles v, c, k; response c, v, k, q, g (5.1.1: g, v, c, k, q); self-energy b, qp, g, q (5.1.1: b, qp, q); BSE eh, k (never t); non-linear k, w (5.1.1: w, k); real time k, b, qp, q; ScaLAPACK p = all, d = 1. If none fits: `[ERROR] Impossible to define an appropriate parallel structure`.
- Environment variables `YAMBO_<S>_CPU`, `YAMBO_<S>_ROLEs` (dot separators) are read only when the input's string is empty.
- Linear algebra: −1 is automatic (with ScaLAPACK, 5.3.0: 1 below 8 processes, 4 from 8, 16 from 32, 36 from 64; 5.1.1: 4 from 4, 16 from 16, 36 from 36); 0 and anything below 4 mean serial; the value is capped by the communicator; the grid is **square**, floor(√n)² processes (8 gives 2 × 2). A failed grid: `[WARNING] SLK setup failed ... Switching to standard LK`. ScaLAPACK needs a build with `--enable-par-linalg`, SLEPc `--enable-slepc-linalg`. ELPA is not in the official 5.3.
- The log shows the distribution: `[PARALLEL <section> for <role> on N CPU] Loaded/Total`.

### Advice

- **Response (q g k c v):** k, c and v first: near-linear and they divide the memory (wave functions dominate it); q duplicates memory and balances poorly; g communicates most, for very large G sets (layers, surfaces). The c and v counts should be multiples of their processes.
- **Self-energy (q qp b):** b divides memory and work evenly; qp divides work but copies the wave functions; q balances poorly. `SE_Threads` costs no memory. Example: `SE_ROLEs="q.qp.b"`, `SE_CPU="1.4.15"`, `SE_Threads=8` on 60 processes × 8 threads.
- **BSE (k eh t):** the transitions form N_t = n_eh × n_k groups; the kernel has N_t(N_t+1)/2 blocks with the Tamm–Dancoff approximation (N_t² otherwise); k × eh make the process groups and t divides the blocks within each. Avoid eh > 1 when the irreducible and full k-points differ (symmetric elements computed twice). The kernel scales well, the solver less: build the kernel on many processes, solve on fewer.
- MPI × threads = cores; threads do not cross nodes; above about 100 cores, mix MPI and OpenMP; short of memory, fewer processes and more threads. One process per GPU.

**For the suite.** The Phase 2 checks for a yambo run with P MPI processes and T threads, for each section the runlevels use:

- errors: an unknown role for the section (Yambo would accept it and give it no work); as many factors as roles not given; a count above 999; a role other than `eh` with more processes than its size when the size is known from the input (c and v from `BndsRnX*`/`BSEBands` and the occupied bands when known, qp from `QPkrange`, b from `GbndRnge`; k and q wait for Phase 3);
- warnings: a product different from P (Yambo will ignore the settings, saying so only in the log); `*_Threads` different from T; a linear-algebra count that is not a square or is below 4 (serial), or one Yambo never reads (`DIP_*`, `X*_DIAGO`, `SE_*_INV`); Yambo 4 names; eh > 1 in `BS_CPU` (when symmetries reduce k).

---

## 4. BSE

Sources: help strings in `interface/INIT_load.F`; `io/io_BSS_diago.F`, `io_parallel/io_BS_header.F`, `io_serial/io_BS.F`, `interface/INIT_QP_ctl_load.F`; release notes 5.3.

**Variables**

- `BSEmod`: resonant, retarded, coupling. `BSKmod`: IP, Hartree, HF, ALDA, SEX, BSfxc (an unknown value: an IP-like kernel, no message). `Lkind`: 5.3 takes values containing `bar` or `full` (`tilde`, though in the help, stops with `set Lkind = DEFAULT or BAR or FULL`); 5.1.1 takes exactly `Lbar` or `full`.
- `BSSmod`: `h` Haydock, `d` diagonalisation, `s` SLEPc, `i` inversion, `t` TDDFT; 5.4 adds `x` (heevx), `c` (ChASE). **The letters are found as substrings** (case-insensitive in 5.3, case-sensitive in 5.1.1): `"haydock"` runs Haydock and diagonalisation. No `BSSmod`, or none of its letters: the kernel only, no solver, no message. `s` in a Yambo built without SLEPc does nothing.
- `BSEBands`; `BSENGBlk` (screening block; −1 all of it); `BSENGexx` (exchange); `BSEEhEny` (transition energy window); `BSEQptR` (range of transferred momenta, by index); `BSKIOmode`.
- Haydock: `BSHayTrs`, `BSHayItrMAX`. SLEPc: `BSSNEig` (number of states; about 10 % of the kernel's size to start; **unset or 0, yambo computes 1 % of them**, and more than the kernel's size also gives 1 %, both with a warning in the log only: `bse/K_diago_driver.F`, 5.2.4), `BSSEnTarget` (energy target), `BSSSlepcMaxIt`, `BSSSlepcApproach`, `BSSSlepcMatrixFormat` (`shell`: Yambo's distributed matrix, slower; `explicit`: faster, more memory).
- **`WRbsWF`**: flag to write the excitons' eigenvectors, needed by `ypp -e a|w` and by Visual Excitons. It works with `d` and `s`, not with Haydock, and is written commented out in generated inputs.
- QP corrections: `KfnQPdb= "E < GW/ndb.QP"` (actions E, W, Z, in the three words before `<`, which must be a word of its own; path relative to the run folder). **A missing or unreadable QP database is only a warning** (`I/O problems in reading QP database`): the BSE runs on DFT energies. Bands the QP database does not cover get a fitted correction; other k-points are interpolated: no error. A scissor instead: `KfnQP_E= shift | c-stretch | v-stretch |`: a conduction state's energy E gets shift + (c-stretch − 1)(E − CBM) and a valence state's (v-stretch − 1)(E − VBM), CBM and VBM the DFT band edges; the valence top never moves (`mod_QP_CTL.F`, `QP_eval_the_FIT.F`, 5.2.4). `QP_fit_energies.F` fits a QP database's states the same way, for the bands it lacks.
- Coulomb cutoff: runlevel `rim_cut`, `RandQpts`, `RandGvec`, `CUTGeo` (`box`, `cylinder`, `sphere`, `ws`, `slab z` since 5.1), giving `ndb.RIM` and `ndb.cutoff`. **The slab cutoff works in cartesian coordinates** (`coulomb/cutoff_slab.F`, 5.2.4): `slab z` cuts along cartesian z, at half the cell's length along it; the direction's letter is read in either case. `setup/check_periodic_directions.F` wants each cell vector wholly along the cut direction or wholly across it (to 1e-5 bohr); otherwise it only warns, `Non periodic geometry and unit cell are incompatible`, and runs on. Yambo's 2D tutorials (hBN, MoS₂) use `RandQpts = 1000000`, `RandGvec = 100 RL`.
- RIM-W: runlevel `rim_w` (5.2 and later; 5.1 had an `RIM_W` flag instead), `RandGvecW` (the tutorial's 15 RL; 0 in a BSE takes `RandGvec`'s), `rimw_type` (read but used by no version up to 5.4: metals and graphene take the `RIM_W_graph` flag). It integrates the screened interaction W around each q-point, in GW (`QP_interpolate_W.F`) and in the BSE kernel (`K.F`). Slab cutoffs only (`RIM-W without cutoff slab has not been implemented`), a 2D q-grid, semiconductors (`RIM-W works only for 2D semiconductors with slab cutoff`, a warning); its G-vectors, shifted by an in-plane reciprocal vector where a neighbouring q-point is folded back, must lie within the screening's (`NGsBlkXp`, `NGsBlkXs`), or it stops: `G_max 107 for RIM-W is higher than G_max 87 of vX` on an hBN layer (a = 2.504 Å, 20 Å of cell) with 3 Ry, whose 87 vectors are the closed shell at 3.04 Ry; RIM-W's 15 reach the shell closing at 113, 3.705 Ry. Shells group G-vectors of equal length to 1e-5 (`G_shells_finder.F`), and a cutoff in Ry is rounded up to the shell holding it. On Yambo's monolayer MoS₂ GW tutorial: gap 2.48 eV without a cutoff, 4.12 eV with the cutoff alone, 2.81 eV with RIM-W.
- The field's direction, `LongDrX*` and `BLongDir`, defaults to cartesian x (1, 0, 0): for a layer it must lie in the plane (the MoS₂ BSE tutorial: `BLongDir = 1 | 1 | 0`).
- 5.3: the kernel is split into exchange, ALDA and correlation with their own cutoffs; a SLEPc solver for the coupling case; SLEPc on GPUs; Lbar/Lfull fixes for low-dimensional systems.

**Databases** (in the job folder):

- Kernel: with parallel HDF5 I/O, `ndb.BS_head_Q<iq>` and `ndb.BS_PAR_Q<iq>` (reusable with another distribution; a partial one restarts the kernel); without, `ndb.BS_Q<iq>_CPU_<n>` per process (reusable only with the same distribution).
- Solver: **`ndb.BS_diago_Q<iq>`** (and `_ares` for an anti-resonant part). Variables (dimensions in Fortran order, reversed in Python): `BS_Energies` (2, n states), `BS_left_Residuals`, `BS_right_Residuals`, `BS_TABLE` (dimension, 5: k, v, c, spins; 5.0+), `BS_EIGENSTATES` (2, dimension, n states) only with eigenvectors written.
- `iq` is the transferred momentum's index in Yambo's irreducible list; Q1 is the optical limit. *(Not verified: details of the finite-Q indexing.)*

- A SEX or HF kernel without a usable `ndb.em1s` or `ndb.pp` (not computed, not found through `-J`, incompatible) ends with `[WARNING] Bethe Salpter section skipped. Impossible to build the kernel.` and still reaches `Game Over`.
- Bands beyond the SAVE: `GbndRnge` stops the run only after dipoles, screening and HF (`Too many bands required in Green's function`); `BndsRnX*` is lowered silently; `QPkrange` is clamped silently; for `BSEBands` no check was found. `BSEBands` without a valence or conduction band: `Null BSE kernel block dimension(s) found.`
- `ns.db1` holds `DIMENSIONS` (Python, 0-based: 5 bands, 6 irreducible k-points, 7 G vectors, 10 symmetries, 11 spinors, 12 spin polarisations, 14 electrons), `EIGENVALUES`, `K-POINTS`, `SYMMETRY` (`io_DB1.F`); occupied bands of an insulator: electrons / (2 / spinors).
- 5.4: the `SX` parallel section (`SX_CPU`, `SX_ROLEs`, `SX_Threads`; roles q g qp b) for `HF_and_locXC`, which no longer reads `SE_*`; `BSSSlepcMatrixFormat` other than `shell` or `explicit` stops at the BSE's start; `BSKIOmode` adds `write_all_steps` and `read_on_the_fly`; a solver-only run that cannot load the kernel stops (5.3 returned silently).

**For the suite.** Visual Excitons reads only `ndb.BS_diago_Q*` with `BS_EIGENSTATES`: a BSE meant for it uses `BSSmod` `d` or `s` and `WRbsWF`. The BSE's size is valence × conduction bands × k-points of the whole zone; full diagonalisation's time grows as its cube and the matrix takes size² × 8 bytes in single precision, so the checks suggest SLEPc above 20 000 transitions, whose spectrum then covers only the excitons it finds. Haydock gives spectra but nothing for Visual Excitons. A dispersion needs every irreducible Q (`BSEQptR` over all of them).

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

**For the suite.** A Visual Workflows step running Yambo links `SAVE` from the p2y step, and its earlier steps' job folders it reads through `-J` (`links` in the step's `[job]`). Setup can be the first run of the p2y step, so `ndb.gops` and `ndb.kindx` are made once. The p2y step runs `p2y -I <prefix.save of the nscf> -O .`: `-I` is where QE's `data-file-schema.xml` is read (`interfaces/p2y/mod_p2y.F`), `-O` where `SAVE` is made, as `./SAVE/ns.db1` and `ns.wf*` (`p2y.F` sets both database paths to `-O`; `Yio/io_utilities.F`, `io_connect.F`; checked in the source of 5.1.1 and 5.3.0). p2y writes no `r-` report, only a log `l_p2y` (`LOG/l_p2y_CPU_<n>` with MPI, `l-<job>_p2y` with `-J`) in the `-C` folder. So `SAVE` is at the top of its folder rather than inside `prefix.save` (Visual Excitons would find it in either place: it looks 3 levels into each step's `outputs/`).

---

## 7. Pitfalls

1. **Serial numbers:** each p2y run gives `ns.db1` a new random serial number, stored in every database. `ndb.gops`, `ndb.kindx`, wave functions, projectors, dipoles, overlaps, `HF_and_locXC`, RIM, cutoff, the BSE kernel's header and collisions with another number are refused (`Incompatible serial number for <file>`) and computed again or ignored; `ndb.em1s`, `ndb.pp` and `ndb.QP` are still used, with `Wrong serial number` (`io_header.F`). Running p2y again makes most old databases useless. Databases with another spin setting are refused (`Refusing incompatible SPIN pol/spinors`). Any run rebuilds a missing or incompatible `ndb.kindx` into `SAVE`, so steps running at once on one linked SAVE may race.
2. **Search order:** `SAVE` comes before the further `-J` jobs; keep `SAVE` free of `ndb.QP`, `ndb.em1s`, `ndb.pp` and the like.
3. **Parallel settings that do not match the job** are dropped with a line in the log only (§3); roles larger than their sizes are fatal (except `eh`); unknown roles are accepted and do nothing.
4. **Spin:** databases with another spin setting are refused (except screening without spin–orbit used with it, 5.1+). Databases older than certain revisions are "no longer supported".
5. **netCDF/HDF5:** 5.x writes netCDF-4/HDF5 unless built for classic netCDF (2 GB per file). In 5.3 parallel HDF5 I/O is on by default when the HDF5 found is parallel; without it the BSE kernel is written per process and cannot be reused with another distribution.
6. **Versions:** `X_and_IO_*` (5.x) against `X_all_q_*`, `X_finite_q_*`, `X_q_0_*` (4.x); `g` in `SE_ROLEs` only in 5.3; `KQmemory` from 5.2; command-line letters changed in 5.0.
7. p2y stops on non-symmorphic symmetries (`force_symmorphic=.true.` in QE).
8. `-walltime` stops only the Haydock and inversion solvers, and only in forms its unit-by-unit check matches (§1): `11h`, not `11h50m`; `1d2h`, not `26h`. A stop writes `Game Over` too.
9. Success is not the exit code: a serial run that fails exits with 0; errors on processes other than the master are only in `LOG/`.

*Not verified from the source or the documentation:* the finite-Q indexing of `ndb.BS_diago_Q*` beyond "the irreducible q list", the meaning of `BS_TABLE`'s columns 4 and 5, `DysSolver="q"`, and `BSEBands` beyond the SAVE's bands (no check found). `-Q` is settled: runlevel options on the command line put yambo in input-editing mode (it rewrites the `-F` file and exits); `-Q` only matters there.
