# Improving a skill

## What it is

A skill is a directory, `{extension_root}/skill/{name}/`, holding a required `SKILL.md`
and optional `scripts/`, `references/`, `resources/` and `examples/`.

**The contract**: the frontmatter — `name`, `description`, `version`, `type` (`worker` and/or
`orchestrator`) — and above all the `description`, which decides whether the skill is ever
triggered at all.

## Improving a skill

This is the heart of the loop. Given evaluation results (and any concrete complaints about specific test cases), make the skill better.

### How to think about improvements

1. **Generalize from the signal.** A skill is meant to be used across many prompts, not just the handful you're iterating on. Don't put in fiddly overfit changes or oppressively constrictive MUSTs to patch one example. If some issue is stubborn, try a different framing or metaphor — it's cheap to try and you may land on something much better.
2. **Keep it lean.** Remove things that aren't pulling their weight. Read the *transcripts*, not just the final outputs — if the skill is making the agent waste steps on something unproductive, cut the part causing it and see what happens.
3. **Explain the why.** Try hard to explain the reasoning behind everything you ask the agent to do. Even when the signal is terse, understand what the user actually needs and transmit that understanding into the instructions. All-caps ALWAYS/NEVER and rigid structures are a yellow flag — reframe and explain instead.
4. **Look for repeated work across test cases.** If every test run independently wrote a similar helper script or took the same multi-step approach, that's a strong signal the skill should bundle that script. Write it once, put it in the new skill's `scripts/`, and have the skill use it — saving every future invocation from reinventing the wheel.

Take your time here — thinking time is not the blocker. Draft a revision, reread it fresh, improve.

### The iteration loop

1. Apply the improvements to the skill files.
2. Re-run all test cases into a new `iteration-<N+1>/` directory, including the baseline. (For a *new* skill the baseline is always no-skill; for an *existing* skill, the baseline can be the original version — snapshot it before editing.)
3. Re-grade and re-aggregate; compare against the previous iteration.
4. Repeat until the outputs are good, the benchmark stops improving, or you've stopped making meaningful progress.

When re-registering an edited skill, put the edited SKILL.md path in your `done_tool` reasoning so the hook reloads it.

---
