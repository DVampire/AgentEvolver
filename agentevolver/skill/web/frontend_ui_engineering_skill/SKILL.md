---
name: frontend_ui_engineering_skill
description: "Design and implement distinctive, usable web interfaces: content-led composition, deliberate typography and imagery, responsive interactions and rendered visual refinement. Use for building or improving user-facing websites and applications."
version: 1.4.0
type: worker
license: N/A
category: web
requirements: [cpu]
enable_evolving: false
metadata: {}
---

# Frontend UI Engineering

Make the product recognizable through its content, composition and interactions. Aim for
clear hierarchy, confident typography, well-chosen imagery and precise working states.
Follow explicit brand requirements and the existing project's conventions. Use the mounted
tools and current stack; this skill does not prescribe a framework or component library.

## Choose the visual direction before styling

For a new visual identity or substantial redesign, read
[visual direction and practical starting points](references/visual-direction.md) before
writing the main layout and CSS. For a small change, inspect the existing rendered surface
and preserve its language; a bug fix is not permission to redesign the product.

Ground the direction in the product's subject and primary activity. Choose the composition,
type roles, light/dark surfaces, accent role and main visual together. For ambiguous briefs,
briefly compare genuinely different compositions and commit to one; do not spend the task
producing a style catalog. Keep the decision and useful references in the existing work record.

Do not make cream backgrounds, terracotta accents, oversized italic serif headlines and
asterisk logos the default identity of unrelated products. Dark green console styling,
purple gradient landing pages and rounded-card grids are not universal defaults either.
Use any of these when the brief or established identity supports them. The host framework's
monitoring UI does not determine the visual identity of the products it builds.

When a brief leaves style open, favor crisp neutral surfaces, strong text contrast, readable
sans-serif working UI and a focused accent. This is a fallback starting point, not a fixed
palette: an exhibition can be vivid, an editorial product typographic, and a game atmospheric.
Give each product a meaningful distinguishing feature beyond a font or color swap.

For multi-step product work with planning enabled, first read
[the website planning guide](references/planning.md). Use the session's shared
index.md/plan.md paths; choose additional records to fit the work.

## Design and implementation

- Build a visually credible, runnable primary journey first, then complete the connected workflows.
  Keep the full required scope in the plan and design for its state relationships.
- Let the main exhibit, document, conversation, chart or playable scene anchor the screen.
  A large slogan should not push the actual activity out of sight. Reveal the next useful
  action and enough real content to make the product's purpose immediately apparent.
- Compose with scale, alignment, grouping and purposeful space. Use rows, sections, split
  panes, figures or canvases as the content warrants; put a border/card around a thing only
  when the grouping helps. Avoid repeated containers inside containers and equally loud elements.
- Use consistent tokens for surface/text/accent/state colors, type roles, spacing, radii
  and elevation. Define interaction states too. Patch the owning styles instead of accumulating
  late overrides that make earlier design decisions unreliable.
- Choose meaningful assets or original graphics when the subject benefits. Inspect their
  quality, crop, scale and attribution. In a simulation, improve geometry, materials, lighting
  and camera before decorating its surrounding panels. Never invent results to fill a chart.
- Verify fonts and assets actually load and render. A CSS font-family declaration does not
  prove the intended face is used. Check fallback layout, glyph coverage and network failures;
  do not claim a typography improvement from source inspection alone.
- Separate rendering, data/state and interactions when useful; use focused, composable units
  without arbitrary file-length limits.
- Use the simplest state owner: local for isolated UI, shared for related components,
  URL for shareable filters/views, cached server state for remote data. Add a global store
  only when state relationships justify it. Preserve existing interfaces and conventions.
- Provide meaningful loading, empty, error and recovery states. Keep feedback immediate;
  optimistic updates require rollback on failure. Avoid expensive work on every render/frame,
  and measure suspected performance problems in the actual interaction.
- Read the exact contracts needed for the next change, not every component or data table.
  Reuse valid evidence; update the work record after meaningful implementation/checks.

## Interaction and accessibility

- Prefer semantic controls, associated labels and correct heading order. Give icon-only
  actions accessible names. Support keyboard activation, visible focus and logical Tab order.
- Manage focus on navigation/dialog changes; modal focus stays inside until dismissal,
  then returns to the trigger. Custom controls need the relevant keyboard semantics.
- Preserve contrast, readable text, focus visibility and usable touch targets; never convey
  state only by color. See the accessibility reference for thresholds and exceptions.
- Design narrow layouts intentionally: reorder or collapse secondary information, preserve
  the main content and actions, and keep charts legible. Check representative phone and desktop
  widths and any intermediate breakpoint that changes layout. Do not simply shrink everything.
- Use motion to explain feedback, continuity or spatial change. Respect reduced motion and
  avoid delaying useful content for entrance choreography.

## Verification

Use the mounted browser capability for rendering and interactions, following any declared
acceptance route. Check the primary action, keyboard/focus behavior, relevant
persistence, responsive layout, console/runtime errors and loading/error/empty states.
Use accessibility tools when available, but do not treat automated results as full usability
proof. Run code/build checks relevant to the change and report skipped or unavailable checks.

## Visual self-review and improvement

Inspect actual rendered screenshots early, before styling spreads across every view. Include
the opening composition, a populated working state and a narrow-screen journey. Read the
visual review section of the direction reference; judge focal hierarchy, density, subject
quality, typography and consistency, not only overflow or contrast.

Name the most consequential visible weakness, explain a specific change, implement it and
compare equivalent content/viewport/state. Fix broken fonts, weak main imagery, excessive
headline space, unreadable plots or noisy nested panels before spending time on decorative
details. Revisit the affected interaction after the change and preserve user data.

Record a short observation → design change → visible result with links to evidence and
remaining defects. Do not turn reviewing into repeated reports without design changes.
A palette swap, asset download, screenshot count, HTTP 200 or numeric accessibility result
does not establish beauty. A pleasing opening screen also does not complete missing workflows.
Keep visual judgment, functionality and accessibility distinct; do not hide open visual
defects behind a clean console. This is self-review, not independent audience endorsement.
Respect explicit limits on execution and identify any unobserved result as unverified.

## Read on demand

- `references/visual-direction.md`: visual decisions, useful starting values, assets and
  screenshot critique for a new identity or substantial visual work.
- `references/react-patterns.md`: component composition and state patterns **only for React**.
- `references/accessibility-checklist.md`: detailed accessibility checks when implementing
  a relevant control or investigating a specific issue.

Read the needed section rather than loading every reference up front. Examples illustrate
methods; adapt them to the project's language and tools.
