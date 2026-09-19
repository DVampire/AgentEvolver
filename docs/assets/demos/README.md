# Results and demo assets

The [results and demos page](../../demos.html) is a self-contained GitHub Pages
surface. [demos.css](../demos.css) owns its layout and [demos.js](../demos.js) owns
bilingual copy and the native video dialog. It uses the shared site navigation.

## Benchmark provenance

[benchmark.json](benchmark.json) records the chart values and their provenance.
AgentEvolver's **82.08%** on SWE-bench Pro Public is a formal evaluation result
reported by the project owner from a separate machine on 2026-09-19. The underlying
model, configuration, raw evaluation report and official leaderboard acceptance
are not supplied with this page. The interrupted local run is not the source.

The selected comparison values are transcribed from the
[CodingFleet compilation](https://codingfleet.com/blog/swe-bench-pro-leaderboard-2026/),
updated 2026-09-11 and accessed 2026-09-19. Its upstream links are retained in the
JSON as attribution, not as independently verified reports. The
[Scale Public leaderboard](https://labs.scale.com/leaderboard/swe_bench_pro_public)
is linked separately. Models, harnesses and budgets differ; the chart does not
claim an official rank, a controlled comparison or a measured evolution uplift.

Keep the static chart, score headline, JSON values and bilingual notes consistent
when a result changes. Do not derive a score by merging partial or retried runs.

## Films

[manifest.json](manifest.json) lists each MP4, poster, subtitle file, SHA-256 hash,
observed outcome and scope. The films combine historical experiment records with
new recordings of actual product artifacts. They are not recordings of newly
launched agent experiments. Captions are Chinese, with original ambient music
and no narration. Separate SRT files accompany the burned-in editorial captions.

Videos use H.264/AAC, 1920 × 1080, 24 fps, 80 seconds each. They live in this
published directory; no output-directory links, live agent services or external
video hosts are required. The page initially loads posters only and assigns the
MP4 source when the user opens a film. Closing the dialog releases the media.
Cards retain ordinary MP4 links if JavaScript is unavailable.

The existing Pages workflow uploads `docs/`, including these media assets.
