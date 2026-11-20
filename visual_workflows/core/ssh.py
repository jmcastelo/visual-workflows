"""SSH connection to a cluster: one connection for commands and files.

Login methods (ConnectionSettings.auth):
  key          a key file (asked for its passphrase only if it is encrypted),
               or the default keys and the ssh-agent if no file is given
  agent        the ssh-agent and the default keys
  password     the account password
  interactive  keyboard-interactive: the server asks its own questions, e.g. a
               password and then a one-time code

All four also accept keyboard-interactive after the main method, for clusters
that ask for a one-time code after the key or password.

~/.ssh/config is read (host aliases, HostName, User, Port, IdentityFile,
ProxyJump), so a host that works in a terminal works here.

Host keys are checked against ~/.ssh/known_hosts. An unknown key is shown to
the user (AuthPrompter.accept_host_key) and appended if accepted; a changed
key refuses the connection.

A dropped connection is reopened by the next operation. Commands are never
retried automatically: running sbatch twice would submit twice.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

import asyncssh

from .credentials import CredentialStore, passphrase_key, password_key
from .shell import login_shell
from .transport import (AuthenticationError, CommandResult, CommandTimeout, FileInfo,
                        HostKeyError, Transport, TransportError)

AUTH_METHODS = ("key", "agent", "password", "interactive")

# What reading a key with a wrong passphrase raises (depends on the key format)
_BAD_PASSPHRASE = (asyncssh.KeyImportError, asyncssh.KeyEncryptionError)

# Attempts at a password or passphrase before giving up
MAX_ATTEMPTS = 3

DEFAULT_KNOWN_HOSTS = Path("~/.ssh/known_hosts").expanduser()
DEFAULT_SSH_CONFIG = Path("~/.ssh/config").expanduser()


@dataclass
class ConnectionSettings:
    host: str                       # a host name or a ~/.ssh/config alias
    port: int | None = None         # None: from ~/.ssh/config, else 22
    username: str = ""              # empty: from ~/.ssh/config, else the local user
    auth: str = "key"
    key_file: str = ""              # empty: default keys and the ssh-agent
    connect_timeout: float = 30


@dataclass
class Secret:
    value: str
    remember: bool = False


class AuthPrompter(Protocol):
    """Asks the user during login. Returning None (or False) cancels."""

    async def password(self, username: str, host: str, *, retry: bool) -> Secret | None: ...

    async def passphrase(self, key_file: str, *, retry: bool) -> Secret | None: ...

    async def keyboard_interactive(self, name: str, instructions: str,
                                   prompts: list[tuple[str, bool]]) -> list[str] | None:
        """prompts: (text, echo) pairs; echo False means a hidden answer."""

    async def accept_host_key(self, host: str, port: int, algorithm: str,
                              fingerprint: str) -> bool: ...


class NonInteractivePrompter:
    """Never asks: logins work only with keys, the agent or remembered secrets."""

    async def password(self, username, host, *, retry):
        return None

    async def passphrase(self, key_file, *, retry):
        return None

    async def keyboard_interactive(self, name, instructions, prompts):
        return None

    async def accept_host_key(self, host, port, algorithm, fingerprint):
        return False


class _Login:
    """The secrets of one login attempt: what to try, remember and forget."""

    def __init__(self, username: str, host: str, port: int,
                 prompter: AuthPrompter, store: CredentialStore | None):
        self.username, self.host = username, host
        self.prompter, self.store = prompter, store
        self.cancelled = False
        self._pw_key = password_key(username, host, port)
        self._stored_pw = store.get(self._pw_key) if store else None
        self._pw_attempts = 0
        self._save: dict[str, str] = {}
        self._forget: set[str] = set()

    async def password(self) -> str | None:
        self._pw_attempts += 1
        if self._stored_pw:
            if self._pw_attempts == 1:
                return self._stored_pw
            self._forget.add(self._pw_key)    # the remembered one was refused
        if self._pw_attempts > MAX_ATTEMPTS + (1 if self._stored_pw else 0):
            return None
        secret = await self.prompter.password(self.username, self.host,
                                              retry=self._pw_attempts > 1)
        if secret is None:
            self.cancelled = True
            return None
        self._remember(self._pw_key, secret)
        return secret.value

    async def keyboard_interactive(self, name, instructions, lang, prompts):
        if not prompts:          # an empty challenge only shows a message
            return []
        if (len(prompts) == 1 and not prompts[0][1]
                and "password" in prompts[0][0].lower()):
            value = await self.password()
            return None if value is None else [value]
        answers = await self.prompter.keyboard_interactive(name, instructions, list(prompts))
        if answers is None:
            self.cancelled = True
        return answers

    async def load_key(self, key_file: str) -> asyncssh.SSHKey:
        path = os.path.expanduser(key_file)
        try:
            return asyncssh.read_private_key(path)
        except FileNotFoundError:
            raise AuthenticationError(f"Key file not found: {key_file}") from None
        except asyncssh.KeyImportError as e:
            if "passphrase" not in str(e).lower():
                raise AuthenticationError(f"Cannot read key {key_file}: {e}") from None
        # Encrypted: the remembered passphrase first, then ask
        key = passphrase_key(key_file)
        stored = self.store.get(key) if self.store else None
        if stored:
            try:
                return asyncssh.read_private_key(path, stored)
            except _BAD_PASSPHRASE:
                self._forget.add(key)
        for attempt in range(MAX_ATTEMPTS):
            secret = await self.prompter.passphrase(
                key_file, retry=attempt > 0 or bool(stored))
            if secret is None:
                self.cancelled = True
                raise AuthenticationError("Login cancelled")
            try:
                loaded = asyncssh.read_private_key(path, secret.value)
            except _BAD_PASSPHRASE:
                continue
            self._remember(key, secret)
            return loaded
        raise AuthenticationError(f"Wrong passphrase for {key_file}")

    def _remember(self, key: str, secret: Secret):
        if secret.remember:
            self._save[key] = secret.value
            self._forget.discard(key)
        else:
            self._save.pop(key, None)

    def finish(self, success: bool):
        """Forget refused secrets; on success, remember the ones asked to."""
        if not self.store:
            return
        for key in self._forget:
            self.store.delete(key)
        if success:
            for key, value in self._save.items():
                self.store.set(key, value)


class _Client(asyncssh.SSHClient):
    def __init__(self, login: _Login):
        self._login = login

    async def password_auth_requested(self):
        return await self._login.password()

    def kbdint_auth_requested(self):
        return ""        # no particular submethods

    async def kbdint_challenge_received(self, name, instructions, lang, prompts):
        return await self._login.keyboard_interactive(name, instructions, lang, prompts)


class SSHTransport(Transport):
    def __init__(self, settings: ConnectionSettings, prompter: AuthPrompter | None = None,
                 credentials: CredentialStore | None = None, *,
                 known_hosts: str | os.PathLike = DEFAULT_KNOWN_HOSTS,
                 ssh_config: str | os.PathLike | None = DEFAULT_SSH_CONFIG):
        if settings.auth not in AUTH_METHODS:
            raise ValueError(f"Unknown login method {settings.auth!r}; "
                             f"one of {', '.join(AUTH_METHODS)}")
        self.settings = settings
        self.prompter = prompter or NonInteractivePrompter()
        self.credentials = credentials
        self._known_hosts = Path(known_hosts)
        cfg = Path(ssh_config) if ssh_config else None
        self._config = [str(cfg)] if cfg and os.access(cfg, os.R_OK) else []
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._lock: asyncio.Lock | None = None

    # -- connection ---------------------------------------------------------

    def resolved(self) -> tuple[str, int, str]:
        """(host name, port, username) after applying ~/.ssh/config."""
        s = self.settings
        opts = asyncssh.SSHClientConnectionOptions(
            config=self._config, host=s.host, port=s.port or (),
            username=s.username or ())
        return opts.host, opts.port, opts.username

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closed()

    async def connect(self) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.is_connected:
                return
            await self._close_quietly()
            try:
                self._conn = await self._connect_once()
            except HostKeyError as e:
                if e.changed or not await self._offer_host_key():
                    raise
                self._conn = await self._connect_once()

    async def _connect_once(self) -> asyncssh.SSHClientConnection:
        s = self.settings
        host, port, username = self.resolved()
        login = _Login(username, host, port, self.prompter, self.credentials)
        options = dict(
            port=s.port or (), username=s.username or (), config=self._config,
            known_hosts=self._read_known_hosts(),
            client_factory=lambda: _Client(login),
            connect_timeout=s.connect_timeout, keepalive_interval=60)
        if s.auth in ("key", "agent"):
            options["preferred_auth"] = ("publickey", "keyboard-interactive")
            if s.auth == "key" and s.key_file:
                options["client_keys"] = [await login.load_key(s.key_file)]
        else:
            first = "password" if s.auth == "password" else "keyboard-interactive"
            second = "keyboard-interactive" if s.auth == "password" else "password"
            options.update(preferred_auth=(first, second), client_keys=None, agent_path=None)
        try:
            conn = await asyncssh.connect(s.host, **options)
        except asyncssh.HostKeyNotVerifiable as e:
            login.finish(False)
            raise HostKeyError(f"Host key of {host} not verified: {e}") from None
        except asyncssh.PermissionDenied:
            login.finish(False)
            raise AuthenticationError(
                "Login cancelled" if login.cancelled
                else f"Login to {username}@{host} refused") from None
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as e:
            login.finish(False)
            raise TransportError(f"Cannot connect to {s.host}: {e or type(e).__name__}") from None
        login.finish(True)
        return conn

    def _read_known_hosts(self) -> asyncssh.SSHKnownHosts:
        try:
            text = self._known_hosts.read_text()
        except FileNotFoundError:
            text = ""
        return asyncssh.import_known_hosts(text)

    async def _offer_host_key(self) -> bool:
        """Show the server's key if it is unknown; True if the user accepts it.

        Raises HostKeyError(changed=True) if known_hosts has a different key
        for this host.
        """
        host, port, _ = self.resolved()
        try:
            key = await asyncssh.get_server_host_key(
                self.settings.host, self.settings.port or (), config=self._config)
        except (asyncssh.Error, OSError) as e:
            raise TransportError(f"Cannot read the host key of {host}: {e}") from None
        if key is None:
            raise TransportError(f"{host} sent no host key")
        fingerprint = key.get_fingerprint("sha256")
        known = self._read_known_hosts().match(host, "", port)
        if any(known):
            raise HostKeyError(
                f"The host key of {host} has CHANGED (now {fingerprint}). This can "
                f"mean the cluster was reinstalled, or that someone is intercepting "
                f"the connection. Check with the cluster's administrators, then "
                f"remove the old key from {self._known_hosts}.", changed=True)
        if not await self.prompter.accept_host_key(host, port, key.get_algorithm(), fingerprint):
            return False
        self._append_known_host(host, port, key)
        return True

    def _append_known_host(self, host: str, port: int, key: asyncssh.SSHKey):
        pattern = host if port == 22 else f"[{host}]:{port}"
        algorithm, data = key.export_public_key("openssh").decode().split()[:2]
        path = self._known_hosts
        if not path.parent.exists():
            path.parent.mkdir(mode=0o700, parents=True)
        needs_newline = path.exists() and path.stat().st_size > 0 \
            and not path.read_bytes().endswith(b"\n")
        with open(path, "a") as f:
            f.write(("\n" if needs_newline else "") + f"{pattern} {algorithm} {data}\n")

    async def close(self) -> None:
        await self._close_quietly()

    async def _close_quietly(self):
        if self._sftp is not None:
            self._sftp.exit()
            self._sftp = None
        if self._conn is not None:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            except Exception:
                pass
            self._conn = None

    async def _connection(self) -> asyncssh.SSHClientConnection:
        if not self.is_connected:
            await self.connect()
        return self._conn

    def _lost(self, e: Exception) -> TransportError:
        """Forget a broken connection, so the next operation reconnects."""
        self._conn = None
        self._sftp = None
        return TransportError(f"Connection to {self.settings.host} lost: {e}")

    # -- commands -----------------------------------------------------------

    async def run(self, command, *, timeout=60, login=True):
        conn = await self._connection()
        line = login_shell(command) if login else command
        try:
            r = await conn.run(line, check=False, timeout=timeout, errors="replace")
        except asyncssh.TimeoutError:
            raise CommandTimeout(f"Timed out after {timeout} s: {command}") from None
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError, OSError) as e:
            raise self._lost(e) from None
        except asyncssh.ChannelOpenError as e:
            raise TransportError(f"Cannot run a command on {self.settings.host}: {e}") from None
        code = r.exit_status if r.exit_status is not None else -1
        return CommandResult(command, code, r.stdout or "", r.stderr or "")

    # -- files --------------------------------------------------------------

    async def _sftp_client(self) -> asyncssh.SFTPClient:
        conn = await self._connection()
        if self._sftp is None:
            try:
                self._sftp = await conn.start_sftp_client()
            except (asyncssh.ConnectionLost, asyncssh.DisconnectError, OSError) as e:
                raise self._lost(e) from None
            except asyncssh.Error as e:
                raise TransportError(f"No file transfer (SFTP) on {self.settings.host}: {e}") from None
        return self._sftp

    async def _files(self, operation, *args, **kwargs):
        sftp = await self._sftp_client()
        try:
            return await operation(sftp, *args, **kwargs)
        except asyncssh.SFTPNoSuchFile as e:
            raise TransportError(f"No such file or directory: {e.reason}") from None
        except asyncssh.SFTPError as e:
            raise TransportError(f"File operation failed: {e.reason}") from None
        except (asyncssh.ConnectionLost, asyncssh.DisconnectError) as e:
            raise self._lost(e) from None

    async def home(self):
        return await self._files(lambda sftp: sftp.realpath("."))

    @staticmethod
    def _info(path: str, name: str, attrs: asyncssh.SFTPAttrs) -> FileInfo:
        is_dir = attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY
        return FileInfo(path, name, attrs.size or 0, float(attrs.mtime or 0), is_dir)

    async def stat(self, path):
        async def op(sftp):
            try:
                attrs = await sftp.stat(path)
            except asyncssh.SFTPNoSuchFile:
                return None
            return self._info(path, PurePosixPath(path).name, attrs)
        return await self._files(op)

    async def listdir(self, path):
        async def op(sftp):
            names = await sftp.readdir(path)
            return sorted((self._info(str(PurePosixPath(path) / n.filename), n.filename, n.attrs)
                           for n in names if n.filename not in (".", "..")),
                          key=lambda f: f.name)
        return await self._files(op)

    async def makedirs(self, path):
        await self._files(lambda sftp: sftp.makedirs(path, exist_ok=True))

    async def _make_parent(self, sftp, remote: str):
        parent = str(PurePosixPath(remote).parent)
        if parent not in (".", "/"):
            await sftp.makedirs(parent, exist_ok=True)

    async def put(self, local, remote):
        async def op(sftp):
            await self._make_parent(sftp, remote)
            await sftp.put(os.fspath(local), remote, preserve=True)
        await self._files(op)

    async def get(self, remote, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        await self._files(lambda sftp: sftp.get(remote, os.fspath(local), preserve=True))

    async def write_text(self, remote, text):
        async def op(sftp):
            await self._make_parent(sftp, remote)
            async with sftp.open(remote, "wb") as f:
                await f.write(text.encode())
        await self._files(op)

    async def read_text(self, remote):
        async def op(sftp):
            async with sftp.open(remote, "rb") as f:
                return (await f.read()).decode(errors="replace")
        return await self._files(op)
