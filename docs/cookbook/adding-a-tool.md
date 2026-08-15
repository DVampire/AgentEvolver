# Adding a tool

A tool is one thing an agent can do. This is the whole procedure, ending in a step that
proves it worked.

For *why* things are the way they are, follow the links — this page is the sequence.

## 1. Write the class

One file under `agentevolver/tool/default/`, named after the tool.

```python
from typing import Any, Dict

from pydantic import Field

from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType


class WordCountTool(Tool):
    """Count the words in a file."""

    name: str = Field(default="word_count")
    description: str = Field(default="Count the words in a UTF-8 text file.")

    #: False means this tool only reports on the world. Three-valued: `True` changes
    #: state, `None` means "depends on the arguments" — what a shell tool declares.
    #: Anything but an explicit `False` makes the agent flush its log before dispatch,
    #: so that a run killed mid-call still records what it was about to do.
    mutates: bool = Field(default=False)

    async def __call__(self, path: str, ctx: Any = None, **kwargs: Any) -> Response:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                count = len(handle.read().split())
        except OSError as error:
            return Response(type=ResponseType.TOOL, success=False, message=str(error))
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=f"{count} words in {path}",
            data={"words": count},
        )
```

Three things the registry reads and one that decides behaviour:

- **`name` must be a class field**, not something passed to `__init__`. Registration reads
  it off the class, so a name supplied any other way registers as `None`. The same rule
  governs agents and environments.
- **`description` is what the model sees.** It is the entire basis on which the model
  decides to call this rather than something else.
- **The return value must be a `Response`.** A tool that returns a bare string reaches the
  agent as `None` and the run spins without an observation.
- **`mutates`** decides whether the trace is flushed before this runs — see
  [the decision record](../decisions/2026-08-15-the-log-reaches-disk-before-a-mutation.md).

Optionally, `call_timeout_seconds` sets this tool's own budget. Declare it here rather than
leaving it to the caller: the tool is what knows whether ten seconds means "stuck" or
"still compiling".

## 2. Export it

`agentevolver/tool/default/__init__.py`:

```python
from .word_count import WordCountTool
```

Discovery is by import. A tool that is written and not exported is invisible, and nothing
reports it — which is the same class of defect the
[coverage gate](../decisions/2026-08-15-coverage-gate-is-a-dark-file-register.md) exists
to catch.

## 3. Write the test

`tests/test_word_count.py`, following [the convention](../../tests/README.md): a module
docstring naming the failure it prevents, sentence-style test names, and a docstring
wherever the name cannot carry the reason.

Cover the failure path. A tool that only works on the happy path fails in front of a model,
which has no way to tell "this file does not exist" from "this tool is broken" unless the
message says so.

## 4. Verify

```sh
pytest tests/test_word_count.py -q     # your test
pytest --cov -q                        # the gated lane
```

The second matters more than it looks. It is the run that would catch a tool exported
nowhere — a file the whole suite never executes a line of — and it is the run that fails
if the tool catalog has drifted.

Then check the tool actually reaches a model:

```sh
python -c "
import asyncio
from agentevolver.tool import tool_manager
print('word_count' in asyncio.run(tool_manager.list()))
"
```

`True` means registered. The catalog in [docs/tool-catalog.md](../tool-catalog.md)
regenerates from the registry, and `tests/test_tool_catalog.py` fails when it has drifted —
so a new tool shows up there without anyone editing it.

## What not to do

**Do not add the tool to a list somewhere.** Registration is by import and class field.
Anything that also has to be listed by hand becomes a second place to forget.

**Do not describe the tool for a human.** The description is a model-facing contract.
"Count the words in a UTF-8 text file" tells a model when to reach for it; "Word counting
utility" does not.

**Do not make `mutates` a guess.** If a tool's effect depends on its arguments, `None` is
the correct answer and it is not the cautious-looking one — it makes the agent checkpoint
before every call, which is the point.
