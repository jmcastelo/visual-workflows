# Installation

[← Documentation](../README.md#documentation)

## Requirements

- **On your PC:** Python 3.11 or newer. Developed and tested on Linux; the
  code has nothing Linux-specific, but macOS and Windows are untested.
- **On the cluster:** SSH access, Slurm, and the usual Linux tools (`bash`,
  GNU `find`). Nothing is installed there. Environment Modules or Lmod are
  used if present.

## Installing

In a virtual environment, so that its packages do not mix with the system's:

```
python -m venv visual-workflows-env
source visual-workflows-env/bin/activate        # on Windows: visual-workflows-env\Scripts\activate
pip install git+https://github.com/jmcastelo/visual-workflows.git
```

This installs two commands:

- `visual-workflows`: the graphical program ([The main window](interface.md)).
- `vwf`: the command-line tool ([Command line](command-line.md)).

Activate the environment (`source …/activate`) before using them in a new
terminal.

## Dependencies

Installed with it:

| Package | For |
|---|---|
| [PySide6-Essentials](https://doc.qt.io/qtforpython-6) | the window (Qt) |
| [asyncssh](https://asyncssh.readthedocs.io) with `bcrypt` | SSH connections, commands and file transfer; `bcrypt` reads encrypted keys in OpenSSH's format (what `ssh-keygen` writes) |
| [qasync](https://github.com/CabbageDevelopment/qasync) | running the connection code inside Qt's event loop, so the window never freezes |
| [keyring](https://github.com/jaraco/keyring) | remembering passwords and key passphrases in the system's keyring |
| [tomli-w](https://github.com/hukkin/tomli-w) | writing cluster profiles and projects (TOML) |

## The system keyring

When you log in with a password or an encrypted key, you can tick **Remember
it in the system keyring**. It is then stored by your operating system's
password store, never in a file of the program:

- Linux: the Secret Service (GNOME Keyring, KWallet) of your desktop.
- macOS: the Keychain. Windows: the Credential Manager.

Without a usable keyring (a Linux system without a desktop, for example) the
option is not offered and you are asked at each connection. Passwords are
stored under the service name `visual-workflows`; remove them with your
desktop's password manager. One-time codes are never stored.

## Where the program keeps its files

| What | Where (Linux) |
|---|---|
| Cluster profiles | `~/.config/visual-suite/visual-workflows/clusters/*.cluster.toml` |
| Settings (recent projects) | `~/.config/visual-suite/visual-workflows.conf` |
| Known hosts | your `~/.ssh/known_hosts`, as `ssh` uses it |
| Projects | wherever you create them |

On macOS the configuration folder is `~/Library/Preferences`, on Windows
`%APPDATA%`.

## Updating

```
pip install --upgrade git+https://github.com/jmcastelo/visual-workflows.git
```

Profiles and projects carry a format version: newer versions of the program
read older files, and older versions read newer files as far as they can,
keeping what they do not understand.
