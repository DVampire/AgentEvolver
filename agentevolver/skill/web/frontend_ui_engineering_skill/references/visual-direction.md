# Visual direction for a working product

Use this reference when choosing a new identity or improving a substantial visual surface.
Explicit user direction and established brand assets take precedence. The examples below are
starting points for reasoning, not themes to copy into every project.

## Decide what the screen is about

Before choosing colors, identify the main subject and the activity it supports: exploring
an exhibit, reading a conversation, manipulating a simulation, comparing strategies or playing.
Compose around that subject. A slogan, dashboard card grid or decorative illustration should
not displace the actual reason to visit.

Keep a short design decision in the existing plan or work record:

- **Subject and mood:** what the visitor should notice and feel, tied to this product.
- **Composition:** dominant content, secondary information, navigation and primary action.
- **Type:** display, reading, UI and numeric roles, with usable language/fallback coverage.
- **Color and material:** surface hierarchy, text, focus/action accent and semantic states.
- **Signature:** a meaningful visual or interaction idea that belongs to this subject.

If direction is unclear, compare alternatives that differ in composition and subject treatment,
not just accent hue. For example, an exhibit can open on an interactive specimen or a curated
image sequence; a research tool can start from a linked chart/table or an investigation timeline.
Choose a direction and implement the main view early. A mood board is useful only if it changes
that choice; avoid long inspiration searches and unused design documents.

## A practical aesthetic starting point

For an unbranded working application, start with crisp neutral surfaces, dark readable text,
a small number of accents, clear alignment and strong content. Give weight and space to the
important thing. Visual richness can come from an excellent exhibit, photograph, chart or scene;
the surrounding chrome can stay quiet.

Choose a different language when the subject supports it. These examples deliberately have
different compositions as well as palettes:

| Direction | Composition and character | Example color roles |
| --- | --- | --- |
| Precision workspace | Large analytical content beside a compact inspector; strong numeric alignment, restrained navigation, readable dense tables | Ground `#F5F7FB`, surface `#FFFFFF`, text `#18212F`, secondary text `#566174`, action `#2457E6` with white text |
| Contemporary exhibition | A compelling specimen/image/interactive demonstration, a clear question and a compact caption; generous variation in scale | Ground/surface `#FFFFFF`, text `#191923`, secondary text `#60606D`, action `#9D2368` with white text; exhibit colors come from its content |
| Immersive world | The scene fills the space, with a compact readable HUD and contextual controls; depth comes from light, silhouettes and materials | Ground `#0B1324`, panel `#15223A`, text `#EAF2FF`, secondary text `#AAB9CF`, accent `#47D7C1` with dark `#062323` text |

Derive final colors from the actual content, assets and brand. Check the colors together in
the rendered layout, including hover/selected/focus/error states and charts. These pairings
are examples, not certification of a complete theme. Keep brand accent, interaction selection
and success/failure semantics distinct; never color all important things the same way.

Avoid adopting cream/terracotta/italic-serif styling as a ready-made signal of sophistication.
Likewise, a black background and neon glow do not create an immersive product. Use warmth,
serifs, gradients or expressive colors intentionally when the product calls for them. Do not
reuse the same ornamental asterisk, generic geometric logo or brand-adjacent motif across
unrelated products; a restrained wordmark is preferable to an arbitrary symbol.

## Typography that survives implementation

Choose fonts by function and actual rendering. A clear sans-serif is a useful starting point
for controls and sustained UI reading. A display or serif face can give a specific publication
or exhibit character; it should not automatically occupy every heading or numeral. One family
with well-chosen weights can be enough. Keep expressive type out of long controls and dense data.

Useful starting values, adjusted to the font and content:

- Reading text around 16–18 CSS px, comfortable line height and roughly 55–75 characters per
  line for long Latin prose. CJK and mixed-language content need their own visual check.
- Labels and table text usually 13–14 px when space is constrained; do not shrink essential
  explanations into tiny uppercase text. Make units, errors and chart axes readable.
- Display sizes depend on composition: a working app often needs a compact 28–40 px title,
  whereas a poster-like opening may justify 48–80 px. These are not required scales. If the
  headline hides the product on a phone, shorten it or change the composition.
