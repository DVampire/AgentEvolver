"""What sort of thing something is, is spelled `type` here.

The repository used both words for one question. `Response.type`, `Command.type` and
`MemoryEvent.event_type` said `type`; `Job.kind`, `CodeFailure.kind`, `ToolPolicyDecision
.kind` and a sandbox's `kind` said `kind`. Nothing decided between them, so which word a
new field got depended on which file its author had open.

Two exceptions are real and are registered below rather than argued about each time. An
external specification owns its own field names — renaming LSP's `Symbol.kind` produces
something that is not LSP — and a discriminated envelope needs a word that is not already
taken by the thing it carries: `GatewayEvent` answers both "which message shape is this"
and "which event is it", and the capability API answers both "which family" and "what type
does this skill declare". Those keep `kind` for the first question.

Renaming a keyword parameter is the part that bites. Four call sites survived a grep during
one rename in this repository, and `tests/test_port.py` records three more from an earlier
one; each failed at run time, inside a `try`, as something that read like a missing feature.
So this checks the callers, not only the definitions.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]

#: Where `kind` is still the right word, and why. Entries may only leave this list.
#:
#: Prefixes match against repository-relative paths.
ALLOWED = {
    # An external specification owns its own field names.
    "agentevolver/lsp/": "LSP's own wire field (`Symbol.kind`, `ResultKind`)",
    "agentevolver/tool/default/lsp.py": "renders LSP results, which carry `kind`",
    "tests/test_lsp.py": "exercises the LSP shapes",
    # A discriminated envelope, where `type` already answers a different question.
    "agentevolver/gateway/types.py": "envelope discriminator beside the event's own `type`",
    "agentevolver/gateway/typescript.py": "renders that discriminator",
    "frontend/src/protocol/gateway.ts": "generated from the models above",
    "frontend/src/controllers/gateway.ts": "narrows on the discriminator",
    "frontend/src/cli/gateway/": "emits envelopes",
    "tests/test_gateway_contract.py": "asserts the discriminator is rendered",
    # Capability family, beside a capability's own declared `type`.
    "agentevolver/gateway/service.py": "capability family beside `CapabilityDetail.type`",
    "agentevolver/plan/server.py": "capability family",
    "agentevolver/hook/default/plan_mode.py": "capability family",
    "frontend/src/App.tsx": "capability family beside `CapabilityDetail.type`",
    "tests/test_gateway.py": "capability family",
    "tests/test_plan_mode.py": "capability family",
    "tests/test_trace_checkpoint.py": "capability family",
    # Data written to disk before the rename. A field can be renamed; a file already on
    # someone's disk cannot, so these readers accept both spellings and write the new one.
    "agentevolver/canvas/types.py": "loads flows saved with the old spelling",
    "agentevolver/trace/derive.py": "reads folds recorded with the old spelling",
    "agentevolver/trajectory/projector.py": "resumes state files written with the old spelling",
    "tests/test_saved_files_outlive_a_rename.py": "pins exactly that back-compatibility",
    # Third-party skill documents describing another system's feature.
    "agentevolver/skill/": "Claude Code's own subagent vocabulary",
    # This file names the word in order to forbid it.
    "tests/test_kind_is_spelled_type.py": "the register itself",
    "tests/test_port.py": "asserts the old keyword is gone, so it must name it",
}

#: `inspect.Parameter.kind` is the standard library's, wherever it appears.
_STDLIB = re.compile(r"\b(parameter|param|p)\.kind\b")

#: `kind` used as a name rather than as the English word.
_IDENTIFIER = re.compile(r"""(?x)
      \bkind\s*[:=]                 # kind: str   kind = x   kind=x
    | \bkind\s*[,)\]]               # f(kind)   [kind]
    | \.kind\b                      # obj.kind
    | ["']kind["']                  # "kind" as a dict key
    | \bkind\b\s*(===|!==|==)       # kind === 'x'
    | \b\w*Kind\b                   # ResultKind, CapabilityKind
""")

SOURCES = ("agentevolver/**/*.py", "frontend/src/**/*.ts", "frontend/src/**/*.tsx",
           "tests/**/*.py", "scripts/**/*.py")


def _allowed(relative: str) -> bool:
    return any(relative.startswith(prefix) for prefix in ALLOWED)


def _offences() -> List[str]:
    found: List[str] = []
    for pattern in SOURCES:
        for path in ROOT.glob(pattern):
            if "__pycache__" in str(path):
                continue
            relative = str(path.relative_to(ROOT))
            if _allowed(relative):
                continue
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                stripped = line.strip()
                if _STDLIB.search(stripped) or not _IDENTIFIER.search(stripped):
                    continue
                found.append(f"{relative}:{number}: {stripped[:96]}")
    return found


def test_nothing_outside_the_register_names_a_thing_kind():
    """One word for one question, everywhere the register does not except."""
    offences = _offences()
    assert not offences, (
        "these spell `type` as `kind`; rename them, or add the file to ALLOWED with the "
        "reason it is an exception:\n" + "\n".join(offences)
    )


def test_the_register_lists_only_files_that_exist():
    """A stale exception silently widens the rule.

    A directory prefix is kept as-is; a file that was deleted or moved would otherwise go
    on excusing a path nothing writes to any more.
    """
    missing = [
        prefix for prefix in ALLOWED
        if not prefix.endswith("/") and not (ROOT / prefix).exists()
    ]
    assert not missing, f"ALLOWED names files that no longer exist: {missing}"


def test_every_registered_exception_still_uses_the_word():
    """The register may only shrink.

    An exception that stopped needing to be one is the same defect as the coverage gate's
    file that started being covered: the list stays true only if both directions fail.
    """
    unused = []
    for prefix, reason in ALLOWED.items():
        if prefix == "tests/test_kind_is_spelled_type.py":
            continue
        target = ROOT / prefix
        paths = ([p for p in target.rglob("*") if p.is_file()] if target.is_dir()
                 else [target] if target.exists() else [])
        if not any(_IDENTIFIER.search(p.read_text(errors="ignore")) for p in paths
                   if p.suffix in {".py", ".ts", ".tsx", ".md"}):
            unused.append(f"{prefix} ({reason})")
    assert not unused, f"these no longer use `kind`; drop them from ALLOWED: {unused}"


def test_no_caller_passes_the_renamed_keyword():
    """The failure mode a rename actually has.

    Renaming a keyword parameter leaves every `f(kind=...)` call raising `TypeError` at
    run time rather than at import, and those raise inside handlers that report them as
    something else. Four survived a grep during the rename this file was written for.
    """
    renamed = {"register", "acquire", "release", "get"}
    offenders = []
    for path in ROOT.glob("agentevolver/**/*.py"):
        if "__pycache__" in str(path) or _allowed(str(path.relative_to(ROOT))):
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else getattr(node.func, "id", ""))
            if name in renamed and any(k.arg == "kind" for k in node.keywords):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}(kind=...)")
    assert not offenders, f"these pass the old keyword: {offenders}"


def test_the_managers_no_longer_accept_the_old_keyword():
    """Read off the live signatures, so this cannot pass against a stale copy."""
    from agentevolver.job import job_manager
    from agentevolver.sandbox.server import sandbox_manager

    for callable_ in (job_manager.register, sandbox_manager.acquire, sandbox_manager.release):
        parameters = inspect.signature(callable_).parameters
        assert "type" in parameters, f"{callable_.__qualname__} lost its `type` parameter"
        assert "kind" not in parameters, f"{callable_.__qualname__} still takes `kind`"
