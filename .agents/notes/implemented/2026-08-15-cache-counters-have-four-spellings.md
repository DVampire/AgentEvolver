---
status: implemented
date: 2026-08-15
owner: model
affects:
  - agentevolver/model/types.py
  - agentevolver/model/google/chat.py
  - tests/test_cache.py
commits:
  - 16693c1
  - 2992d7f
---
# Agent Note: `TokenUsage.from_raw` reads every spelling of the cache counters, because an unknown one reads as zero

## Problem

Every surface names the cache counters differently, and `from_raw` knew two of them. The
failure mode is what makes this worth a note: a name the function does not know produces `0`,
and `0` is indistinguishable downstream from a genuine "nothing was cached".

So a whole provider gets written off as uncacheable and **no number ever changes** to say so.
`gpt-5.6-sol` is routed to the Responses API, which spells the read count
`input_tokens_details.cached_tokens` and the write count differently again; it reported every
prompt as a full re-read. Gemini caches implicitly — there is no breakpoint to set, so
`cached_content_token_count` is the *only* evidence a hit occurred — and that field was
dropped, which meant a cross-provider comparison was between one provider that counts cache
hits and one that structurally cannot.

## Decision

`TokenUsage.from_raw` reads all four spellings, in a fixed fallback order:

- Anthropic: top-level `cache_read_input_tokens` / `cache_creation_input_tokens`.
- Chat completions: nested `prompt_tokens_details.cached_tokens` / `cache_write_tokens`.
- Responses API: nested `input_tokens_details.cached_tokens` / `cache_write_tokens`.
- Gemini: `cached_content_token_count`, mapped to `cache_read_input_tokens` at the provider
  in `google/chat.py` rather than in `from_raw`, because it arrives on a protobuf object and
  not in a dict.

Input and output totals get the same treatment (`prompt_tokens` / `input_tokens` /
`prompt_token_count`, and `completion_tokens` / `output_tokens` / `candidates_token_count`).

`tests/test_cache.py` discovers its subjects rather than listing them, and
`tests/test_consistency_checks_can_fail.py` mutates two of these spellings out of the source
and requires that test to go red — because a check on a fallback chain is exactly the kind
that silently stops checking.

## What this rules out

**A per-provider usage parser.** Tempting and it would type-check, but the failure being
guarded against is a *missing* case, and N parsers is N places to forget one. A single
function with every spelling in it is one place to look.

**Raising on an unrecognised usage dict.** It would surface the defect loudly, but usage
records carry fields this framework does not care about and providers add more; a strict
parser turns a cosmetic upstream addition into a failed run.

**Mapping Gemini's field inside `from_raw`.** It is not in the dict — the provider hands over
a protobuf object, and reaching into it from the shared normalizer would put a provider's
wire type in the common path.

## What would make this wrong

A fifth spelling. This is the entire risk, and it fails silently by construction — a new
surface or a renamed field reports as zero, and zero looks like a correct answer. The only
defence that works is measurement: a provider whose `cache_read` column is flat zero across a
run where the prefix demonstrably did not change is reporting a spelling problem, not a cache
problem.

The fallback chain also assumes the spellings never collide — that no provider sends both
`cache_read_input_tokens` and `prompt_tokens_details.cached_tokens` with different meanings.
That holds today because each name belongs to one API shape. A relay that merged two upstream
formats into one dict would break the assumption, and the first `or` in the chain would win
silently.
