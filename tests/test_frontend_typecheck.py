"""The TypeScript half of this repository compiles.

Nothing checked it. The Python gates that touch the frontend compare *text* — the wire
contract test matches field names, the port-type test extracts one function body — which
is the right shape for those questions and blind to whether the file compiles at all.

That blindness had already cost something by the time this file was written. Rewiring
`controllers/gateway.ts` to re-export the generated contract left `PROTOCOL_VERSION`
re-exported but not imported, so the one line that *uses* it referred to a name no longer
in scope. A re-export forwards a name; it does not bind it locally. Every Python gate
passed, because none of them compile anything.

Known errors are registered below rather than tolerated by a count. A count admits any
three errors; the register admits these three, so a new one in a file that already had one
still fails.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
TSC = FRONTEND / "node_modules" / ".bin" / "tsc"

#: `file(line,col): error TSxxxx: message`
_ERROR = re.compile(r"^(?P<file>[^(]+)\((?P<line>\d+),\d+\): error (?P<code>TS\d+): (?P<msg>.+)$")

#: Errors this repository has and has decided to carry, each with the reason. Keyed by
#: file and code — not by line, which moves whenever anything above it does.
#:
#: Entries may only leave. A registered error that stops occurring fails too, so a fix
#: cannot quietly leave a stale excuse behind that would cover the next one.
KNOWN: dict[tuple[str, str], str] = {
    (
        "src/vnc/VncView.tsx",
        "TS2339",
    ): "@types/novnc-core omits resizeSession/qualityLevel/compressionLevel, which the "
    "runtime RFB does have; the alternative is casting away the type entirely",
}


def _node() -> Path | None:
    """The interpreter tsc needs. It is not always on PATH here."""
    import shutil

    found = shutil.which("node")
    if found:
        return Path(found)
    candidate = Path("/home/wtzhang/miniconda3/envs/agentos/bin/node")
    return candidate if candidate.exists() else None


_NODE = _node()
_RUNNABLE = _NODE is not None and TSC.exists()


def _errors() -> list[dict]:
    result = subprocess.run(
        [str(_NODE), str(TSC), "--noEmit", "-p", "tsconfig.json"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
    )
    out = []
    for line in (result.stdout + result.stderr).splitlines():
        match = _ERROR.match(line.strip())
        if match:
            out.append(match.groupdict())
    return out


@pytest.mark.skipif(not _RUNNABLE, reason="node or frontend/node_modules is not present")
def test_the_frontend_has_no_unregistered_type_errors():
    """The gate. A new type error fails here rather than in someone's browser."""
    unregistered = [
        f"{e['file']}:{e['line']} {e['code']}: {e['msg']}"
        for e in _errors()
        if (e["file"], e["code"]) not in KNOWN
    ]
    assert not unregistered, (
        "these are new; fix them, or register the pair in tests/test_frontend_typecheck.py "
        "with the reason it is carried:\n" + "\n".join(unregistered)
    )


@pytest.mark.skipif(not _RUNNABLE, reason="node or frontend/node_modules is not present")
def test_every_registered_error_still_happens():
    """The register may only shrink.

    A fixed error whose entry stays behind is an excuse with nothing to excuse — and the
    next error in that file and code would inherit it silently.
    """
    occurring = {(e["file"], e["code"]) for e in _errors()}
    stale = [f"{f} {c} ({why})" for (f, c), why in KNOWN.items() if (f, c) not in occurring]
    assert not stale, f"these no longer occur; drop them from KNOWN: {stale}"


@pytest.mark.skipif(not _RUNNABLE, reason="node or frontend/node_modules is not present")
def test_the_generated_contract_is_reachable_as_a_value_and_not_only_a_type():
    """The specific shape of the failure this file exists for.

    `PROTOCOL_VERSION` is a value: it is read at run time to stamp every outgoing
    command. Re-exported without being imported, the module still type-checks as far as
    its *consumers* are concerned — the export exists — while the line inside that uses
    it does not resolve. Asserting the import directly makes the requirement legible
    where it is easy to get wrong again.
    """
    source = (FRONTEND / "src" / "controllers" / "gateway.ts").read_text(encoding="utf-8")
    uses = "PROTOCOL_VERSION" in source.split("} from '../protocol/gateway';", 1)[-1]
    if uses:
        assert re.search(
            r"^import \{[^}]*PROTOCOL_VERSION[^}]*\} from '\.\./protocol/gateway';", source, re.M
        ), (
            "gateway.ts uses PROTOCOL_VERSION as a value but only re-exports it; a "
            "re-export forwards a name without binding it locally"
        )
