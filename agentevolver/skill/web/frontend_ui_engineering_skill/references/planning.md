# Planning and reviewing a web product

Use the generic plan module's exact paths from plan-context. Its two core files are
`index.md` and `plan.md`; do not create a competing plan in the product workspace.
Respect disabled planning or an active user-requested planning review gate.

## Keep a small current index and a detailed plan

The whole index shares the runtime's 2,000-character budget. Include the objective,
current iteration, progress, blockers, next action and links with short status/summary.
Keep the authoritative task path accessible. Read linked records on demand; do not copy
the brief, full design or review history into the index. The runtime does not maintain it
for you and does not inject full plan.md into live context.

Expand the product outline in plan.md: audience and visitor goals, chosen interaction
design, visual direction, component/service responsibilities, data ownership/persistence,
implementation dependencies and concrete acceptance journeys. Distinguish planned,
implemented and browser-verified work. A release count is not a quality verdict.

Choose any additional documents only when they help navigation or preserve useful detail.
For example, a growing forum might use `design.md`, `reviews/iteration-02.md` and
`evaluations/<capability>.md` beside the core files. These are optional examples, not a
required tree or schema. Reuse an inherited structure instead of generating empty folders.
Product source, executable tests and assets remain in the workspace. Link screenshots,
tool-call IDs and logs at their original paths instead of duplicating raw evidence.

## Record two distinct improvement loops

For the website, preserve the tested revision/pinned URL, viewport, content and initial
state; the UI actions and visible outcome; a specific visual critique and useful creative
idea; the selected implementation; and equivalent before/after evidence. Cover an actual
reading/participation state as well as the opening view. Record aesthetic judgment,
functionality, accessibility and unresolved defects separately. Self-observations are not
real user feedback, and fictional demonstration participants are not test reviewers.

Maintain a compact coverage table for the journeys promised by this product, including
material import/inspection, personal choices, return visits and failure recovery where
applicable. For each, link the last observed version and remaining uncertainty. Experience
the whole workflow before narrowing the next iteration to an easy local fix. Compare
distinct creative approaches to an observed unmet goal, explain the selected trial and
test it with different content. Keep the observation → design decision → method probe →
product result connected through links; detailed evidence belongs in the linked records.

For system evolution, connect an observed product need to an essential operation and its
quality/reliability/cost requirement. Preserve baseline evidence before changing the method.
Only an observed reusable limitation justifies a capability candidate. Keep the exact
registered version, baseline comparison, independent reuse/regression, decision and later
consumer result with the adoption-tool receipts. A new feature or prettier CSS is product
work; a written evaluation or loaded skill alone does not establish capability improvement.
Do not select a predetermined helper or invent a limitation to satisfy an evolution quota.
Maintenance improvements may be useful, but cannot close another unmet experience goal.
Revisit that goal after adoption and verify the resulting UI. Supported component forms,
registration and evaluation policy are defined by the shared evolution rules.

Update the affected record and its index entry after a coherent implementation, browser
review, release or capability decision. Avoid per-click narration and repeated index
rewrites without new progress. Before handing off, reconcile both outcomes with the task
and carry unfinished requirements forward honestly.

## Shared-product acceptance

When a brief requires independent visitors, test separate browser storage contexts;
two tabs in one context do not prove isolation. This needs no persona or additional agent.
Use the existing browser's command capability for this bounded acceptance check. In local
Playwright, `context.browser.new_context()` creates a second context in the same browser;
create a page, set bounded locator/navigation timeouts and use ordinary visible UI actions.
Keep the runtime-owned page intact and close the additional context after the check. Do
not start a second browser or switch to API writes to manufacture a UI success.

Preserve A's contribution, B's independently observed reply and A's return journey as
actual outcomes. Separate browser storage from server durability. Check the outcome when
an action starts from a direct contribution link: the resulting
URL, scroll and keyboard focus should point to the saved contribution after rendering and reload.
Keep persistent data outside replaceable release source, isolate preview data and verify controlled restarts
and revisions with test content. Never silently reset published discussions to make a
new release easier. Record any unavailable isolation, storage or browser step as a blocker.
