"""The usual Quantum ESPRESSO chain for Yambo, made in one go from an scf
input: scf, then an nscf with empty bands on an unshifted grid, p2y, and a
band structure, each optional but the scf, consistent by construction.

All pw.x runs share the scf's `prefix` and its `outdir` (`out/` in the scf's
step): the nscf and the bands run read the scf's density there, and each
writes its wave functions there too. A bands run therefore replaces the
nscf's: it runs after p2y has read them (or after the nscf, without p2y).
The nscf gets `force_symmorphic` when pw.x would use symmetry operations
with fractional translations in its cell (p2y stops on them). Without a
`max_seconds`, the inputs get one from the job's time (pw.x stops and saves
before Slurm's limit).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..yambopy_ext.qepy.pw_input import PwInput, read_pw_input, write_pw_input
from .jobspec import JobSpec, Run
from .kpoints import BandPath, Grid, for_yambo
from .qe_inputs import set_kpoints
from .structure import Structure, StructureError

OUTDIR = "out"


@dataclass
class QeChain:
    """What to make: the scf input, and the other steps' settings (None: not made)."""
    scf: str                         # the scf input's text
    job: JobSpec                     # resources for the pw.x steps (runs are filled in)
    nscf_grid: Grid | None = None    # the nscf for Yambo (unshifted)
    nscf_nbnd: int | None = None
    p2y: bool = True                 # with an nscf
    bands_path: BandPath | None = None
    bands_nbnd: int | None = None
    names: dict = field(default_factory=lambda: {"scf": "scf", "nscf": "nscf", "p2y": "p2y",
                                                  "bands": "bands"})


def _true(value) -> bool:
    return value is True


def needs_force_symmorphic(pw: PwInput) -> bool:
    """Whether pw.x would use operations with fractional translations in this
    input's cell (so p2y needs force_symmorphic)."""
    if _true(pw.get("system", "nosym", False)):
        return False
    try:
        operations = Structure.from_pw(pw).pw_operations()
    except StructureError:
        return True                  # not known: setting it is harmless
    return any(any(abs(x) > 1e-6 for x in t) for _, t in operations)


def suggested_bands(occupied: int | None) -> tuple[int | None, int | None]:
    """nbnd for the nscf (empty bands for Yambo: 4 × the occupied ones, at least
    20 more) and for the band structure (a few empty bands); None when the
    occupied bands are not known."""
    if not occupied:
        return None, None
    return max(4 * occupied, occupied + 20), occupied + max(8, occupied // 2)


def chain_inputs(chain: QeChain, scf_folder: str) -> dict[str, str]:
    """The inputs, by step kind ("scf", "nscf", "bands"): the scf's with outdir
    and calculation set, the others derived from it. `scf_folder`: the scf
    step's folder name (the others reach its outdir as ../<scf_folder>/out)."""
    scf = read_pw_input(chain.scf)
    scf.set("control", "calculation", "scf")
    scf.set("control", "outdir", f"./{OUTDIR}")
    if scf.get("control", "prefix") is None:
        scf.set("control", "prefix", "pw")
    seconds = chain.job.time_seconds
    if seconds and scf.get("control", "max_seconds") is None:
        # pw.x stops and saves before Slurm's limit (the margin the checks suggest)
        scf.set("control", "max_seconds", seconds - max(600, seconds // 20))
    inputs = {"scf": write_pw_input(scf)}
    shared = f"../{scf_folder}/{OUTDIR}"
    if chain.nscf_grid is not None:
        nscf = read_pw_input(inputs["scf"])
        nscf.set("control", "calculation", "nscf")
        nscf.set("control", "outdir", shared)
        if chain.nscf_nbnd:
            nscf.set("system", "nbnd", chain.nscf_nbnd)
        set_kpoints(nscf, for_yambo(chain.nscf_grid))
        if needs_force_symmorphic(nscf):
            nscf.set("system", "force_symmorphic", True)
        inputs["nscf"] = write_pw_input(nscf)
    if chain.bands_path is not None:
        bands = read_pw_input(inputs["scf"])
        bands.set("control", "calculation", "bands")
        bands.set("control", "outdir", shared)
        if chain.bands_nbnd:
            bands.set("system", "nbnd", chain.bands_nbnd)
        set_kpoints(bands, chain.bands_path)
        inputs["bands"] = write_pw_input(bands)
    return inputs


def add_qe_chain(project, chain: QeChain, after: list[str] = (), profile=None) -> list:
    """The chain's steps added to the project (folders, inputs, job
    descriptions, scripts if `profile`); returns them, in order."""
    import tempfile
    from pathlib import Path

    from .project import _slug

    n = len(project.steps)
    folder = lambda i, kind: f"{n + i:02d}-{_slug(chain.names[kind])}"      # noqa: E731
    order = ["scf"] + (["nscf"] if chain.nscf_grid is not None else []) \
        + (["p2y"] if chain.nscf_grid is not None and chain.p2y else []) \
        + (["bands"] if chain.bands_path is not None else [])
    folders = {kind: folder(i, kind) for i, kind in enumerate(order, start=1)}
    inputs = chain_inputs(chain, folders["scf"])
    prefix = read_pw_input(inputs["scf"]).get("control", "prefix", "pw")
    steps = []
    with tempfile.TemporaryDirectory(prefix="vwf-chain-") as staging:
        for kind in order:
            if kind == "p2y":
                job = replace(chain.job, nodes=1, tasks_per_node=1, cpus_per_task=1, runs=[
                    Run("yambo", "p2y", args=f"-I ../{folders['scf']}/{OUTDIR}/{prefix}.save -O .",
                        mpi=False)])
                files = []
            else:
                name = f"{kind}.in"
                path = Path(staging) / name
                path.write_text(inputs[kind], encoding="utf-8")
                job = replace(chain.job, runs=[Run("qe", "pw", name)])
                files = [path]
            previous = {"scf": list(after), "nscf": [folders["scf"]], "p2y": [folders.get("nscf")],
                        "bands": [folders.get("p2y") or folders.get("nscf") or folders["scf"]]}[kind]
            step = project.add_job_step(chain.names[kind], job, files, previous, profile)
            steps.append(step)
    return steps
