import tomllib

import pytest

from visual_workflows.core.paths import cluster_profiles_dir
from visual_workflows.core.profile import (ClusterProfile, Partition, ProfileError,
                                           ProfileStore, Program, file_name)
from visual_workflows.core.ssh import ConnectionSettings

LYNX = """
format = "cluster-profile/1"

[cluster]
name = "Lynx"
description = "Group cluster"

[connection]
host = "lynx.example.org"
username = "alice"
auth = "key"
key_file = "~/.ssh/id_ed25519"
python = "python3"

[scheduler]
type = "slurm"
account = "physics"

[paths]
work_root = "$HOME/vw"

[partitions.cpu]
max_walltime = "3-00:00:00"
cores_per_node = 64
memory_per_node_gb = 256

[programs.qe]
modules = ["intel/2023", "qe/7.3"]
mpi_launcher = "srun"
executables = { pw = "pw.x", ph = "ph.x" }
"""


def test_load_lynx():
    p = ClusterProfile.from_toml(LYNX)
    assert p.name == "Lynx"
    assert p.connection == ConnectionSettings(
        host="lynx.example.org", port=None, username="alice", auth="key",
        key_file="~/.ssh/id_ed25519")
    assert p.account == "physics" and p.work_root == "$HOME/vw"
    assert p.partitions["cpu"] == Partition("3-00:00:00", 64, 256.0, 0)
    assert p.programs["qe"].executables == {"pw": "pw.x", "ph": "ph.x"}
    assert p.validate() == [] and p.warnings == []


def test_round_trip_keeps_unknown_keys(tmp_path):
    text = LYNX + """
[future]
setting = 1

[programs.qe.gpu]
cuda = true
"""
    text = text.replace('python = "python3"', 'python = "python3"\nproxy = "gateway"')
    p = ClusterProfile.from_toml(text)
    p.programs["qe"].modules.append("hdf5/1.14")
    p.save(tmp_path / "lynx.cluster.toml")
    data = tomllib.loads((tmp_path / "lynx.cluster.toml").read_text())
    assert data["future"] == {"setting": 1}
    assert data["connection"]["proxy"] == "gateway"
    assert data["programs"]["qe"]["gpu"] == {"cuda": True}
    assert data["programs"]["qe"]["modules"][-1] == "hdf5/1.14"
    assert ClusterProfile.load(tmp_path / "lynx.cluster.toml") == p


def test_format_key_first_and_port_omitted_when_unset(tmp_path):
    text = ClusterProfile.from_toml(LYNX).to_toml()
    assert text.startswith('format = "cluster-profile/1"')
    assert "port" not in tomllib.loads(text)["connection"]


def test_removed_partition_is_dropped():
    p = ClusterProfile.from_toml(LYNX)
    del p.partitions["cpu"]
    assert p.to_dict()["partitions"] == {}


def test_export_leaves_out_personal_fields(tmp_path):
    p = ClusterProfile.from_toml(LYNX)
    p.path = tmp_path / "mine.cluster.toml"
    p.export(tmp_path / "shared.cluster.toml")
    shared = tomllib.loads((tmp_path / "shared.cluster.toml").read_text())["connection"]
    assert shared["username"] == "" and shared["key_file"] == ""
    assert shared["host"] == "lynx.example.org"
    assert p.path == tmp_path / "mine.cluster.toml"     # still the user's own file
    assert p.connection.username == "alice"


def test_newer_format_loads_with_warning_and_stays_newer():
    p = ClusterProfile.from_toml(LYNX.replace("cluster-profile/1", "cluster-profile/7"))
    assert p.warnings and "newer" in p.warnings[0]
    assert p.to_dict()["format"] == "cluster-profile/7"


@pytest.mark.parametrize("text, message", [
    ("[cluster]\nname = 'x'\n", "no format key"),
    ('format = "workflow-project/1"\n', "not a cluster profile"),
    ('format = "cluster-profile/one"\n', "format version"),
    ("format = ", "not valid TOML"),
    (LYNX.replace("cores_per_node = 64", 'cores_per_node = "64"'), "partitions.cpu.cores_per_node"),
    (LYNX.replace('modules = ["intel/2023", "qe/7.3"]', 'modules = "qe/7.3"'), "list of strings"),
    (LYNX.replace("[connection]", "[connection]\nport = true"), "connection.port"),
])
def test_unreadable_profiles(text, message):
    with pytest.raises(ProfileError, match=message):
        ClusterProfile.from_toml(text)


def test_validate():
    p = ClusterProfile(name="", connection=ConnectionSettings(host="", port=70000, auth="magic"))
    p.partitions["x"] = Partition(max_walltime="3 days", cores_per_node=-1)
    p.programs["qe"] = Program(mpi_launcher="aprun", executables={"pw": ""})
    problems = "\n".join(p.validate())
    for expected in ("no name", "No host", "70000", "'magic'", "'3 days'",
                     "negative cores", "'aprun'", "no executable given for pw"):
        assert expected in problems


@pytest.mark.parametrize("walltime", ["60", "30:00", "12:00:00", "2-12", "2-12:30",
                                      "3-00:00:00", "UNLIMITED", "infinite", ""])
def test_valid_walltimes(walltime):
    p = ClusterProfile(name="x", connection=ConnectionSettings(host="h"),
                       partitions={"p": Partition(max_walltime=walltime)})
    assert p.validate() == []


def test_store(tmp_path):
    store = ProfileStore(tmp_path / "clusters")
    assert store.load_all() == ([], [])
    a = ClusterProfile.from_toml(LYNX)
    assert store.add(a).name == "lynx.cluster.toml"
    b = ClusterProfile.from_toml(LYNX)
    assert store.add(b).name == "lynx-2.cluster.toml"     # never overwrites
    (tmp_path / "clusters" / "broken.cluster.toml").write_text("format = ")
    profiles, errors = store.load_all()
    assert len(profiles) == 2 and len(errors) == 1 and "broken" in errors[0]
    store.delete(b)
    assert [p.name for p in store.paths()] == ["broken.cluster.toml", "lynx.cluster.toml"]


def test_import_shared_profile(tmp_path):
    shared = tmp_path / "from-a-colleague.cluster.toml"
    ClusterProfile.from_toml(LYNX).export(shared)
    store = ProfileStore(tmp_path / "clusters")
    p = store.import_file(shared)
    assert p.path == tmp_path / "clusters" / "lynx.cluster.toml"
    assert p.connection.username == ""
    with pytest.raises(ProfileError):
        store.import_file(tmp_path / "missing.cluster.toml")


def test_file_names_and_default_folder(tmp_path):
    assert file_name("Lynx (GPU)") == "lynx-gpu.cluster.toml"
    assert file_name("***") == "cluster.cluster.toml"
    # conftest points XDG_CONFIG_HOME to a temporary folder
    assert "pytest" in str(cluster_profiles_dir())
