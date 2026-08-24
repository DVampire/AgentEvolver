"""`atomic_json_update` is the one place a read-modify-write of a shared JSON file is
serialised across processes.

The framework kept several process-global registries — `ports.json`, the sandbox crash
ledger — and read-modify-wrote each with no cross-process lock and a fixed temp
filename, on a standing assumption of one instance per tree root. Running ProgramBench
instances concurrently breaks that assumption: without serialisation the writers lose
each other's updates, and sharing one temp name lets an `os.replace` publish a
half-written file. These tests pin the guarantee the helper is supposed to give.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agentevolver.utils.file_utils import atomic_json_update


def test_a_missing_file_starts_from_the_default(tmp_path):
    path = tmp_path / "reg.json"
    result = atomic_json_update(path, lambda cur: (cur or []) + ["a"], default=[])
    assert result == ["a"]
    assert json.loads(path.read_text()) == ["a"]


def test_it_returns_what_it_stored(tmp_path):
    path = tmp_path / "reg.json"
    atomic_json_update(path, lambda _: {"x": 1}, default={})
    out = atomic_json_update(path, lambda cur: {**cur, "y": 2}, default={})
    assert out == {"x": 1, "y": 2}
    assert json.loads(path.read_text()) == {"x": 1, "y": 2}


def test_a_corrupt_file_is_treated_as_the_default(tmp_path):
    path = tmp_path / "reg.json"
    path.write_text("{ this is not json")
    result = atomic_json_update(path, lambda cur: (cur or []) + ["recovered"], default=[])
    assert result == ["recovered"]


def test_concurrent_processes_do_not_lose_each_others_writes(tmp_path):
    """20 processes each append one id. All 20 must survive.

    This is the property the unlocked read-modify-write did not have: run the same shape
    without the lock and it loses most of them, because every process reads the same
    starting list and writes its own single-element result over the others.
    """
    path = tmp_path / "ledger.json"
    worker = tmp_path / "worker.py"
    # The mutate sleeps between reading the value and returning the new one, holding the
    # read-modify-write window open. Without it the 20 processes each pay the framework
    # import cost and finish so far apart that their windows rarely overlap — the test
    # would then pass with no lock at all, guarding nothing. With it, making the
    # cross-process lock a no-op loses writes on every run.
    #
    # (The pid-scoped temp name is a separate, secondary guard. While the lock works
    # nothing else is in the critical section, so even a shared temp name is safe; the
    # pid only matters as defence-in-depth when the lock is absent, on non-POSIX. So this
    # test pins the lock, not the temp name.)
    worker.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {os.getcwd()!r})\n"
        "from agentevolver.utils.file_utils import atomic_json_update\n"
        "def mutate(cur):\n"
        "    cur = list(cur or [])\n"
        "    time.sleep(0.05)\n"
        "    return sorted(set(cur + [sys.argv[2]]))\n"
        "atomic_json_update(sys.argv[1], mutate, default=[])\n"
    )
    procs = [subprocess.Popen([sys.executable, str(worker), str(path), f"id{i:02d}"])
             for i in range(20)]
    for p in procs:
        assert p.wait() == 0

    stored = json.loads(path.read_text())
    assert len(stored) == 20, f"lost {20 - len(stored)} concurrent writes: {stored}"


def test_no_stray_temp_or_lock_files_are_left_as_the_real_file(tmp_path):
    """The temp is pid-scoped and replaced; the lock is a sidecar. Neither is the file."""
    path = tmp_path / "reg.json"
    atomic_json_update(path, lambda _: {"ok": True}, default={})
    # The real file is valid JSON, not a temp fragment.
    assert json.loads(path.read_text()) == {"ok": True}
    # Any leftover .tmp is not masquerading as the real file.
    assert not list(tmp_path.glob("*.tmp"))
