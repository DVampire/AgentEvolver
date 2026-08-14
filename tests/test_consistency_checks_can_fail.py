"""Each consistency check is re-checked against the bug it exists for.

A check that cannot fail is worse than no gate: it reports the invariant as held. The
failure is silent and permanent, and an assertion loosened during an unrelated refactor
is exactly how it happens — nothing goes red, so nobody looks.

So every gate is verified the only way that means anything: reintroduce the defect it was
written for and require it to go red. The mutations below are the real ones, taken from
the session that produced these gates — not hypotheticals. The first draft of the
serializer gate was itself caught this way, asserting an invariant Gemini does not hold.

Marked slow: each case is a pytest subprocess. Run with `-m "not slow"` to skip.

**Only one test session may run against this checkout at a time.** Each case edits real
source for the moment it takes the subprocess to finish. Within one session that is safe —
pytest is serial, so nothing else reads the file in that window — but a second session
running concurrently will read the mutated file and fail somewhere unrelated. It has
happened twice: parallel agents each reported an "intermittent, pre-existing" failure in
a file neither had touched, and both were reading this file's mutations. If you need
concurrent runs, deselect this module with `-m "not slow"`.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from tests.conftest import BACKUP_SUFFIX, SKIP_REPAIR_ENV

SERIALIZERS = "tests/test_serializer.py"
USAGE = "tests/test_cache.py"
STRUCTURE = "tests/test_prompt_layout.py"
CATALOG = "tests/test_tool_catalog.py"
LINKS = "tests/test_doc_links.py"
PAIRING = "tests/test_translation_pairing.py"

# (name, file, find, replace, check that must go red)
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

    # A tool's description is one of the facts the committed catalog copies out of the
    # code. Changing it and leaving the document alone is what "the generated file went
    # stale" looks like from the inside.
    ("a tool's description changes and the catalog is not regenerated",
     "agentevolver/tool/default/read_file.py",
     '_DESCRIPTION = "Read the contents of a file."',
     '_DESCRIPTION = "Read a file."', CATALOG),

    # The everyday rename: a document moves and the pages pointing at it do not.
    ("a document a README links to is renamed",
     "README.md", "](docs/canvas.md)", "](docs/canvas-moved.md)", LINKS),

    # The everyday translation drift: a section is added on one side only. Nothing about
    # the file is malformed, and no other check in the suite has an opinion about it.
    ("a section is added to one language and not the other",
     "README.md", "## Project status", "## Project status\n\n## Extra section", PAIRING),
]


@pytest.mark.slow
@pytest.mark.parametrize("name,path,find,replace,check", MUTATIONS,
                         ids=[m[0] for m in MUTATIONS])
def test_the_check_goes_red_when_the_defect_returns(name, path, find, replace, check):
    target = ROOT / path
    backup = Path(str(target) + BACKUP_SUFFIX)
    original = target.read_text(encoding="utf-8")
    mutated = original.replace(find, replace, 1)
    assert mutated != original, (
        f"the mutation for {name!r} no longer applies — its anchor moved, so this case "
        f"has been testing nothing. Re-point it at the current code.")

    # Park the original first. If this process dies between here and the restore, the
    # autouse fixture puts it back on the next run.
    backup.write_text(original, encoding="utf-8")
    target.write_text(mutated, encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", check, "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=ROOT, timeout=300,
            # The child must not repair the defect this test just introduced.
            env={**os.environ, SKIP_REPAIR_ENV: "1"})
    finally:
        target.write_text(original, encoding="utf-8")
        backup.unlink(missing_ok=True)

    assert result.returncode != 0, (
        f"{check} stayed green with {name!r} reintroduced — the check does not verify "
        f"what it claims to.\n{result.stdout[-1500:]}")
