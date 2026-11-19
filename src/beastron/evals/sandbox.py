"""A macOS sandbox for the agent's shell commands during evals.

Evals approve every action automatically, so a model-written command like
`rm -rf ~` must not be able to do harm. Each `run_shell` command is wrapped in
`sandbox-exec` with a profile that denies all file writes except:
- the run's own folder (the workspace and its scratch space)
- the system temp folders (compilers and test runners write there)
- build-tool caches: ~/.m2, ~/.gradle, ~/.cache (offline Maven and pytest need them)
- /dev/null, terminals and file descriptors

Reading and network access stay allowed. `sandbox-exec` exists only on macOS;
elsewhere the runner refuses to run unless sandboxing is explicitly turned off.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

CACHES = ("~/.m2", "~/.gradle", "~/.cache")


class SandboxError(Exception):
    pass


def available() -> bool:
    return platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None


def profile(writable: list[Path], regexes: tuple[str, ...] = ()) -> str:
    """The sandbox rules: deny file writes, except under `writable`, the temp folders, the caches,
    and paths matching `regexes` (for files replaced atomically, such as ~/.claude.json)."""
    temp = {Path("/private/tmp"), Path("/private/var/folders"), Path(tempfile.gettempdir()).resolve()}
    caches = {Path(c).expanduser() for c in CACHES}
    paths = sorted({p.resolve() for p in writable} | temp | {c.resolve() if c.exists() else c for c in caches})
    rules = " ".join([f'(subpath "{_quote(p)}")' for p in paths] + [f'(regex #"{r}")' for r in regexes])
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        f"(allow file-write* {rules}\n"
        '  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper")\n'
        '  (regex #"^/dev/tty") (regex #"^/dev/fd/"))\n'
    )


def command_prefix(writable: list[Path], regexes: tuple[str, ...] = ()) -> list[str]:
    """Put this in front of `/bin/sh -c <command>` to run the command in the sandbox."""
    if not available():
        raise SandboxError("sandbox-exec is not available (it exists only on macOS)")
    rules = profile(writable, regexes)
    probe = subprocess.run(["sandbox-exec", "-p", rules, "/usr/bin/true"], capture_output=True, text=True)
    if probe.returncode != 0:
        raise SandboxError(f"sandbox-exec rejected the profile: {probe.stderr.strip()}")
    return ["sandbox-exec", "-p", rules]


def _quote(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')
