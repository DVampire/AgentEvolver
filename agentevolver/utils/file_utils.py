import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Union
from datetime import datetime

try:
    import fcntl
except ImportError:                                          # noqa: F401 — non-POSIX
    fcntl = None

from agentevolver.utils.singleton import Singleton

def format_size(size_bytes: int) -> str:
    """Format file size in human readable format (from project.py)."""
    if size_bytes == 0:
        return "0 B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f} {size_names[i]}"

def get_file_info(file_path: str) -> Dict[str, Any]:
    """Get file information."""
    abs_path = os.path.abspath(file_path)
    
    info = {}
    file_stats = os.stat(abs_path)

    info["path"] = abs_path
    info["size"] = format_size(file_stats.st_size)
    info["created"] = datetime.fromtimestamp(file_stats.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
    info["modified"] = datetime.fromtimestamp(file_stats.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    info["accessed"] = datetime.fromtimestamp(file_stats.st_atime).strftime("%Y-%m-%d %H:%M:%S")
    info["permissions"] = oct(file_stats.st_mode)[-3:]
    info["is_directory"] = os.path.isdir(abs_path)
    info["is_file"] = os.path.isfile(abs_path)
    info["is_symlink"] = os.path.islink(abs_path)
    
    return info

class FileLock(metaclass=Singleton):
    def __init__(self):
        self._locks = {}

    def get_lock(self, key: Union[str]) -> asyncio.Lock:
        # Convert Path to string if needed (for backward compatibility)
        key_str = str(key) if not isinstance(key, str) else key
        if key_str not in self._locks:
            self._locks[key_str] = asyncio.Lock()
        return self._locks[key_str]

    def __call__(self, key):
        return _FileLockContext(self.get_lock(key))


class _FileLockContext:
    def __init__(self, lock):
        self._lock = lock

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()
        
file_lock = FileLock()


@contextlib.contextmanager
def _cross_process_lock(path: Path):
    """Hold an exclusive lock on a sidecar `.lock` file for the length of the block.

    `FileLock` above is an `asyncio.Lock`: it serialises coroutines inside one process
    and means nothing to a second process. Several process-global registries —
    `ports.json`, the sandbox crash ledger — are read-modify-written, and the framework
    has always assumed one instance per tree root because of it. Running ProgramBench
    instances concurrently breaks that assumption, and an unlocked read-modify-write
    there does not merely lose an update: the writers share a fixed temp filename, so an
    `os.replace` can publish another writer's half-written file.

    The lock is a separate `<path>.lock` rather than the file itself, so the exclusive
    hold is independent of the atomic replace that swaps the real file underneath it.
    Without `fcntl` (non-POSIX) it is a no-op — the single-instance assumption then holds
    as it always did, rather than the process failing.
    """
    if fcntl is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = open(lock_path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def atomic_json_update(path: Union[str, Path], mutate: Callable[[Any], Any], *,
                       default: Any = None) -> Any:
    """Read a JSON file, transform it, and write it back — atomically, across processes.

    `mutate` receives the current value (or `default` when the file is absent or
    unreadable) and returns the value to store. The whole read-modify-write is under one
    cross-process lock, so two concurrent callers serialise instead of clobbering, and
    the write is a temp-file replace named by pid so their temp files never collide.

    Returns whatever `mutate` returned, so a caller can act on the value it just stored.
    """
    path = Path(path)
    with _cross_process_lock(path):
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = default
        updated = mutate(current)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return updated