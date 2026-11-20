"""An SSH server inside the test process, to test SSHTransport for real.

Commands run on this PC with the server's root folder as their home and
working folder; SFTP is confined (chroot) to that folder.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh

from visual_workflows.core.ssh import Secret


@dataclass
class ServerConfig:
    root: Path
    username: str = "alice"
    password: str = "secret"
    otp: str | None = None                 # one-time code asked after the password
    methods: tuple = ("password",)         # of "password", "kbdint", "publickey"
    authorized_keys: list = field(default_factory=list)
    host_key: asyncssh.SSHKey = field(
        default_factory=lambda: asyncssh.generate_private_key("ssh-ed25519"))
    password_attempts: int = 0


class _Server(asyncssh.SSHServer):
    def __init__(self, cfg: ServerConfig):
        self.cfg = cfg
        self._stage = 0

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return "password" in self.cfg.methods

    def validate_password(self, username, password):
        self.cfg.password_attempts += 1
        return username == self.cfg.username and password == self.cfg.password

    def kbdint_auth_supported(self):
        return "kbdint" in self.cfg.methods

    def get_kbdint_challenge(self, username, lang, submethods):
        self._stage = 0
        return "", "", "", [("Password: ", False)]

    def validate_kbdint_response(self, username, responses):
        if self._stage == 0:
            if username != self.cfg.username or responses != [self.cfg.password]:
                return False
            if self.cfg.otp is None:
                return True
            self._stage = 1
            return "", "Two-factor authentication", "", [("Verification code: ", True)]
        return responses == [self.cfg.otp]


async def _run_command(process: asyncssh.SSHServerProcess, root: Path):
    proc = await asyncio.create_subprocess_shell(
        process.command, cwd=root, env=dict(os.environ, HOME=str(root)),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    process.stdout.write(out.decode())
    process.stderr.write(err.decode())
    process.exit(proc.returncode)


async def start_server(cfg: ServerConfig):
    """Start the server; returns (server, port)."""
    options = {}
    if "publickey" in cfg.methods:
        options["authorized_client_keys"] = asyncssh.import_authorized_keys(
            "".join(k.export_public_key().decode() for k in cfg.authorized_keys))
    server = await asyncssh.create_server(
        lambda: _Server(cfg), "127.0.0.1", 0,
        server_host_keys=[cfg.host_key],
        process_factory=lambda p: _run_command(p, cfg.root),
        sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=str(cfg.root)),
        **options)
    return server, server.sockets[0].getsockname()[1]


def known_hosts_line(port: int, key: asyncssh.SSHKey) -> str:
    return f"[127.0.0.1]:{port} " + key.export_public_key().decode()


class FakePrompter:
    """Answers from queues and records what it was asked."""

    def __init__(self, passwords=(), passphrases=(), kbdint=(), accept_host=False):
        self.passwords = list(passwords)      # Secret, or None to cancel
        self.passphrases = list(passphrases)
        self.kbdint = list(kbdint)            # lists of answers
        self.accept_host = accept_host
        self.asked: list[tuple] = []

    async def password(self, username, host, *, retry):
        self.asked.append(("password", username, retry))
        return self.passwords.pop(0) if self.passwords else None

    async def passphrase(self, key_file, *, retry):
        self.asked.append(("passphrase", key_file, retry))
        return self.passphrases.pop(0) if self.passphrases else None

    async def keyboard_interactive(self, name, instructions, prompts):
        self.asked.append(("kbdint", [p for p, _ in prompts]))
        return self.kbdint.pop(0) if self.kbdint else None

    async def accept_host_key(self, host, port, algorithm, fingerprint):
        self.asked.append(("host_key", host, port, fingerprint))
        return self.accept_host


def S(value, remember=False):
    return Secret(value, remember)
