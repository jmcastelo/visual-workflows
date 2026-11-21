import os

import keyring
import pytest

from visual_workflows.core.credentials import MemoryKeyring

# The user's own configuration, for live tests (taken before tests change it)
REAL_XDG_CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME")


def pytest_addoption(parser):
    parser.addoption("--cluster", action="append", default=[], metavar="NAME",
                     help="run the live tests against this cluster profile (read-only; "
                          "may be given more than once)")
    parser.addoption("--record-to", metavar="DIR",
                     help="with --cluster: also record each cluster into DIR/<name> "
                          "(the layout of tests/data)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--cluster"):
        return
    skip = pytest.mark.skip(reason="live test: run with --cluster NAME")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated(request, tmp_path_factory, monkeypatch):
    """Never touch the user's keyring, config folder or ssh-agent (except in
    live tests, which use the user's own profiles and logins)."""
    if request.node.get_closest_marker("live"):
        if REAL_XDG_CONFIG_HOME is None:
            monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        else:
            monkeypatch.setenv("XDG_CONFIG_HOME", REAL_XDG_CONFIG_HOME)
        yield
        return
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    yield
    keyring.set_keyring(previous)
