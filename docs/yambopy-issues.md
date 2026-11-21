# YamboPy issues

[← Documentation](../README.md#documentation)

Problems found in [YamboPy](https://github.com/yambo-code/yambopy) while building Visual Workflows, written to be filed as GitHub issues. Each section is one issue: its heading is the issue title, and the text below it the body, ready to copy and paste. Where Visual Workflows works around a problem, the code that does it is named; the code in `visual_workflows/yambopy_ext/` is written to be contributed to YamboPy as a fix.

Each section starts with a hidden comment (`<!-- ... -->`) with its status, when it was found and what to check before filing: it is not shown on GitHub if pasted with the body. Update the status when an issue is filed (with its link) or fixed, and add new findings at the end, with the next number.

Visual Excitons keeps its own list (`docs/yambopy-issues.md` in its repository); check it before filing, so that no issue is filed twice. Its issue 7 (failing tests, and `schedulerpy` tests that leave files in the repository) also concerns the parts of YamboPy listed here.

All issues were reproduced on the fork's `ase-bz` branch (e312d12) in memory, with the scripts shown; the code involved is the same as on `master`.

| # | Issue | Status |
|---|---|---|
| 1 | `PwIn` cuts string values at `/`, so paths are lost and the input written back is invalid | Not filed |
| 2 | `PwIn` fails on `K_POINTS gamma` | Not filed |
| 3 | `PwIn.from_file` adds the constructor's defaults to the input it reads | Not filed |
| 4 | `YamboIn` drops string values with characters other than letters, digits, `_` and spaces | Not filed |
| 5 | `YamboIn` reads integers as reals and writes them back as reals | Not filed (minor) |
| 6 | `YamboIn` turns on runlevels named in comments | Not filed |
| 7 | `YamboIn` does not know the `em1d` runlevel (missing comma) | Not filed |
| 8 | `PhIn.set_vars` has no `self` | Not filed |
| 9 | `yambopy/io/factories.py` imports the removed `yambopy.flow` | Not filed |
| 10 | `Scheduler.factory` accepts only the names `oar`, `pbs`, `slurm` and `bash` | Not filed |
| 11 | `schedulerpy` ignores the documented `modules` configuration key | Not filed |
| 12 | `Slurm.run` fails with its default file name and reads the job id from `sbatch`'s message | Not filed |
| 13 | The BN tutorial and the real-time scripts use Yambo 4 parallel variables, one of them misspelt | Not filed |
| 14 | The exciton–phonon tutorial's `bse.in` and the reference inputs use Yambo 4 names | Not filed |

---

<!-- Status: not filed. Found 2026-11-21. Visual Workflows reads pw.x inputs with yambopy_ext/qepy/pw_input.py (Phase 2). -->
## 1. `PwIn` cuts string values at `/`, so paths are lost and the input written back is invalid

`PwIn.store` reads namelist variables with the pattern

```python
group_regexp = r'([a-zA-Z_0-9_\(\)]+)\s*=\s*([a-zA-Z\'"0-9_.+-]+)'
```

(`qepy/pw.py`), whose value allows only letters, digits, quotes and `_.+-`. A value with any other character is cut there: paths lose everything from the first `/`, and the closing quote is lost with them. With

```
&control
 calculation='scf', prefix='si', pseudo_dir='../pseudos/', outdir='./tmp'
/
```

`PwIn.from_file` gives

```python
{'calculation': "'scf'", 'prefix': "'si'", 'pseudo_dir': "'..", 'outdir': "'.", ...}
```

and `str()` writes `pseudo_dir = '..` and `outdir = '.`, which pw.x cannot read. Any value with `/`, spaces, `:`, `~`, `$` or `!` inside quotes is affected, e.g. `title`, `outdir`, `pseudo_dir`, `wfcdir`.

**Suggested fix:** match quoted strings as a whole before the other values, e.g. `'[^']*'|"[^"]*"|[^\s,]+` for the value, and strip `!` comments outside strings first.

---

<!-- Status: not filed. Found 2026-11-21. -->
## 2. `PwIn` fails on `K_POINTS gamma`

An input ending with

```
K_POINTS gamma
```

(a valid card with no lines after it) makes `PwIn.from_file` raise `StopIteration` from `read_kpoints`, which reads the next line whatever the card's option. `gamma` takes no lines; neither do the other cards when they come last.

**Suggested fix:** in `read_kpoints`, return for `gamma` before reading further lines, and stop at the end of the file instead of calling `next()` unguarded.

---

<!-- Status: not filed. Found 2026-11-21. Check: whether the defaults are meant for new inputs only. -->
## 3. `PwIn.from_file` adds the constructor's defaults to the input it reads

`PwIn.__init__` sets defaults (`prefix='pw'`, `wf_collect=.true.`, `verbosity='high'`, `conv_thr=1e-8`), and `from_file` reads the file into that object, so the defaults stay wherever the file does not set them. Reading and writing back an input that has none of them adds

```
verbosity = 'high'
wf_collect = .true.
conv_thr = 1e-08
```

so a round trip changes the calculation (`conv_thr`, whose pw.x default is 1e-6). `wf_collect` is obsolete: INPUT_PW says it is no longer implemented, wave functions being collected by default since QE 6.2.

**Suggested fix:** apply the defaults only to inputs built from scratch (e.g. a `PwIn.new()` or a flag), not in `from_file`.

---

<!-- Status: not filed. Found 2026-11-21. Visual Workflows reads Yambo inputs with yambopy_ext/io/yambo_input.py (Phase 2). -->
## 4. `YamboIn` drops string values with characters other than letters, digits, `_` and spaces

`YamboIn` reads strings with

```python
_stringexp = '["\']([a-zA-Z0-9_ ]+?)["\']'
```

(`yambopy/io/inputfile.py`), so a string with a dot, `<`, `/`, `-`, `,` or anything else is not read, and neither is an empty string. Nothing is reported: the variable is just missing, and writing the input back loses it. From

```
BS_CPU= "2.1.1"
BS_ROLEs= "k.eh.t"
KfnQPdb= "E < ./gw/ndb.QP"
DIP_CPU= ""
DIP_ROLEs= ""
```

`YamboIn.read_string` keeps none of them. The dot form of the parallel variables is the one YamboPy's own scripts write (`yambocommandline/scripts/realtime/rt-*.py`), `KfnQPdb` is how a BSE takes GW corrections, and the empty `*_CPU`/`*_ROLEs` are what `yambo -V par` writes.

**Suggested fix:** `["\']([^"\']*)["\']`.

---

<!-- Status: not filed (minor). Found 2026-11-21. Yambo reads it fine: parse_int in src/parser/PARSER.c rounds with rint, so only the text changes. -->
## 5. `YamboIn` reads integers as reals and writes them back as reals

Numbers are read with one pattern for integers and reals and stored as `float`, so `DIP_Threads=0` and `NLogCPUs=4` become `[0.0, '']` and `[4.0, '']`, and are written back as

```
DIP_Threads = 0.000000
NLogCPUs = 4.000000
```

Yambo declares these variables as integers. It still reads such inputs correctly (its parser rounds a real to the nearest integer), so runs are not affected, but an input read and written back no longer looks like what `yambo -F` writes, and differs from the original line by line.

**Suggested fix:** keep a number without a decimal point or exponent as `int`, and write `int` values without decimals.

---

<!-- Status: not filed. Found 2026-11-21. -->
## 6. `YamboIn` turns on runlevels named in comments

Runlevels are found by searching the whole text for words in `_runlevels`, comments included. With

```
# a comment mentioning em1s
#WRbsWF                          # [BSS] Write to disk excitonic the WFs
```

`YamboIn.read_string` turns on `em1s` and `WRbsWF`, and writing the input back makes them active. `yambo -F` writes optional flags commented out exactly like `#WRbsWF` above, so reading any generated input turns them on.

**Suggested fix:** strip everything from `#` to the end of the line (outside strings) before looking for runlevels, and take a runlevel only when it is the first word of a line.

---

<!-- Status: not filed. Found 2026-11-21. -->
## 7. `YamboIn` does not know the `em1d` runlevel (missing comma)

`_runlevels` in `yambopy/io/inputfile.py` reads

```python
_runlevels  = ['rim_cut','chi','em1s','bse','optics','bsk','bss','dipoles','ExcitonGkkp'
               'em1d','gw0','HF_and_locXC', ...
```

with no comma after `'ExcitonGkkp'`, so Python joins the two strings into `'ExcitonGkkpem1d'`: neither `ExcitonGkkp` nor `em1d` is a runlevel. An `em1d` line (every full-frequency or PPA GW input) is dropped on reading.

**Suggested fix:** add the comma.

---

<!-- Status: not filed. Found 2026-11-21. -->
## 8. `PhIn.set_vars` has no `self`

`qepy/ph.py`:

```python
    def set_vars(**kwargs):
        for var,value in kwargs.items():
            self[var] = value
```

Calling `ph.set_vars(...)` raises `TypeError` (it takes no positional argument).

**Suggested fix:** `def set_vars(self, **kwargs):`.

---

<!-- Status: not filed. Found 2026-11-21. Check: whether the module should be removed or ported. -->
## 9. `yambopy/io/factories.py` imports the removed `yambopy.flow`

`factories.py` starts with

```python
from yambopy.flow import PwTask, PhTask, P2yTask, YamboTask, DynmatTask, YambopyFlow, AbinitTask, E2yTask
```

but `yambopy/flow` was removed ("old unused developments; see yambopy-0.6 branch", 2ed95b2), so importing the module fails with `ModuleNotFoundError`. Nothing imports it, so the failure only shows when someone tries to use the task factories it defines (`PwNscfTasks`, `YamboQPBSETasks`, ...).

**Suggested fix:** remove the module, or move it to the branch that has `yambopy.flow`.

---

<!-- Status: not filed. Found 2026-11-21. -->
## 10. `Scheduler.factory` accepts only the names `oar`, `pbs`, `slurm` and `bash`

`Scheduler.factory` (`schedulerpy/scheduler.py`) reads the configuration of the scheduler named by its argument or by `default`, and finds its type from a `type` tag or from the name. It then checks

```python
if schedulername not in list(schedulers.keys()):
    raise ValueError("Scheduler name %s is invalid"%schedulername)
```

against `oar`, `pbs`, `slurm` and `bash`, so a configuration named after a cluster (`"mycluster": {"type": "slurm", ...}`) is rejected, although the `type` tag exists for it. Two smaller problems in the same method:

- with no scheduler and no `default`, the error message uses `self._config_filename` in a class method, so it raises `NameError` instead;
- the type is stored on the class (`cls.schedulertype = schedulertype`), so every instance shares the last one created.

**Suggested fix:** check `schedulertype` instead of `schedulername`; use `cls` in the message; store the type on the instance.

---

<!-- Status: not filed. Found 2026-11-21. -->
## 11. `schedulerpy` ignores the documented `modules` configuration key

The configuration described in `Scheduler`'s docstring and used in `schedulerpy/tests/test_scheduler.py` has a `"modules"` key (`{"<module tag>": "<module name>"}`), but the code reads only `modules_dict` and `modules_list` (`Scheduler.__init__`, `add_module`). A configuration written as documented loads no modules, without any message.

**Suggested fix:** read `modules` as well (or update the documentation and tests to `modules_dict`).

---

<!-- Status: not filed. Found 2026-11-21. -->
## 12. `Slurm.run` fails with its default file name and reads the job id from `sbatch`'s message

`Slurm.run(filename='run.sh')` calls `subprocess.Popen(..., cwd=os.path.dirname(filename))`, and `os.path.dirname('run.sh')` is `''`: `Popen` raises `FileNotFoundError` for an empty `cwd`, so `run()` only works with a path that has a folder.

It then takes the job id as the last word of `sbatch`'s output (`stdout.split(' ')[-1]`). Sites that add messages to `sbatch`'s output, or a federated cluster (`Submitted batch job 123 on cluster x`), give a wrong id.

**Suggested fix:** `cwd=os.path.dirname(filename) or None`; submit with `sbatch --parsable` and take the part before any `;`.

---

<!-- Status: not filed. Found 2026-11-21. Yambo ignores unknown variables silently (its parser stores every name; the code reads only those it knows: src/parser/PARSER.c, 5.3). Check: whether the tutorials are meant for Yambo 4. -->
## 13. The BN tutorial and the real-time scripts use Yambo 4 parallel variables, one of them misspelt

`tutorial/run_calculations/bse_bn.py` sets

```python
y['X_all_q_CPUs'] = "%d"%nthreads
```

a variable no Yambo version has (Yambo 4 called it `X_all_q_CPU`; Yambo 5 `X_and_IO_CPU`). Yambo ignores unknown variables without a message, so the setting has no effect and the run uses the default distribution. The real-time scripts (`yambocommandline/scripts/realtime/rt-ip.py`, `rt-pulse.py`, `rt-cohsex.py`, `rt-bse.py`) set `X_all_q_CPU`, which Yambo 5 no longer has (it was replaced by `X_and_IO_CPU` in 5.0), so with Yambo 5 these settings are ignored as well. No script uses the Yambo 5 names.

**Suggested fix:** use `X_and_IO_CPU`/`X_and_IO_ROLEs` with space separators, or say which Yambo version each script is for.

---

<!-- Status: not filed. Found 2026-11-21 by checking YamboPy's inputs against the names yambo reads per version (visual_workflows/yambopy_ext/io/yambo_variables.py, from Yambo's source at 5.1.1, 5.3.0 and 5.4-beta). -->
## 14. The exciton–phonon tutorial's `bse.in` and the reference inputs use Yambo 4 names

`tutorial/exciton-phonon/1L_MoS2/bse.in` (and `3D_hBN/bse.in`) switch on the runlevel `bsk` and set `BS_nCPU_diago`, names Yambo 4 used. Yambo 5 has neither (the kernel runlevel is part of `bse`; the linear-algebra count is `BS_nCPU_LinAlg_DIAGO`) and ignores unknown names without a message, so the tutorial's setting for the parallel diagonalisation has no effect. The 1L_MoS2 input also sets `X_Threads` twice.

The reference inputs in `yambopy/data/refs/` (`bse/yambo_run.in`, `gw_conv/*.in`, `si/yambo_bse*.in`, `si/yambo_gw*.in`) have Yambo 4 names too: `XfnQP_N`, `GfnQP_N`, `KfnQP_N` (removed in 5.0), `BS_nCPU_invert`, `X_all_q_nCPU_invert`, and `kind`.

**Suggested fix:** regenerate the tutorial inputs with Yambo 5 (`yambo -o b -k sex -y d -V par -F bse.in`), or replace `bsk` (drop it) and `BS_nCPU_diago` (→ `BS_nCPU_LinAlg_DIAGO`); say which Yambo version the reference inputs come from.
