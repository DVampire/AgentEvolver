"""Allowlisted filesystem view for a trusted Python worker with private host inputs.

Unlike owned_command, this does not bind the host root. Network access is retained
for model endpoints; capability policy must separately restrict executable tools.
"""

import shutil
import sys
from pathlib import Path


def isolated_command(argv: list[str], *, readonly: list[str], writable: list[str],
                     cwd: str, environment: dict[str, str] | None = None) -> list[str]:
    helper = shutil.which("bwrap")
    if sys.platform != "linux" or not helper:
        raise RuntimeError("filesystem-isolated workers require Linux bubblewrap; no unsafe fallback")
    read_paths = [Path(p).resolve(strict=True) for p in readonly]
    write_paths = [Path(p).resolve(strict=True) for p in writable]
    if Path("/") in read_paths + write_paths:
        raise ValueError("binding the host root defeats filesystem isolation")
    workdir = Path(cwd).resolve(strict=True)
    if not any(workdir == p or p in workdir.parents for p in read_paths + write_paths):
        raise ValueError("worker cwd must be explicitly mounted")
    command = [helper, "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
               "--unshare-ipc", "--unshare-uts", "--cap-drop", "ALL", "--tmpfs", "/"]
    # Runtime libraries, DNS and CA roots, not user homes, /run sockets or host /proc.
    for name in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        path = Path(name)
        if path.is_symlink():
            command.extend(["--symlink", str(path.readlink()), name])
        elif path.exists():
            command.extend(["--ro-bind", name, name])
    for name in ("/etc/ssl", "/etc/ca-certificates", "/etc/resolv.conf", "/etc/hosts",
                 "/etc/nsswitch.conf", "/etc/passwd", "/etc/group", "/etc/localtime"):
        if Path(name).exists():
            command.extend(["--ro-bind", name, name])
    command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"])
    for path in sorted(set(read_paths), key=lambda p: len(p.parts)):
        command.extend(["--ro-bind", str(path), str(path)])
    for path in sorted(set(write_paths), key=lambda p: len(p.parts)):
        command.extend(["--bind", str(path), str(path)])
    command.extend(["--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"])
    for key, value in (environment or {}).items():
        command.extend(["--setenv", key, str(value)])
    return [*command, "--chdir", str(workdir), "--", *argv]
