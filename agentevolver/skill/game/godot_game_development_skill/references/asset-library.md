# Art library and acquisition for Godot

Catalog checked on 2026-09-09. These are official starting points, not a promise that
every download remains accessible or every model fits the game. Recheck the selected
pack, free edition and actual archive. Keep this reference on disk; the live plan Brief
needs only the art work item's status and paths to decisions, assets and evidence.

## Godot ecosystem resources

Search the new [official Godot Store](https://store.godotengine.org/) through
`assets.py search QUERY --provider store`; the [older AssetLib](https://godotengine.org/asset-library/asset)
is also available with `--provider assetlib`. They include plugins and templates as
well as art; do not confuse installing a system with completing the game's visuals.

The curated CLI catalog additionally includes [KayKit Forest](https://kaylousberg.itch.io/kaykit-forest),
[Adventurers](https://kaylousberg.itch.io/kaykit-adventurers) and
[Character Animations](https://kaylousberg.itch.io/kaykit-character-animations) as a
coherent environment/character family with free glTF editions. It lists Phantom Camera,
Dialogue Manager and Terrain3D through the Store, and
[GDQuest Stylized Sky](https://github.com/gdquest-demos/godot-4-stylized-sky) through its
source repository. See each returned entry's compatibility and acquisition notes.
The GDQuest sky archive has separate licenses: MIT for code, CC-BY-NC-SA-4.0 for
art. Do not assume its example image textures have the shader code's license.

## Select a coherent family

For a stylized 3D companion RPG, favor consistent proportions, clear silhouettes,
simple materials and expressive animation. Make the main creature, player, village
and HUD look as though they belong in the same game. More assets do not compensate
for weak camera placement, uniform lighting or unrelated visual styles.

| Official source | Candidate role | License and format / acquisition notes |
| --- | --- | --- |
| [Quaternius Cute Animated Monsters](https://quaternius.com/packs/cutemonsters.html) | Expressive companion/enemy bases | The page lists 21 animated monsters, CC0 and glTF/FBX/OBJ/Blend. The current download button opens a Google Drive folder; inspect the files actually offered. OBJ does not carry the advertised skeletal animation. |
| [Quaternius Universal Base Characters](https://quaternius.com/packs/universalbasecharacters.html) | Player and NPC base meshes | CC0; glTF/FBX options, humanoid rig. Free and paid Source editions differ. Do not assume the free archive includes the complete Godot project, custom shaders, source Blender files or all models/animations. |
| [Quaternius Medieval Village](https://quaternius.com/packs/medievalvillage.html) | Buildings and settlement props | CC0; page lists FBX/OBJ/Blend. Adapt materials and proportions to the coastal setting; do not assume GLB exists. |
| [Quaternius Ultimate Nature](https://quaternius.com/packs/ultimatenature.html) | Vegetation and rocks matching older Quaternius packs | CC0; FBX/OBJ/Blend listed. Compare with Kenney before choosing a dominant environment family. |
| [Kenney Nature Kit](https://kenney.nl/assets/nature-kit) | Stylized vegetation, rocks and landscape dressing | CC0; official free ZIP via “Continue without donating”. Inspect archive formats and copy the selected format with its dependencies. |
| [Kenney UI Pack](https://kenney.nl/assets/ui-pack) | Buttons, panels and controls | CC0; official free ZIP. Select one theme, build a Godot Theme/NinePatchRect treatment and maintain consistent padding/type hierarchy. |
| [Poly Haven](https://polyhaven.com/) | Optional sky, lighting reference and supporting materials | [Assets are CC0](https://polyhaven.com/license). Photoreal assets often clash with stylized characters: evaluate and simplify rather than mixing by default. Use small textures/HDRIs first. |

The candidate roles and suggested combination above are design judgments. A source's
marketing render is a reference, not evidence of how an asset looks in our renderer.
Retain original game identities, writing and creature behaviors when adapting a public
base mesh; do not claim authorship of downloaded art.

## Acquisition through the existing Bash tool

The default base has Python 3.12 and outbound network access (`base_network=bridge`).
The Godot container is offline. Both see the same workspace, so download in base Bash
and let Godot import the resulting local files. There is no need to add a file tool,
browser environment, network access to the engine or asset-download MCP server.
Respect a deliberately offline configuration; report the restriction rather than changing it.

1. Open the official pack page using Python urllib. Follow its actual free download
   target; never invent a filename from the pack title. Some targets are in a button's
   `onclick`, rather than an anchor. Record the page and resolved target separately.
2. For Kenney, the free continuation anchor currently links directly to a ZIP. Select
   that pack's link, not the paid all-in-one bundle. For Quaternius, follow its official
   Drive/itch.io target; it may need another public download step. A folder, purchase page
   or HTML response is not an asset archive. If unavailable from Bash, record the blocker
   and use an accessible compatible pack. Do not invent a direct URL or claim success.
3. Download only the selected pack, with a deadline and size bound. Preserve the source
   archive in `workspace/asset_sources/`, outside `project.godot`'s directory. Validate
   the archive, inspect license and file listing, then select meshes, textures and animations.
4. Extract into a new staging directory; reject absolute/parent-traversing paths, symbolic
   links and excessive expanded size. Never execute scripts or installers from an art pack.
   Copy only required assets into `game/assets/vendor/<provider>/<pack>/`, preserving
   relative dependencies. Leave alternative formats, source archives and previews outside
   the game import tree. Keep attribution/license files with the delivered resources.
5. Keep downloads local for offline play and export. Runtime game code must not depend
   on a provider URL, the author's machine or a development-only absolute path.

Use the bundled `scripts/assets.py` interface instead of writing a new downloader
for each run. See [CLI commands, supported providers and receipts](assets-cli.md).
For example, `search forest`, `info catalog:kaykit-forest`, then
`download catalog:kaykit-forest --out asset_sources --extract`. Use the mounted script's
absolute path and the existing base Bash tool. Unsupported sources return manual
instructions instead of an invented archive. Full inventories remain in manifest.json.

For Poly Haven automation, use its [documented public API](https://polyhaven.com/our-api),
not a scrape of gallery pages: `/assets` for discovery and `/files/{id}` for actual file
URLs, sizes and dependencies. Cache metadata, identify requests with an application
User-Agent and follow the live API's credit terms (separate from the CC0 asset license).
Choose 1k/2k resources initially and download required dependencies as well as the mesh.

## Godot import and visual acceptance

Follow [Godot's format documentation](https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/available_formats.html):
prefer GLB/glTF; preserve adjacent `.bin` and textures for non-embedded glTF. The pinned
Godot supports FBX through ufbx. OBJ is suitable for static props, not skeletal animation.
Direct `.blend` import needs Blender, which is not provided by the current engine image.
Choose an available interchange format instead of copying `.blend` into the import tree.

Stop a running game before import. Import one representative model first; inspect
its bounds, forward direction, material/texture dependencies, skeleton and actual clip
names. Wrap imported scenes with authored actor scenes for collisions, logic and effects
so reimport does not destroy gameplay changes. Check idle, locomotion and an interaction
at the game's intended scale and camera distance before integrating the whole cast.

Build a representative arrival scene and battle/UI view. Review native frames for:

- Recognizable silhouettes and distinct species beyond a name tag or color swap.
- Consistent proportions/material treatment; no missing textures, accidental white
  materials, broken skeletons, floating feet or animation sliding.
- A clear focal point and navigable paths, with foreground/middle/background depth;
  intentional sun/sky/fill balance rather than a uniformly lit empty ground plane.
- Dialogue, icons and combat intent readable at play resolution; one UI family with
  consistent spacing, contrast and selected/disabled/focus states.
- Measured responsiveness on the actual renderer; constrained texture sizes, sensible
  instancing and restrained transparency/shadows where needed.

For each problem, retain the frame path, observation, intended change and recheck status
in `reports/art_review.md`. This is an agent self-review, not independent audience approval
or a numeric guarantee of beauty. Continue iterating or leave the defect explicitly open.

Maintain `game/assets/ASSET_SOURCES.md` with provider/author, pack and free edition,
official page, license URL/file, resolved download URL and date, archive SHA-256,
imported paths, modifications, required attribution and import/visual evidence. In
plan.md keep only the art direction, chosen family, work status and links to this ledger
and review; the Brief carries the current progress index. Downloading art is product
development, not evidence of agent-system evolution by itself.
