import asyncssh
import pytest

from sshserver import FakePrompter, S, ServerConfig, known_hosts_line, start_server
from visual_workflows.core.credentials import (CredentialStore, MemoryKeyring, passphrase_key,
                                               password_key)
from visual_workflows.core.ssh import ConnectionSettings, SSHTransport
from visual_workflows.core.transport import (AuthenticationError, CommandTimeout,
                                             HostKeyError, TransportError)


@pytest.fixture
async def server(tmp_path):
    """A running server; tests change its config before connecting."""
    root = tmp_path / "remote"
    root.mkdir()
    cfg = ServerConfig(root=root)
    srv, port = await start_server(cfg)
    cfg.port = port
    known = tmp_path / "known_hosts"
    known.write_text(known_hosts_line(port, cfg.host_key))
    cfg.known_hosts = known
    yield cfg
    srv.close()
    await srv.wait_closed()


def transport(cfg, prompter=None, store=None, **settings):
    s = ConnectionSettings(host="127.0.0.1", port=cfg.port, username=cfg.username,
                           auth=settings.pop("auth", "password"), **settings)
    return SSHTransport(s, prompter, store, known_hosts=cfg.known_hosts, ssh_config=None)


def store():
    return CredentialStore(MemoryKeyring())


# -- password -------------------------------------------------------------------

async def test_password_login_and_commands(server):
    p = FakePrompter(passwords=[S("secret")])
    async with transport(server, p) as t:
        r = await t.run("echo out; echo err >&2; exit 3")
        assert (r.exit_code, r.stdout, r.stderr) == (3, "out\n", "err\n")
        assert not r.ok
        with pytest.raises(TransportError, match="exit code 3"):
            r.check()
        # A login shell, so `module` and the site's setup would be there
        assert (await t.run("shopt -q login_shell && echo login")).stdout == "login\n"
    assert p.asked == [("password", "alice", False)]


async def test_wrong_password_asks_again(server):
    p = FakePrompter(passwords=[S("wrong"), S("secret")])
    async with transport(server, p) as t:
        assert t.is_connected
    assert [a[2] for a in p.asked] == [False, True]


async def test_cancelled_login(server):
    with pytest.raises(AuthenticationError, match="cancelled"):
        await transport(server, FakePrompter()).connect()


async def test_remembered_password(server):
    st = store()
    p = FakePrompter(passwords=[S("secret", remember=True)])
    async with transport(server, p, st):
        pass
    key = password_key("alice", "127.0.0.1", server.port)
    assert st.get(key) == "secret"
    # Next time: no question
    p2 = FakePrompter()
    async with transport(server, p2, st):
        pass
    assert p2.asked == []


async def test_not_remembered_unless_asked(server):
    st = store()
    async with transport(server, FakePrompter(passwords=[S("secret")]), st):
        pass
    assert st.get(password_key("alice", "127.0.0.1", server.port)) is None


async def test_refused_remembered_password_is_forgotten(server):
    st = store()
    key = password_key("alice", "127.0.0.1", server.port)
    st.set(key, "old")
    p = FakePrompter(passwords=[None])
    with pytest.raises(AuthenticationError):
        await transport(server, p, st).connect()
    assert st.get(key) is None
    assert p.asked == [("password", "alice", True)]


# -- keys -----------------------------------------------------------------------

