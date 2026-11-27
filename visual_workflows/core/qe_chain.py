"""The usual Quantum ESPRESSO chain for Yambo, made in one go from an scf
input: scf, then an nscf with empty bands on an unshifted grid, p2y, and a
band structure, each optional but the scf, consistent by construction.

All pw.x runs share the scf's `prefix` and its `outdir` (`out/` in the scf's
step): the nscf and the bands run read the scf's density there, and each
writes its wave functions there too. A bands run therefore replaces the
nscf's: it runs after p2y has read them (or after the nscf, without p2y).
With GW or a BSE, the p2y step also runs yambo's setup (the SAVE indexed
once: ndb.gops, ndb.kindx; without it yambo asks for nothing else), and the
yambo steps link its SAVE: the GW writes its QP energies (its job `gw`), the
BSE reads them through `KfnQPdb` (or shifts DFT energies by a scissor);
inputs from core/yambo_inputs.py. The bands run comes last.

The nscf gets `force_symmorphic` when pw.x would use symmetry operations
with fractional translations in its cell (p2y stops on them), and
`diago_full_acc` (empty bands converged as the occupied ones, for GW and BSE). Without a
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
# The band structure's energies, copied by the bands step into its own folder
# from the outdir it shares with the scf (where the next pw.x run would
# overwrite them); read by core/band_structure.py
BANDS_XML = "bands.xml"


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
    # Yambo, after p2y (which then also runs yambo's setup): a GW step, and a
    # BSE step on its QP energies ("gw") or on DFT energies with a scissor
    gw: bool = False
    bse: str | None = None           # None, "gw", "scissor"
    scissor_ev: float | None = None
    scissor_from: str | None = None  # an ndb.QP on the local disk: the scissor fitted to it
    yambo_bands: tuple | None = None  # QPkrange's and BSEBands' (default: 2 + 2 around the gap)
    bse_rim_w: bool = True           # a layer's BSE with RIM-W in its kernel, as its GW
    gw_terminator: bool = False      # the GW with the Bruneval–Gonze terminator (GTermKind "BG")
    bse_all_q: bool = False          # the BSE at every Q (BSEQptR), for the excitons' dispersion
    yambo_job: JobSpec | None = None  # resources of the yambo steps (default: `job`'s)
    names: dict = field(default_factory=lambda: {"scf": "scf", "nscf": "nscf", "p2y": "p2y",
                                                  "gw": "gw", "bse": "bse", "bands": "bands"})


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
        # Empty bands converged as tightly as the occupied ones (Yambo's tutorials)
        if nscf.get("electrons", "diago_full_acc") is None:
            nscf.set("electrons", "diago_full_acc", True)
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


def add_qe_chain(project, chain: QeChain, after: list[str] = (), profile=None,
                 yambo_version=None) -> list:
    """The chain's steps added to the project (folders, inputs, job
    descriptions, scripts if `profile`); returns them, in order.
    `yambo_version`: (major, minor) of the yambo the steps will run, for the
    names of its inputs (default: from the profile's yambo modules)."""
    import tempfile
    from pathlib import Path

    from .project import _slug

    yambo = chain.gw or chain.bse is not None
    if yambo and (chain.nscf_grid is None or not chain.p2y):
        raise ValueError("GW and BSE steps need the nscf and p2y")
    if chain.bse == "gw" and not chain.gw:
        raise ValueError("a BSE on GW energies needs the GW step")
    if chain.bse == "scissor" and (chain.scissor_ev is None) == (chain.scissor_from is None):
        raise ValueError("a BSE with a scissor needs the shift (scissor_ev) or an ndb.QP to fit it to "
                         "(scissor_from), one of them")
    n = len(project.steps)
    folder = lambda i, kind: f"{n + i:02d}-{_slug(chain.names[kind])}"      # noqa: E731
    order = ["scf"] + (["nscf"] if chain.nscf_grid is not None else []) \
        + (["p2y"] if chain.nscf_grid is not None and chain.p2y else []) \
        + (["gw"] if chain.gw else []) + (["bse"] if chain.bse else []) \
        + (["bands"] if chain.bands_path is not None else [])
    folders = {kind: folder(i, kind) for i, kind in enumerate(order, start=1)}
    inputs = chain_inputs(chain, folders["scf"])
    prefix = read_pw_input(inputs["scf"]).get("control", "prefix", "pw")
    if yambo:
        inputs.update(_yambo_inputs(project, chain, folders, inputs, profile, yambo_version))
    steps = []
    with tempfile.TemporaryDirectory(prefix="vwf-chain-") as staging:
        def staged(name):
            path = Path(staging) / name
            path.write_text(inputs[name.removesuffix(".in")], encoding="utf-8")
            return path
        save = {"SAVE": f"../{folders.get('p2y')}/SAVE"}
        yjob = chain.yambo_job or chain.job
        for kind in order:
            files = []
            if kind == "p2y":
                runs = [Run("yambo", "p2y", args=f"-I ../{folders['scf']}/{OUTDIR}/{prefix}.save -O .",
                            mpi=False)]
                if yambo:                        # the SAVE indexed once, here
                    runs.append(Run("yambo", "yambo", "setup.in", mpi=False))
                    files = [staged("setup.in")]
                # one node: the node list's first, if there is one
                job = replace(chain.job, nodes=1, tasks_per_node=1, cpus_per_task=1, runs=runs,
                              nodelist=chain.job.node_names[:1])
            elif kind in ("gw", "bse"):
                files = [staged(f"{kind}.in")]
                job = replace(yjob, links=dict(save),
                              runs=[Run("yambo", "yambo", f"{kind}.in", jobs=[kind])])
            else:
                files = [staged(f"{kind}.in")]
                job = replace(chain.job, runs=[Run("qe", "pw", f"{kind}.in")])
                if kind == "bands":              # its energies kept in its own folder
                    job.after = [f"cp ../{folders['scf']}/{OUTDIR}/{prefix}.save/"
                                 f"data-file-schema.xml {BANDS_XML}"]
            previous = {"scf": list(after), "nscf": [folders["scf"]], "p2y": [folders.get("nscf")],
                        "gw": [folders.get("p2y")],
                        "bse": [folders.get("gw") if chain.bse == "gw" else folders.get("p2y")],
                        "bands": [folders.get("p2y") or folders.get("nscf") or folders["scf"]]}[kind]
            step = project.add_job_step(chain.names[kind], job, files, previous, profile)
            steps.append(step)
    return steps


def _yambo_inputs(project, chain: QeChain, folders: dict, inputs: dict, profile, version) -> dict:
    """setup, gw and bse inputs (core/yambo_inputs.py), from what the nscf
    makes the SAVE hold."""
    from .parallel import yambo_version
    from .yambo_inputs import SaveInfo, bse_input, gw_input
    if version is None and profile is not None and "yambo" in profile.programs:
        version = yambo_version(profile.programs["yambo"].modules)
    version = ".".join(str(x) for x in version) if version else None
    save = SaveInfo.from_nscf(read_pw_input(inputs["nscf"]), project.path / folders["nscf"])
    tasks = (chain.yambo_job or chain.job).tasks
    out = {"setup": "setup\n"}
    if chain.gw:
        out["gw"] = gw_input(save, qp_bands=chain.yambo_bands, tasks=tasks, version=version,
                             terminator=chain.gw_terminator).text(version)
    if chain.bse == "gw":
        qp = f"../{folders['gw']}/gw/ndb.QP"
        out["bse"] = bse_input(save, qp_db=qp, bands=chain.yambo_bands, tasks=tasks,
                               rim_w=chain.bse_rim_w, version=version, all_q=chain.bse_all_q).text(version)
    elif chain.bse == "scissor":
        out["bse"] = bse_input(save, scissor_ev=chain.scissor_ev, bands=chain.yambo_bands,
                               tasks=tasks, scissor_from=chain.scissor_from,
                               rim_w=chain.bse_rim_w, version=version,
                               all_q=chain.bse_all_q).text(version)
    return out
