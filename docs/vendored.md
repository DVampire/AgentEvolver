# Vendored reference checkouts

`others/` holds third-party repositories read as reference while building this one. None
of it is shipped, imported, or executed by AgentEvolver; `others/README.md` states the
rule that matters when the two disagree — the code here is right.

The whole directory is gitignored, so a fresh clone has none of it. That is the reason
this file exists in the tracked tree: without it, what those checkouts are, where they
came from, and what their licences permit is recorded only in a nested `.git` that only
one machine has.

## What is checked out

| Directory | Upstream | Commit | Licence |
|---|---|---|---|
| `archify/` | `github.com/tt-a1i/archify` | `7b49d0b715fd4ba48116bcdecd1ba3789a279613` (v2.12.0-42) | MIT — © 2026 tt-a1i |
| `deepseek-harness/` | `github.com/deepseek-ai/deepseek-harness` | `47f943859bef60e4160492346772ded9b24f765a` | MIT — © 2026 DeepSeek |
| `diagram-design/` | `github.com/cathrynlavery/diagram-design` | `4da4dfb80b1f3d2f11678726b0db58c33c1d7e9d` | MIT — © 2025 Cathryn Lavery |
| `langflow/` | `github.com/langflow-ai/langflow` | `ce17f022bed0eac943a723b9b6c74a023e819b60` (v1.11.0-9) | MIT — © 2024 Langflow |
| `research-writing-skill/` | `github.com/DVampire/research-writing-skill` | `6f7959554b4614d879d79cb4ece9ed04a7c8a88c` | MIT — © 2026 旬常 |

Every checkout is unmodified: no local commits, no working-tree changes. Reference
material that has been edited is no longer reference material, because a reader comparing
it against this repository would be comparing against something neither project published.

## Getting one

```sh
git clone <upstream> others/<directory>
git -C others/<directory> checkout <commit>
```

## Moving one forward

Checking out a different commit makes the row above false, and `tests/test_vendored.py`
fails until the row is updated in the same change. That gate is the point of pinning a
commit at all: a reference whose version is unknown cannot settle the question it was kept
to settle, which is what the upstream actually did rather than what a reader remembers.

Notes written *about* a checkout — such as
[`others/DEEPSEEK-HARNESS-PACKAGES.md`](../others/DEEPSEEK-HARNESS-PACKAGES.md) — name the
commit they were written against, so moving one forward does not silently invalidate the
reading behind it.
