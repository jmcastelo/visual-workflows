import visual_workflows
from visual_workflows import identity


def test_version():
    assert visual_workflows.__version__


def test_shared_formats_do_not_carry_the_program_name():
    # Shared files must keep loading after the suite is renamed
    for fmt in (identity.FORMAT_CLUSTER_PROFILE, identity.FORMAT_PROJECT):
        assert "visual" not in fmt.lower()
