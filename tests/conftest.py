import keyring
import pytest

from visual_workflows.core.credentials import MemoryKeyring


@pytest.fixture(autouse=True)
def isolated(tmp_path_factory, monkeypatch):
    """Never touch the user's keyring, config folder or ssh-agent."""
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    yield
    keyring.set_keyring(previous)
