"""Building shell commands to run on a cluster."""

import shlex

quote = shlex.quote


def login_shell(command: str) -> str:
    """Wrap a command to run in a bash login shell.

    Clusters set up `module`, the site's PATH and the user's environment in
    login shells only: a command run directly over SSH gets a non-login,
    non-interactive shell where `module` usually does not exist.
    """
    return "bash -lc " + quote(command)


def join(args) -> str:
    """One command line from a list of arguments, each quoted."""
    return " ".join(quote(str(a)) for a in args)
