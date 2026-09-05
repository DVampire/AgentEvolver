"""Filesystem metadata plus process-safe durable write helpers."""

import asyncio
import contextlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Union

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
        return _FileLockContext(self.get_lock(key), Path(str(key)))


class _FileLockContext:
    def __init__(self, lock, path: Path):
        self._lock = lock
        self._path = path
        self._handle = None

    async def __aenter__(self):
        await self._lock.acquire()
        if fcntl is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                lock_path = self._path.with_suffix(self._path.suffix + ".lock")
                self._handle = open(lock_path, "a+")
                # A blocking flock in to_thread outlives cancellation of its
                # awaiter. Poll nonblocking instead so shutdown owns every wait.
                while True:
                    try:
                        fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(0.05)
            except BaseException:
                if self._handle is not None:
                    self._handle.close()
                    self._handle = None
                self._lock.release()
                raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._handle is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
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
                       default: Any = None, recover_corrupt: bool = True) -> Any:
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
        except FileNotFoundError:
            current = default
        except ValueError:
            if not recover_corrupt:
                raise
            current = default
        updated = mutate(current)
        _atomic_replace_text_unlocked(
            path, json.dumps(updated, indent=2, ensure_ascii=False),
        )
        return updated


def atomic_write_text(path: Union[str, Path], text: str) -> None:
    """Durably replace one file under a cross-process sidecar lock."""
    path = Path(path)
    with _cross_process_lock(path):
        _atomic_replace_text_unlocked(path, str(text))


def atomic_text_update(
    path: Union[str, Path], mutate: Callable[[str], str], *, default: str = "",
) -> str:
    """Cross-process-safe read/modify/write for a UTF-8 text document."""
    path = Path(path)
    with _cross_process_lock(path):
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = default
        updated = str(mutate(current))
        _atomic_replace_text_unlocked(path, updated)
        return updated


def _atomic_replace_text_unlocked(path: Path, text: str) -> None:
    """Replace ``path`` durably; the caller must hold its sidecar lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.{os.getpid()}-", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def append_jsonl(path: Union[str, Path], value: Any) -> None:
    """Append exactly one JSON line without interleaving concurrent writers."""
    append_text(path, json.dumps(value, ensure_ascii=False) + "\n")


def append_text(
    path: Union[str, Path], text: str, *, prefix_if_empty: str = "",
) -> bool:
    """Append one indivisible text block; optionally initialize an empty file.

    Returns ``True`` when this call created/initialized the file. This is useful for
    append-only formats whose header must appear exactly once before concurrent batches.
    """
    path = Path(path)
    with _cross_process_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8") as handle:
            if created and prefix_if_empty:
                handle.write(str(prefix_if_empty))
            handle.write(str(text))
            handle.flush()
            os.fsync(handle.fileno())
        if created and os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return created
