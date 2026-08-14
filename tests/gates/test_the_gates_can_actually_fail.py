"""Each gate is re-checked against the bug it exists for.

A gate that cannot fail is worse than no gate: it reports the invariant as held. The
failure is silent and permanent, and an assertion loosened during an unrelated refactor
is exactly how it happens — nothing goes red, so nobody looks.

So every gate is verified the only way that means anything: reintroduce the defect it was
written for and require it to go red. The mutations below are the real ones, taken from
the session that produced these gates — not hypotheticals. The first draft of the
serializer gate was itself caught this way, asserting an invariant Gemini does not hold.

Marked slow: each case is a pytest subprocess. Run with `-m "not slow"` to skip.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SERIALIZERS = "tests/gates/test_serializers_cover_every_message_type.py"
USAGE = "tests/gates/test_usage_spellings_are_all_normalized.py"
STRUCTURE = "tests/gates/test_prompt_structure_agrees_across_files.py"

# (name, file, find, replace, gate that must go red)
MUTATIONS = [
    ("a serializer forgets ToolMessage",
     "agentevolver/model/llm_hub/serializer.py",
     "elif isinstance(message, ToolMessage):",
     "elif isinstance(message, type(None)):", SERIALIZERS),

    ("the Responses cache spelling is dropped",
     "agentevolver/model/types.py",
     'input_details.get("cached_tokens") or 0', "0", USAGE),

    ("Gemini's protobuf field stops being mapped",
     "agentevolver/model/google/chat.py",
     '"cache_read_input_tokens": getattr(u, "cached_content_token_count", 0) or 0,',
     "", USAGE),

    ("the renderer stops knowing the container",
     "agentevolver/visual/js/prompt.js",
     "'agent-context', 'capability-context'", "'agent-context'", STRUCTURE),

    ("the stylesheet reverts to a direct-child selector",
     "agentevolver/visual/css/prompt.css",
     "div.user capability-context > tool-context", "div.user > tool-context", STRUCTURE),

    ("the splitter loses the container",
     "agentevolver/agent/types.py",
     'for block in ("capability-context", "tool-context"',
     'for block in ("tool-context"', STRUCTURE),

    ("a template puts state before capabilities",
     "agentevolver/prompt/default/code_agent.html",
     '<div class="user">\n<capability-context>',
     '<div class="user">\n<agent-context></agent-context>\n<capability-context>', STRUCTURE),
]


@pytest.mark.slow
@pytest.mark.parametrize("name,path,find,replace,gate", MUTATIONS,
                         ids=[m[0] for m in MUTATIONS])
def test_the_gate_goes_red_when_the_defect_returns(name, path, find, replace, gate):
    target = ROOT / path
    original = target.read_text(encoding="utf-8")
    mutated = original.replace(find, replace, 1)
    assert mutated != original, (
        f"the mutation for {name!r} no longer applies — its anchor moved, so this case "
        f"has been testing nothing. Re-point it at the current code.")

    target.write_text(mutated, encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", gate, "-q", "--no-header", "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=ROOT, timeout=300)
    finally:
        target.write_text(original, encoding="utf-8")   # always, including on timeout

    assert result.returncode != 0, (
        f"{gate} stayed green with {name!r} reintroduced — the gate does not guard what "
        f"it claims to.\n{result.stdout[-1500:]}")
