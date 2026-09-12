# Documentation website

GitHub Pages publishes this directory as a static site through
[deploy-pages.yml](../.github/workflows/deploy-pages.yml). It needs no frontend build or
live Gateway. Preview the directory locally:

```bash
python -m http.server 18765 --bind 127.0.0.1 --directory docs
```

## Visual and content ownership

[theme.css](assets/theme.css) carries the site's color and typography tokens, aligned
with [agentevolver/visual](../agentevolver/visual/README.md): deep green surfaces, mint
emphasis, muted text and semantic accents. It is self-contained because GitHub Pages
publishes only `docs/`. Layouts live in [home.css](assets/home.css),
[site.css](assets/site.css) and [ui.css](assets/ui.css).
[chrome.css](assets/chrome.css) and [chrome.js](assets/chrome.js) own shared navigation.

The [homepage](index.html) introduces the system and its task examples. The
[architecture guide](architecture.html) explains implementation boundaries and links
their source owners. Their translations and interactions live in
[home.js](assets/home.js) and [architecture.js](assets/architecture.js), with shared
language and diagram switching in [site.js](assets/site.js). `?lang=en` and `?lang=zh`
select a language explicitly; the saved choice persists between pages.

Keep both languages aligned when changing behavior descriptions. Domain Builders reuse
the shared agent lifecycle; product quality, capability evaluation, structural admission
and downstream use are different claims. The [documentation standard](DOC-STANDARD.md)
describes where detailed module contracts belong.

The [diagram source guide](diagrams/README.md) contains editable PowerPoint downloads,
regeneration commands and a map from diagram areas to implementation files. Update the
PPTX, SVG and PNG together. The UI tour contains recorded feature demonstrations; its
recordings are not a live deployment or a guarantee of the current theme's exact pixels.
