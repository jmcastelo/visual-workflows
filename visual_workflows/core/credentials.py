"""Passwords and key passphrases kept in the operating system's keyring.

Secrets are never written to files. They are stored only when the user asks
("remember"), under identity.KEYRING_SERVICE. One-time codes are never stored.

Without a usable keyring (some Linux setups have none) nothing is remembered
and the user is asked at each connection; that is reported, not an error.
"""

from __future__ import annotations

import os

import keyring
import keyring.backend
from keyring.errors import KeyringError, PasswordDeleteError

from ..identity import KEYRING_SERVICE


def password_key(username: str | None, host: str, port: int) -> str:
    return f"password:{username or ''}@{host}:{port}"


def passphrase_key(key_file: str | os.PathLike) -> str:
    return f"passphrase:{os.path.abspath(os.path.expanduser(key_file))}"


class CredentialStore:
    """Get, remember and forget secrets.

    `backend` is a keyring backend; the default is the system's. Tests pass
    an in-memory one so the user's keyring is never touched.
    """

    def __init__(self, backend: keyring.backend.KeyringBackend | None = None,
                 service: str = KEYRING_SERVICE):
        self._backend = backend
        self._service = service
        self.last_error: str | None = None

    def _keyring(self):
        return self._backend if self._backend is not None else keyring.get_keyring()

    @property
    def available(self) -> bool:
        """Whether secrets can be remembered at all."""
        try:
            kr = self._keyring()
        except Exception:
            return False
        # The "fail" backend is what keyring falls back to when it finds none
        return type(kr).__module__ != "keyring.backends.fail" and kr.priority > 0

    def get(self, key: str) -> str | None:
        try:
            return self._keyring().get_password(self._service, key)
        except (KeyringError, RuntimeError) as e:
            self.last_error = str(e)
            return None

    def set(self, key: str, secret: str) -> bool:
        """Remember a secret; False (and last_error set) if it could not be."""
        try:
            self._keyring().set_password(self._service, key, secret)
            return True
        except (KeyringError, RuntimeError) as e:
            self.last_error = str(e)
            return False

    def delete(self, key: str) -> None:
        try:
            self._keyring().delete_password(self._service, key)
        except PasswordDeleteError:
            pass
        except (KeyringError, RuntimeError) as e:
            self.last_error = str(e)


class MemoryKeyring(keyring.backend.KeyringBackend):
    """A keyring kept in memory: for tests, and when no keyring exists."""

    priority = 1

    def __init__(self):
        super().__init__()
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._data.get((service, username))

    def set_password(self, service, username, password):
        self._data[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self._data[(service, username)]
        except KeyError:
            raise PasswordDeleteError(username) from None
