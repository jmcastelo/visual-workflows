# Troubleshooting

[← Documentation](../README.md#documentation)

## Connecting

### Cannot connect

`Cannot connect to <host>: …` comes before any login: the name does not
resolve, the network blocks it (VPN needed?), or the port is wrong. Try
`ssh <host>` in a terminal. If it works there through an alias or a jump host,
put that alias in the profile's Host: the program reads `~/.ssh/config`.

### Login refused

- **key:** is the key file right, and is its public key in the cluster's
  `~/.ssh/authorized_keys`? Does `ssh -i <key> <user>@<host>` work?
- **password:** a remembered password that the server refuses is forgotten
  and you are asked again. If the cluster asks for more than a password
  (a one-time code), use the `interactive` login.
- **username:** empty means the one in `~/.ssh/config`, else your local name,
  which may not be your name on the cluster.

### The host key has changed

The program refuses to connect when a cluster's host key differs from the one
in `~/.ssh/known_hosts`. This happens when the cluster is reinstalled, but it
is also what an attack looks like: **ask the cluster's administrators** before
going on. Once confirmed, remove the old key with
`ssh-keygen -R <host>` (or `ssh-keygen -R '[<host>]:<port>'` for a port other
than 22) and connect again to accept the new one.

### Passwords are asked every time

Either you did not tick **Remember**, or there is no usable system keyring
(see [Installation](installation.md#the-system-keyring)): on Linux, a running
GNOME Keyring or KWallet is needed. An SSH key with the ssh-agent avoids
passwords altogether.

## Clusters

### A partition is missing

Partitions are shown only if you may use them: your Unix groups (Slurm's
`AllowGroups`) and, on clusters with accounting, your accounts and QOS
(`AllowAccounts`, `AllowQos`). `vwf partitions CLUSTER --all` shows them all,
with a YOU column. Ask the administrators for the group, account or QOS.

### Jobs are refused for their account or QOS

`Invalid account or account/partition combination`, `Invalid qos
specification`: set the profile's **Account** and **QOS** (Discover fills
them in), or put `#SBATCH --account`/`--qos` in the script. The QOS must be
one the partition accepts (`vwf partitions` shows yours per partition).

### "Accounting: no"

The cluster keeps no job accounting (`sacct`), as Lynx. Nothing to do: jobs
are followed through their own status files instead. Run time and peak memory
are then not known.

### A module does not show up

- Spack modules: is the Spack module folder in the profile's **Module
  folders**, at the architecture level (`…/modules/linux-…`)? **Discover**
  corrects it.
- Check by hand: `vwf run CLUSTER -- 'module -t avail 2>&1 | grep NAME'`.

### Check modules is not OK

- **not found after loading the modules:** the executable's name is wrong, or
  it is in another module.
- **missing libraries:** a library module is missing. On Lynx, Yambo needs
  `yambo/intel-libs` (or `yambo/gnu-libs`) besides its own module.
- **wrong library versions:** usually the same cause; otherwise modules
  built with different compilers or MPI libraries that do not go together.

## Jobs

### A step is refused at Submit

- **"needs X, which has not run":** submit X too (or all, with no selection).
- **"needs X, whose last job ended failed":** fix and run X again first.
- **"already queued or running":** wait, or cancel it first.

### A job stays pending

The **Reason** column says why. Common ones:

| Reason | Meaning |
|---|---|
| `Priority`, `Resources` | waiting for its turn, or for free nodes |
| `Dependency` | waiting for the steps it runs after |
| `DependencyNeverSatisfied` | a step it needed failed or was cancelled: it will never start; cancel it |
| `JobHeldUser`, `JobHeldAdmin` | held (`scontrol release <id>` for your own holds) |
| `ReqNodeNotAvail`, `PartitionDown` | nodes or partition unavailable |
| `QOSMaxJobsPerUserLimit`, `AssocGrpCpuLimit`… | a limit of your account or QOS |

### A job failed at once

Look at its output (select the step, or `vwf project log STEP`). Usual causes:
a module that does not load (run **Check modules**), an input file not found
(paths are relative to the step's folder on the cluster), or a request the
partition cannot satisfy (Slurm then refuses it at submission, with its
reason in the error).

### Out of memory

Ask for more with `--mem` in the job script. On clusters with a small default
(5000 MB per node on Lynx), a job script without `--mem` gets only that.

### The state is "cancelled: no record of the job running"

Slurm had forgotten the job and it left no status file: it was cancelled
before it started (or its step folder on the cluster was deleted).

### The state is "failed: killed"

The job started but could not record its end: killed without warning (memory,
node failure). Look at its output for Slurm's message.

## Downloads

### "Nothing new to get"

Every chosen file is already in `outputs/` with the same size and time. Delete
a file there to download it again. Files skipped by the choice are counted
separately: use **Everything**, or `--include PATTERN`.

### A download stopped half-way

Start it again: it resumes from the `.vwf-part` file. If the file changed on
the cluster meanwhile, the program says so; delete the `.vwf-part` file and
download again.

## Reporting a problem

Errors caused by a bug open a message with **Show Details**: copy the details
into the report, with what you were doing, and `vwf --version`.
