---
status: implemented
date: 2026-08-15
owner: plan
affects:
  - agentevolver/plan/server.py
  - agentevolver/hook/default/plan_mode.py
  - agentevolver/tool/default/media_search.py
  - agentevolver/tool/default/journal.py
commits:
  - a4e7705
  - 2bc75b9
---
# Agent Note: The plan gate reads declarations, and silence is a refusal

## Problem

Plan mode has to decide, for every action, whether it changes anything. The obvious
implementation is a list: these tool names are safe, everything else waits for approval.

A list is wrong here for the same reason it is wrong in `validate_command` — a name says
nothing about an effect. `python` is not read-only; `python -c 'open("x","w")'` is the same
name. And a list has to be maintained against a tool set this framework generates at
runtime, so it is guaranteed to be missing whatever was added most recently.

## Decision

`action_is_allowed(kind, name, declaration)` admits an action when the capability declared
`mutates is False` **or** `permission_mode == "read_only"`. Both are the capability's own
claim, written next to the code that knows.

Absence is a refusal. A tool with no `mutates` field has not said it is safe, it has said
nothing — and `bash_tool` is exactly that tool. Reading silence as permission would hand
the gate to the one capability that can do anything.

Kinds that cannot be judged from a declaration are refused outright: an agent dispatch or a
workflow does whatever the thing it runs does, which is not knowable here.

`ALWAYS_ALLOWED` is `{exit_plan_mode, ask_user_question, done_tool}` — the way out, the way
to ask, and the way to stop. Without those the agent has no legal move and plan mode is a
deadlock rather than a gate.

The notice announcing the mode rides in the volatile section of the prompt, repeated every
step. Not the system message: plan mode toggles mid-session and system text sits ahead of
the cache breakpoint, so switching it on would rewrite the prefix and throw away the
session's cached tokens to deliver one paragraph.

## What this rules out

It rules out the gate being right about a tool that lies. Two tools declared
`mutates = False` while writing — `media_search_tool` downloads into the workspace,
`journal_tool` appends rounds — and both walked straight through. That was a mislabel
before this gate existed and a hole after it, which is the point: **a declaration nothing
reads is documentation, and a declaration something enforces is a control surface.** Both
were corrected in the same change, and `test_registration.py` now scans for a module that
writes while its class claims otherwise.

It also rules out judging by permission mode alone. Ten read tools inherited
`workspace_write` from the base class without ever writing; deciding on that field alone
would have refused all of them.

## What would make this wrong

The write-detection scan is crude — a literal `open(..., "w")` in the tool's own module. It
cannot see a write behind a helper or inside a library, which is why `web_fetcher_tool` and
`web_searcher_tool` keep the wider mode rather than a `mutates` claim nobody can check. If
a tool is found writing through a path that scan cannot reach, the gate has been trusting a
declaration that was never verifiable, and the answer is to make the effect observable
rather than to widen the scan.

If plan mode is ever wanted for something other than "do not change state" — spending
money, sending mail, calling a person — `mutates` is the wrong axis, and the declaration
needs a second field rather than a broader reading of this one.