- Use numeric alignment/tabular figures for comparison. Avoid extremely tight tracking on
  body text, CJK or small labels. Deliberate line breaks must still work at narrow widths.

Use an existing suitable font, licensed local files or an authorized font source. Read the
font's license and load only needed faces/weights. When using web fonts, verify requests and
font-face availability as well as the visible result. `getComputedStyle(...).fontFamily`
only reports the declared stack; even a successful font check is not proof that every glyph
uses that face. Inspect the browser's rendered-font information when available and test fallback.

Prefer explicit stylesheet links in the document head or well-placed local `@font-face` rules.
If using CSS `@import`, keep it before ordinary style rules; late imports are ignored. Adding
a new rule above an import can silently change the whole site's typography. Confirm loading
again after stylesheet edits. See [MDN's import contract](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@import).

## Composition, surfaces and density

Use scale and grouping before adding borders. Navigation, title, main content and supporting
tools need different visual weights. Cards fit discrete items; continuous text, a trade ledger,
a conversation or a canvas often benefits from open sections, rows or a split view instead.
Do not put every paragraph, metric and control inside its own rounded panel.

A small spacing scale such as 4/8/12/16/24/32/48 px helps rhythm; choose gaps for relationships,
not uniform emptiness. Keep radii and shadows consistent with material: a flat exhibit, a
precise tool and a floating game HUD need not share one radius. Large rounded containers,
heavy shadows and pill labels used everywhere erase hierarchy.

Test realistic density early: long names, populated tables, a saved notebook, expanded details,
errors and selected states. On narrow screens, prioritize and recompose. Move secondary controls
into a sensible disclosure, keep the main artifact visible and preserve touch/keyboard access.
Do not fix overflow by hiding needed content or shrinking all text.

## Make the main subject visually convincing

Choose assets by relevance, quality and style compatibility. Inspect downloaded images,
illustrations or models before integrating; match crop, resolution, material and lighting.
Keep provenance and license information. Prefer actual product content over generic technology
photos. When a mounted image-generation capability is available and an original raster asset
would help, use it deliberately; do not assume that capability exists.

Code-native SVG/canvas is appropriate for diagrams, specimens and truthful data graphics.
Simple geometric art can be excellent when its composition is intentional. It is not a substitute
for required spatial depth, meaningful material or detailed imagery just because it is easy
to generate. In a 3D product, inspect camera, silhouettes, light, surface scale and atmosphere;
a prettier HTML overlay cannot repair a weak scene.

For charts, make the meaningful comparison visible with readable axes, restrained gridlines,
consistent series colors, units and annotation. Reserve saturated colors for the information
that matters. For an exhibit, let the visitor manipulate and inspect the subject rather than
read a long introduction above it. For a game, contextual help should leave the world legible.

## Review the rendered result

Compare the intended direction with real desktop and narrow screenshots, including a populated
working state. Use the same content, viewport, scroll and state for before/after comparisons.
Look at the screenshot before reading source or accepting the implementation's own explanation.

| Visible symptom | Useful next design change |
| --- | --- |
| The headline dominates while the actual product sits below the fold | Recompose around the exhibit/chart/action; reduce or shorten the display copy |
| Most of the page has the same pale or dark midtone | Establish text and surface hierarchy; separate accent from background instead of tinting everything |
| Every block looks equally important | Remove redundant containers, vary scale and place related content together |
| The scene or image is weak but the surrounding UI is polished | Improve the subject's assets, crop, geometry, lighting or camera |
| A long working state becomes a wall of boxes and labels | Edit the hierarchy, improve rows/sections and disclose secondary controls |
| The intended type looks generic or wraps unexpectedly | Check actual font loading/weights/glyphs, then adjust measure and scale |

Keep visual findings concrete: what captures attention, what competes with it, what feels
unfinished and what change would improve it. Implement the most consequential correction and
revisit the affected journey. Do not call a design good simply because it has no overflow,
passes contrast checks or uses a fashionable font. Record remaining aesthetic weaknesses
alongside functional ones. Self-review is a design judgment, not independent user approval.
