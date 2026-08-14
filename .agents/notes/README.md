---
name: agent-notes
description: "Where a decision's reasoning lives once the commit message has scrolled away: what a note must record, which package owns it, and how it is retired when it stops being true."
version: 1.0.0
type: convention
category: documentation
requirements: []
metadata: {}
---
# Agent Notes

A note here records **one decision and the reasoning behind it**. Not what the code does —
the code says that, and says it more accurately. A note says why it is that shape, what
the shape rules out, and what observation should make someone change it.

## The failure this exists for

`_REFREEZE_RATIO = 0.25` sits in `agentevolver/agent/types.py` with a second condition,
`_REFREEZE_MIN_CHARS = 2000`, guarding the same branch. Both numbers were chosen against a
specific measurement and a specific cost model. The reasoning for both was written down
once, in a commit message, and once again in a chat log that no longer exists.

Six months later the reader has three options: leave it alone because it looks deliberate,
change it because it looks arbitrary, or spend an afternoon in `git log -S`. All three are
worse than reading a paragraph. A commit message is addressed to whoever reviews that
diff; a note is addressed to whoever is standing in front of the code with a question.

## Layout

```
.agents/notes/
  README.md
  implemented/YYYY-MM-DD-topic-slug.md
  superseded/YYYY-MM-DD-topic-slug.md
```

Two lifecycle folders, and the date in the filename is when the decision **landed** (the
date of the commit that shipped it), not when the note was written.

- **`implemented/`** — the decision is in the code now. The note is kept true: when a file
  moves or a constant is renamed, the note is corrected in the same change. Facts only —
  correcting a path is required, rewriting the reasoning is not what "keeping it true"
  means.
- **`superseded/`** — the decision was true and no longer is. The note stays because the
  question it answers keeps coming back, and because a deleted note re-invites the
  alternative it rejected.

There is no `proposed/` and no `rejected/` folder. Decisions in this repo land in the same
commit as the code they describe; there is no review stage in which a written proposal
exists and the code does not. An alternative that was considered and declined is recorded
inside the note of the decision that beat it — that is what [What this rules
out](#the-body) is for — rather than getting a file of its own that nothing points at.

## Classification: the package that owns the decision

The one classification a note carries is `owner`: the package under `agentevolver/` where
the decision is enforced. It goes in the frontmatter, not in the path.

This is the question a reader actually arrives with. Nobody opens this directory wondering
which notes are "architecture" and which are "process"; they open it because they are
about to change `agentevolver/model/llm_hub/serializer.py` and want to know what already
constrains it. `owner` answers that, and — unlike a taxonomy of words — it can be checked
against the source tree, so a note naming a package that no longer exists fails a test
instead of quietly misfiling itself.

A decision that spans packages still has exactly one owner: the package where it is
enforced. The rest go in `affects`. Placing the capability catalogs ahead of the agent
state touched twenty-seven templates and three serializers, but the placement is enforced
by the templates, so `prompt` owns it and the serializers are listed.

## The frontmatter

```yaml
---
status: implemented
date: 2026-08-14
owner: agent
affects:
  - agentevolver/agent/types.py
commits:
  - 8523cb5
---
```

| Field | Meaning |
|---|---|
| `status` | `implemented` or `superseded`; must agree with the folder. |
| `date` | The date the decision landed, matching the filename. |
| `owner` | One package directory under `agentevolver/`. |
| `affects` | Repo-relative paths this decision constrains. Every one must exist. |
| `commits` | Short SHAs whose messages carry the raw record. At least one. |

A superseded note adds two more:

| Field | Meaning |
|---|---|
| `superseded_by` | Filename of the note that replaced it, under `implemented/`. |
| `superseded_on` | The date the replacement landed. |

`commits` is here because of how this repo works: the commit messages are long and carry
measurements, and a note is a summary of them, not a replacement. A reader who wants the
numbers behind a claim should be one `git show` away, and a note that cites nothing is
indistinguishable from a note someone invented.

## The body

```markdown
# Agent Note: <title>

## Problem
## Decision
## What this rules out
## What would make this wrong
```

Line 1 is the title, the four sections appear in that order, and nothing else is required.

**`## Problem`** — the situation that forced a choice, written so it stands without the
solution. If it cannot be stated without naming the fix, there was no decision to record.

**`## Decision`** — what is true in the code now, in the present tense. Constants get their
values; thresholds get the measurement they were set against.

**`## What this rules out`** — the alternatives that lost and why, and the changes this
decision forbids. A decision recorded without what it beat gets re-litigated, and a reader
who does not know which door is nailed shut will try it.

**`## What would make this wrong`** — the observation that should send someone back here.
Not a caveat and not a list of risks: a condition that could actually be met, stated
concretely enough to notice. "A model whose cache entries live longer than an hour" is one.
"Requirements may change" is not.

That last section is the point of the whole format. A note whose reasoning was true against
a measurement taken on one task in one week should say so, so that the next person with a
different measurement knows they are allowed to overrule it. Without it, every note reads
as permanent, and the corpus becomes a set of rules nobody remembers agreeing to.

## Retiring a note

When the falsifier in `## What would make this wrong` actually fires, or the decision is
replaced for any other reason:

1. Write the new note in `implemented/`, dated the day the replacement lands. It states
   what changed and why the old reasoning stopped holding — it does not merely say "see the
   old note".
2. Move the old file to `superseded/`, set `status: superseded`, add `superseded_by` and
   `superseded_on`, and append a `## Superseded` section saying what replaced it.
3. Change nothing else in the old file. The reasoning is the record; editing it to agree
   with the new decision destroys the thing that made it worth keeping.

Notes are not deleted. The corpus is small enough that the cost of a stale-but-labelled
file is a line in a directory listing, and the cost of deleting one is that the same
rejected alternative gets proposed again with nothing to point at.

## When to write one

Write a note when a change makes a choice that a reader of the resulting code could not
reconstruct: a constant with a value nothing derives, an ordering that looks arbitrary and
is not, a contract that deliberately keeps an old meaning, a capability deliberately left
off by default.

Do not write one for a change whose reasoning is visible in the diff. A renamed variable, a
fixed typo, a straightforwardly missing branch — these do not become clearer for having a
file. The test in `tests/test_agent_notes.py` checks the format; it deliberately does not
require a note per commit, because a rule that manufactures notes produces notes that
restate the code, and a note that restates the code is worse than none: it costs a read and
returns nothing.

## Notes and postmortems

A note records a decision that was made. A [postmortem](../../docs/postmortem/README.md)
records a failure that escaped — what broke, and why nothing caught it. When a postmortem
leads to a decision, the decision gets a note and the postmortem links to it.
