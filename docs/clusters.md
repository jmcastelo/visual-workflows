# Clusters

[← Documentation](../README.md#documentation)

Each cluster you use has a **profile**: how to connect, where projects go on
it, its partitions, and which modules give each program. Profiles hold no
passwords and can be shared with colleagues.

## Adding a cluster

**In the program:** Clusters ▸ New cluster. Fill in at least the **Host** and,
usually, your **Username**, then **Save**. **Test connection** and
**Discover** (below) fill in the rest.

**From the command line:**

```bash
vwf clusters add Lynx --host lynx.example.org --user alice --key ~/.ssh/id_ed25519
vwf test Lynx
```

## The profile

| Field | Meaning |
|---|---|
| Name | How you call the cluster; also used by projects to find it. |
| Host | Its name, or an alias from your `~/.ssh/config` (see below). |
| Port | Empty ("default"): from `~/.ssh/config`, else 22. |
| Username | Empty: from `~/.ssh/config`, else your local user name. |
| Login | `key`, `agent`, `password` or `interactive` (see [Logging in](#logging-in)). |
| Key file | For `key`: your private key. Empty: the ssh-agent and the default keys (`~/.ssh/id_*`). |
| Projects folder | Where projects go on the cluster (`work_root`). Default `$HOME/visual-workflows`; `$HOME`, `~` and other variables are expanded on the cluster. |
| Scratch | Your scratch folder, if the cluster has a shared one, e.g. `/storage/scratch/<group>/$USER` (variables expanded on the cluster). The Files tab's **Scratch** button goes there. To run projects on scratch, make the projects folder a folder inside it. |
| Python | The cluster's Python (reported by Test connection). |
| Account, QOS | Slurm's `--account` and `--qos`. Added to every job whose script does not set its own; empty: not added. Discover fills them in on clusters with accounting. |
| Module folders | Extra module folders to add (`module use`), one per line, e.g. Spack's. |
| Partitions | Name, maximum time, CPUs, memory and GPUs per node, threads per core, the smallest node's memory and the most memory a job may ask per node. Filled by Discover; editable. |
| Programs | For each program (qe, yambo): modules to load, MPI launcher and its options, executables. |

## Logging in

The program reads your `~/.ssh/config`: host aliases, `HostName`, `User`,
`Port`, `IdentityFile` and `ProxyJump` work as they do for `ssh`. A cluster you
reach in a terminal with `ssh lynx` can use `lynx` as its host.

Login methods:

- **key:** a key file. Its passphrase is asked only if the key is encrypted.
  With no key file, the ssh-agent and the default keys are tried.
- **agent:** the ssh-agent and the default keys.
- **password:** your account's password.
- **interactive:** the server asks its own questions, typically a password and
  then a one-time code. Each question is shown as the server writes it.

All of them also accept a one-time code asked after the key or the password,
as some clusters do.

**Remembering.** When asked for a password or a key passphrase, tick
**Remember it in the system keyring** (in the terminal, answer `y`). It is
kept by your system's password store (see
[Installation](installation.md#the-system-keyring)) only once the login
succeeds, and forgotten if the server later refuses it. One-time codes are
never kept.

**Host keys.** The first time you connect to a cluster, its host key is shown
with its fingerprint. Compare it with the cluster's documentation or ask its
administrators; if you trust it, it is added to `~/.ssh/known_hosts`. If a
cluster's key changes later, the program refuses to connect: see
[Troubleshooting](troubleshooting.md#the-host-key-has-changed).

## Test connection and Discover

**Test connection** (`vwf test`) logs in and reports:

- the home folder and Slurm's version;
- whether the cluster keeps **job accounting** (`sacct`). Some clusters
  (Lynx) do not; the program then follows jobs through their own status files
  (see [Projects](projects.md#where-a-state-comes-from));
- your Unix groups, which decide the partitions you may use;
- the module system (Environment Modules or Lmod) and its version.

**Discover** fills in the partitions you may use, with their time limit and
the most CPUs, memory and GPUs of their nodes (`vwf partitions --all` shows
the others too). It also records:

- **threads per core**: Slurm counts each hardware thread as a CPU, so a
  Lynx node with 48 cores of 2 threads shows 96 CPUs, while Tirant's nodes
  have 48 cores of 1 thread. Where cores have 2 threads, job scripts give each
  MPI task whole cores, asking for both threads of each as CPUs
  (`--cpus-per-task=2` for one thread per task, `OMP_NUM_THREADS=1`): QE and
  Yambo run no faster on the second thread;
- **the smallest node's memory**, where a partition's nodes differ (Lynx's
  `magma` has nodes of 119 and 178 GB), since a job may land on either;
- **the job memory limit per node** (Slurm's `MaxMemPerNode`), which can be
  below the nodes' memory: on Tirant, 92000 MB on nodes of 96310 MB, so a job
  asking for 94 GB is refused.

Memory is rounded down to 0.1 GB, so asking for the figure shown always fits.

Who may use a partition:

- by **Unix group**, where the partition limits them (Lynx);
- on clusters with accounting, also by **account and QOS**: a partition may
  accept only some QOS (Tirant's `main` accepts the `class_*` ones), and your
  account gives you some of them (`class_t` and `interactive`). The time limit
  is then the QOS's (3 days for `class_t`), not the partition's "unlimited".

On such clusters Discover also fills in the profile's **Account** and **QOS**
if they are empty: your account, and the default QOS of the default
partition. It also checks the module folders: if
one holds only a single `linux-…` architecture folder, as Spack's
`…/share/spack/modules` does, it is replaced by that folder, where the
modules really are.

CPUs are counted as Slurm counts them. On clusters with hyper-threading (Lynx:
96 "CPUs" per node are 48 cores with 2 threads each), that is twice the
physical cores.

## Modules

Programs on clusters come as modules: Environment Modules (Lynx) or Lmod
(Tirant). The program lists them (with JSON where Environment Modules offer
it), reads their descriptions and what they load from `module show` in either
system's form, and shows each as program, version and build:

| Module | Shown as |
|---|---|
| `qe/7.3.1b-intelmpi-omp` | qe · 7.3.1b intelmpi-omp |
| `yambo/gpl/5.2.4-single-omp` | yambo · 5.2.4 single-omp (gpl) |
| `quantum-espresso/7.5-intel-oneapi-compilers-2026.0.0-ra7jhfo` | quantum-espresso · 7.5 intel-oneapi-compilers-2026.0.0 (Spack) |
| `qe/oneapi-2024.2.1/7.3.1-omp` (Lmod) | qe · 7.3.1 omp (oneapi-2024.2.1) |

Spack's 7-letter hash is shown only when two builds would otherwise look the
same. Newest versions come first; modules without a version (library
bundles, development branches) last.

**Spack.** Spack's modules appear once their folder is added to the profile's
**Module folders**, e.g. `/apps/spack/share/spack/modules/linux-arch-x86_64`
(the architecture folder; Discover corrects a folder given one level higher).
The listing, the module choice and the check use them. Job scripts you write
must add them themselves (`module use <folder>` before `module load`); the
job script builder planned next will write them.

## Programs: choosing and checking modules

For each program (qe, yambo) the profile says which modules to load, how to
start it in parallel, and its executables:

- **Modules:** select the program's row and **Choose modules…**. Several can be
  chosen: for example `yambo/intel-libs` and `yambo/gpl/5.1.1-intelmpi` on
  Lynx, where the Yambo modules need a separate module with their libraries.
- **MPI launcher** (`srun`, `mpirun`, `mpiexec`) and **Launcher options**, e.g.
  `--mpi=pmi2` for Lynx's Intel MPI builds with `srun` (Lynx sets no MPI default
  in Slurm); none on Tirant, whose Slurm defaults to PMIx, which Intel MPI
  takes (tried with `qe/7.5`).
- **Executables:** `name=file` pairs, e.g. `pw=pw.x ph=ph.x`.

**Check modules** (`vwf modules --check`) loads the modules on the login node
from a clean slate, as a job would, then looks for each executable and lists
the shared libraries it cannot find. A forgotten library module shows up here
in seconds, instead of in a job that fails after waiting in the queue:

```
$ vwf modules Lynx --check yambo/gpl/5.1.1-intelmpi --exe yambo
  yambo: /apps/packages/yambo/5.1.1-intelmpi/bin/yambo
    missing libraries: libnetcdf.so.19, libopenblas.so.3, libhdf5.so.200, …
    wrong version: libfabric.so.1 lacks FABRIC_1.9 (needed by libmpi.so.40)
Not OK:
  yambo: missing libraries … (a library module not loaded?)
```

"Wrong version" means a library was found, but an older one than needed:
usually the side effect of a missing module (here the system's MPI was picked
up instead), sometimes modules that do not go together.

## Sharing profiles

**Export** (Clusters ▸ Export profile to share, `vwf clusters export`) writes a
copy without your username and key file. Send it to colleagues; they
**Import** it (`vwf clusters import`), fill in their username, and save.
Importing never overwrites a profile you have: a second "Lynx" is saved as
`lynx-2.cluster.toml`.

## The profile file

Profiles are TOML files in
`~/.config/visual-suite/visual-workflows/clusters/`. They can be edited by
hand; the program keeps settings it does not know when it saves them again.

```toml
format = "cluster-profile/1"

[cluster]
name = "Lynx"
description = "Group cluster"

[connection]
host = "lynx.example.org"      # or an alias from ~/.ssh/config
username = "alice"             # empty: from ~/.ssh/config or your local name
auth = "key"                   # key | agent | password | interactive
key_file = "~/.ssh/id_ed25519" # empty: ssh-agent and default keys
connect_timeout = 30.0
python = "python3"
# port = 22                    # left out: from ~/.ssh/config, else 22

[scheduler]
type = "slurm"
account = ""                   # only if the cluster requires one
qos = ""

[paths]
work_root = "$HOME/visual-workflows"
scratch = ""
pseudos = ""                   # a library of pseudopotentials on the cluster (optional)

[modules]
use = ["/apps/spack/share/spack/modules/linux-arch-x86_64"]

[partitions.magma]
max_walltime = ""              # empty or UNLIMITED: no limit
cores_per_node = 96            # CPUs as Slurm counts them
memory_per_node_gb = 178.1     # the largest node's
gpus_per_node = 0
threads_per_core = 2           # 96 CPUs are 48 cores; 0: unknown
min_memory_per_node_gb = 119.2 # the smallest node's
memory_limit_per_node_gb = 0   # MaxMemPerNode; 0: no limit
qos = {}                       # the QOS you may use here, with their time limits (Discover)

[programs.qe]
modules = ["qe/7.3.1b-intelmpi-omp"]
preamble = []                  # shell lines after the modules (e.g. a `source`)
mpi_launcher = "srun"
launcher_options = "--mpi=pmi2"
executables = { pw = "pw.x", ph = "ph.x" }

[programs.yambo]
modules = ["yambo/intel-libs", "yambo/gpl/5.1.1-intelmpi"]
mpi_launcher = "srun"
launcher_options = "--mpi=pmi2"
executables = { p2y = "p2y", yambo = "yambo", ypp = "ypp" }
refresh = "login"              # where yambo writes inputs to compare with (login, job, off)
```

`refresh` (yambo only) says where the cluster's yambo may write the inputs
`vwf project refresh-input` compares a step's input with: `login` (the
default: on the login node, seconds and no calculation), `job` (a short job
on the step's partition, waited for, where login nodes must not run
programs), or `off`.

The first line, `format`, tells what the file is and its version; it never
carries the program's name, so shared profiles keep working if the program is
renamed. A profile from a newer version of the program opens with a warning.