async def test_key_without_passphrase_asks_nothing(server, tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.write_private_key(tmp_path / "id")
    server.methods, server.authorized_keys = ("publickey",), [key]
    srv, server.port = await start_server(server)
    (tmp_path / "known_hosts").write_text(known_hosts_line(server.port, server.host_key))
    p = FakePrompter()
    async with transport(server, p, auth="key", key_file=str(tmp_path / "id")) as t:
        assert (await t.run("echo ok")).stdout == "ok\n"
    assert p.asked == []
    srv.close()


async def test_encrypted_key(server, tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    key.write_private_key(tmp_path / "id", passphrase="pp")
    server.methods, server.authorized_keys = ("publickey",), [key]
    srv, server.port = await start_server(server)
    (tmp_path / "known_hosts").write_text(known_hosts_line(server.port, server.host_key))
    st = store()
    p = FakePrompter(passphrases=[S("bad"), S("pp", remember=True)])
    async with transport(server, p, st, auth="key", key_file=str(tmp_path / "id")):
        pass
    assert [a[0] for a in p.asked] == ["passphrase", "passphrase"]
    assert st.get(passphrase_key(tmp_path / "id")) == "pp"
    srv.close()


async def test_missing_key_file(server, tmp_path):
    with pytest.raises(AuthenticationError, match="not found"):
        await transport(server, auth="key", key_file=str(tmp_path / "nope")).connect()


# -- keyboard-interactive ---------------------------------------------------------

async def test_password_and_one_time_code(server):
    server.methods, server.otp = ("kbdint",), "123456"
    p = FakePrompter(passwords=[S("secret")], kbdint=[["123456"]])
    async with transport(server, p, auth="interactive") as t:
        assert t.is_connected
    assert p.asked == [("password", "alice", False), ("kbdint", ["Verification code: "])]


# -- host keys --------------------------------------------------------------------

async def test_unknown_host_key_accepted(server):
    server.known_hosts.write_text("")
    p = FakePrompter(passwords=[S("secret")], accept_host=True)
    async with transport(server, p):
        pass
    assert p.asked[0][0] == "host_key"
    assert p.asked[0][3] == server.host_key.get_fingerprint("sha256")
    assert f"[127.0.0.1]:{server.port} ssh-ed25519 " in server.known_hosts.read_text()
    # Known now: not asked again
    p2 = FakePrompter(passwords=[S("secret")])
    async with transport(server, p2):
        pass
    assert [a[0] for a in p2.asked] == ["password"]


async def test_unknown_host_key_rejected(server):
    server.known_hosts.write_text("")
    with pytest.raises(HostKeyError) as e:
        await transport(server, FakePrompter(passwords=[S("secret")])).connect()
    assert not e.value.changed
    assert server.known_hosts.read_text() == ""


async def test_changed_host_key_refused_without_asking(server):
    other = asyncssh.generate_private_key("ssh-ed25519")
    server.known_hosts.write_text(known_hosts_line(server.port, other))
    p = FakePrompter(passwords=[S("secret")], accept_host=True)
    with pytest.raises(HostKeyError, match="CHANGED") as e:
        await transport(server, p).connect()
    assert e.value.changed
    assert not any(a[0] == "host_key" for a in p.asked)


# -- connection -------------------------------------------------------------------

async def test_unreachable_host(server):
    srv_port = server.port
    server.port = 1      # nothing listens there
    with pytest.raises(TransportError, match="Cannot connect"):
        await transport(server).connect()
    server.port = srv_port


async def test_reconnects_after_a_drop(server):
    st = store()
    p = FakePrompter(passwords=[S("secret", remember=True)])
    async with transport(server, p, st) as t:
        t._conn.close()
        await t._conn.wait_closed()
        assert not t.is_connected
        assert (await t.run("echo back")).stdout == "back\n"


async def test_command_timeout(server):
    async with transport(server, FakePrompter(passwords=[S("secret")])) as t:
        with pytest.raises(CommandTimeout):
            await t.run("sleep 5", timeout=0.3)
        # The connection is still usable
        assert (await t.run("echo ok")).ok


# -- files ----------------------------------------------------------------------

async def test_files(server, tmp_path):
    local = tmp_path / "in.txt"
    local.write_text("K_POINTS automatic\n")
    async with transport(server, FakePrompter(passwords=[S("secret")])) as t:
        assert await t.home() == "/"
        await t.put(local, "proj/01-scf/pw.in")
        info = await t.stat("proj/01-scf/pw.in")
        assert info.size == local.stat().st_size and not info.is_dir
        assert abs(info.mtime - local.stat().st_mtime) < 1
        assert await t.stat("proj/none") is None
        await t.write_text("proj/02-nscf/job.sh", "#!/bin/bash\n")
        await t.makedirs("proj/03-p2y")
        listing = await t.listdir("proj")
        assert [(f.name, f.is_dir) for f in listing] == [
            ("01-scf", True), ("02-nscf", True), ("03-p2y", True)]
        assert listing[0].path == "proj/01-scf"
        assert await t.read_text("proj/02-nscf/job.sh") == "#!/bin/bash\n"
        await t.get("proj/01-scf/pw.in", tmp_path / "back" / "pw.in")
        assert (tmp_path / "back" / "pw.in").read_text() == "K_POINTS automatic\n"
        with pytest.raises(TransportError, match="No such file"):
            await t.read_text("proj/none")
    assert (server.root / "proj/02-nscf/job.sh").exists()


async def test_ssh_config_alias(server, tmp_path):
    config = tmp_path / "ssh_config"
    config.write_text(f"Host lynx\n  HostName 127.0.0.1\n  Port {server.port}\n  User alice\n")
    s = ConnectionSettings(host="lynx", auth="password")
    t = SSHTransport(s, FakePrompter(passwords=[S("secret")]),
                     known_hosts=server.known_hosts, ssh_config=config)
    assert t.resolved() == ("127.0.0.1", server.port, "alice")
    async with t:
        assert (await t.run("echo ok")).ok


async def test_read_range_and_resumed_download(server, tmp_path):
    data = bytes(range(256)) * 9000          # 2.3 MB: several chunks
    (server.root / "big.bin").write_bytes(data)
    async with transport(server, FakePrompter(passwords=[S("secret")])) as t:
        assert await t.read_range("big.bin", 10, 5) == data[10:15]
        assert await t.read_range("big.bin", len(data) - 2, 100) == data[-2:]
        part = tmp_path / "big.bin.part"
        part.write_bytes(data[:1000])          # an interrupted download
        seen = []
        await t.download("big.bin", part, offset=1000, progress=seen.append)
        assert part.read_bytes() == data and sum(seen) == len(data) - 1000
        with pytest.raises(TransportError, match="No such file"):
            await t.download("none.bin", tmp_path / "x")
