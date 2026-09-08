---
name: frontend_ui_engineering_skill
description: "Builds production-quality UIs. Use when building or modifying user-facing interfaces. Use when creating components, implementing layouts, managing state, or when the output needs to look and feel production-quality rather than AI-generated."
version: 1.1.0
type: worker
license: N/A
category: web
requirements: [cpu]
enable_evolving: false
metadata: {}
---

# Frontend UI Engineering

Build an accessible, responsive, polished interface for the actual users and content.
Follow the project's instructions, stack and design system. Use mounted framework tools;
do not introduce a framework or dependency merely because an example uses it.

## Design and implementation

- Build a runnable primary journey first. Separate rendering, data/state and interactions
  when useful; use focused, composable units without arbitrary file-length limits.
- Use the simplest state owner: local for isolated UI, shared for related components,
  URL for shareable filters/views, cached server state for remote data. Add a global store
  only when state relationships justify it. Preserve existing interfaces and conventions.
- Design around real content and information priority. Use coherent spacing, type hierarchy,
  semantic colors and deliberate visual identity. Avoid generic stock layouts; gradients,
  effects and animation should serve the requested experience rather than a default style.
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
- Meet WCAG AA contrast (4.5:1 normal text, 3:1 large text); never convey state only by color.
  Respect reduced motion, readable text and touch targets.
- Start with narrow layouts and verify representative widths (320, 768, 1024, 1440px),
  including wrapping/overflow. Preserve access to primary actions and recovery at each size.

## Verification

Use the mounted browser capability for rendering and interactions, following the task's
independent acceptance route. Check the primary action, keyboard/focus behavior, relevant
persistence, responsive layout, console/runtime errors and loading/error/empty states.
Use accessibility tools when available, but do not treat automated results as full usability
proof. Run code/build checks relevant to the change and report skipped or unavailable checks.

## Read on demand

- `references/react-patterns.md`: component composition and state patterns **only for React**.
- `references/accessibility-checklist.md`: detailed accessibility checks when implementing
  a relevant control or investigating a specific issue.

Read the needed section rather than loading every reference up front. Examples illustrate
methods; adapt them to the project's language and tools.
