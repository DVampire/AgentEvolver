"""The sandbox ledger is what a crashed run leaves behind for the next one to clean up.

Peer containers (chrome-vnc, playwright, code-interpreter) outlive the process that
started them: if the gateway is killed, nothing destroys them. That was observed as
leaked chrome-vnc sandboxes making every subsequent browser-environment start fail with
"Network connectivity error", which silently emptied the environments capability list —
a failure that reads as "the browser environment is broken" rather than "a previous run
never cleaned up". The ledger is written ahead of container creation and cleared on clean
destroy, so whatever survives in it at boot belongs to a dead run. These tests pin the
three properties that make that safe: the ledger says what is live, reaping actually
removes it, and a reap that cannot run today leaves the work for the next boot rather
than forgetting it.
"""

from __future__ import annotations

import asyncio

from agentevolver.sandbox.ledger import ledger


def _use_home(monkeypatch, tmp_path) -> None:
    """Point AGENTEVOLVER_HOME at a throwaway tree so the real ledger file is untouched."""
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))


def test_the_ledger_holds_exactly_the_sandboxes_not_yet_destroyed(monkeypatch, tmp_path) -> None:
    """Record and forget are both called from failure paths, so both must be total.

    A sandbox can be recorded twice (a retried create) and forgotten twice (a destroy
    that races a shutdown). If either duplicated an entry or raised, the caller — which
    is often already handling an error — would either reap a container that a live run
    still owns, or crash on the way out and leak the one it was trying to release.
    """
    _use_home(monkeypatch, tmp_path)
    assert ledger.stale_ids() == []
    ledger.record("aaa")
    ledger.record("bbb")
    ledger.record("aaa")  # idempotent
    assert ledger.stale_ids() == ["aaa", "bbb"]
    ledger.forget("aaa")
    assert ledger.stale_ids() == ["bbb"]
    ledger.forget("missing")  # no-op
    assert ledger.stale_ids() == ["bbb"]


def test_reaping_names_containers_the_way_the_daemon_did_and_then_empties_the_ledger(
    monkeypatch, tmp_path,
) -> None:
    """A ledger entry is a sandbox id; the container it maps to is `sandbox-<id>`.

    Getting that mapping wrong is invisible: `docker rm -f` on a name that does not exist
    succeeds, so a reap that forgot the prefix would report every container removed while
    every container stayed up. Hence the assertion on what `_remove_container` was
    actually handed, not just on the return value.

    Clearing afterwards matters for the same reason in the other direction — ids left in
    the ledger would be re-reaped on the next boot, and by then the name could belong to
    a container the new run created.
    """
    _use_home(monkeypatch, tmp_path)
    ledger.record("dead-1")
    ledger.record("sandbox-dead-2")  # already-prefixed ids are used as-is
    removed: list[str] = []

    async def fake_remove(name: str) -> bool:
        removed.append(name)
        return True

    monkeypatch.setattr(ledger, "_remove_container", fake_remove)
    reaped = asyncio.run(ledger.reap_stale())
    assert sorted(reaped) == ["sandbox-dead-1", "sandbox-dead-2"]
    assert sorted(removed) == ["sandbox-dead-1", "sandbox-dead-2"]
    # Ledger is cleared afterwards; a second reap is a no-op.
    assert ledger.stale_ids() == []
    assert asyncio.run(ledger.reap_stale()) == []


def test_a_reap_that_cannot_reach_docker_keeps_the_debt_for_the_next_boot(
    monkeypatch, tmp_path,
) -> None:
    """No socket and no CLI means "not now", not "nothing to do".

    Reaping is best-effort and swallows its exceptions, which makes the tempting
    implementation — clear the ledger once the loop finishes — look correct: it never
    raises and the ledger ends up empty. It would also permanently forget every orphan on
    a host where Docker was momentarily unreachable, and those containers are then
    unreapable by anything, since the ledger was their only record.
    """
    _use_home(monkeypatch, tmp_path)
    ledger.record("dead-3")

    async def broken_remove(_name: str) -> bool:
        raise FileNotFoundError("no docker socket and no CLI")

    monkeypatch.setattr(ledger, "_remove_container", broken_remove)
    assert asyncio.run(ledger.reap_stale()) == []
    # Entries stay recorded so a later boot (with docker available) can reap.
    assert ledger.stale_ids() == ["dead-3"]
