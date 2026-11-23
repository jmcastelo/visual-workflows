#!/usr/bin/env python3
"""Compare our k-point counts and symmetry operations with pw.x's, on the
benchmarks of QE's test-suite (core/kpoints.py, yambopy_ext/qepy/symmetry.py).

Usage: scripts/check-kpoints-against-qe.py QE_REPO [--tag qe-7.5] [--all]

For every test-suite/pw_*/benchmark.out.git.inp=NAME.in whose input has an
automatic grid, the input's structure and flags (nosym, noinv,
force_symmorphic, a magnetized noncolin) give our count, compared with the
benchmark's "number of k points=" (pw.x prints it before nspin = 2 doubles
the list). Operation counts ("N Sym. Ops.") that differ are listed too, with
--all; benchmarks older than QE 6.4 or so dropped fractional translations
incommensurate with the FFT grid (pw.x now enlarges the grid), so those
differ by design. Berry-phase and electric-field runs are skipped (pw.x
makes their k-points otherwise); noncollinear magnetic ones get no count.

The tag is read with `git show`: the clone is not checked out or changed.
Last run (2026-11-23, qe-7.5): 108 counts, all the same (and 6 noncollinear
magnetic inputs without one).
"""

import argparse
import re
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore")

from visual_workflows.core.kpoints import Grid, irreducible_count  # noqa: E402
from visual_workflows.core.structure import Structure  # noqa: E402
from visual_workflows.yambopy_ext.qepy.pw_input import read_pw_input  # noqa: E402

BENCHMARK = re.compile(r"(test-suite/pw_[^/]+)/benchmark\.out\.(?:SVN|git)\.inp=(.+?\.in)(?:\.args=.*)?$")
TRUE = (".true.", "true", "t", ".t.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("qe_repo")
    parser.add_argument("--tag", default="qe-7.5")
    parser.add_argument("--all", action="store_true", help="also list operation counts that differ")
    args = parser.parse_args()

    def show(path):
        return subprocess.run(["git", "-C", args.qe_repo, "show", f"{args.tag}:{path}"],
                              capture_output=True, text=True).stdout

    files = subprocess.run(["git", "-C", args.qe_repo, "ls-tree", "-r", "--name-only", args.tag,
                            "test-suite/"], capture_output=True, text=True, check=True).stdout.split()
    same = different = skipped = 0
    for name in files:
        m = BENCHMARK.match(name)
        if not m:
            continue
        out, text = show(name), show(f"{m.group(1)}/{m.group(2)}")
        found = re.search(r"number of k points=\s*(\d+)", out)
        ops = re.search(r"(\d+) Sym\. Ops\.|No symmetry found", out)
        try:
            pw = read_pw_input(text)
            kpoints = pw.k_points()
        except Exception:
            skipped += 1
            continue
        if not found or kpoints is None or kpoints.kind != "automatic":
            skipped += 1
            continue

        def flag(namelist, key):
            return str(pw.get(namelist, key, "")).lower() in TRUE

        if flag("control", "lelfield") or flag("control", "lberry"):
            skipped += 1
            continue
        magnetic = flag("system", "noncolin") and any(
            k.startswith("starting_magnetization") and float(str(v).lower().replace("d", "e")) != 0
            for k, v in pw.namelists.get("system", {}).items())
        symmorphic = flag("system", "force_symmorphic")
        try:
            structure = Structure.from_pw_input(text)
            operations = len(structure.pw_operations(nofrac=symmorphic))
            count = irreducible_count(structure, Grid(kpoints.grid, kpoints.shift),
                                      force_symmorphic=symmorphic, nosym=flag("system", "nosym"),
                                      noinv=flag("system", "noinv"), magnetic_noncollinear=magnetic)
        except Exception as e:
            print(f"not read  {m.group(1)[11:]}/{m.group(2)}: {e}")
            skipped += 1
            continue
        theirs = int(found.group(1))
        qe_ops = (int(ops.group(1)) if ops.group(1) else 1) if ops else None
        if count is None or count == theirs:
            same += count is not None
            if args.all and qe_ops and operations != qe_ops and not flag("system", "nosym"):
                print(f"ops only  {m.group(1)[11:]}/{m.group(2)}: operations {operations}, pw.x {qe_ops}")
            continue
        different += 1
        print(f"DIFFERENT {m.group(1)[11:]}/{m.group(2)}: k-points {count}, pw.x {theirs}; "
              f"operations {operations}, pw.x {qe_ops}")
    print(f"same {same}, different {different}, skipped {skipped}")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
