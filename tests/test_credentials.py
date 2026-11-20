import keyring
from keyring.backends import fail

from visual_workflows.core.credentials import CredentialStore, MemoryKeyring


def test_set_get_delete():
    st = CredentialStore(MemoryKeyring())
    assert st.available
    assert st.set("k", "v")
    assert st.get("k") == "v"
    st.delete("k")
    st.delete("k")          # forgetting twice is not an error
    assert st.get("k") is None


def test_default_backend_is_the_test_one():
    # conftest swaps the system keyring for a memory one
    assert isinstance(keyring.get_keyring(), MemoryKeyring)


def test_no_keyring_is_reported_not_raised():
    st = CredentialStore(fail.Keyring())
    assert not st.available
    assert st.get("k") is None
    assert not st.set("k", "v")
    assert st.last_error
