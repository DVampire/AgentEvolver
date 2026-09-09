# Unified resource CLI

`scripts/assets.py` belongs to this skill and uses only Python 3.12's standard library.
Invoke it through the existing base Bash tool, using the absolute script path returned
by the skill loader. No model credentials, additional Python packages or new tools are
needed. The base needs outbound HTTPS. Game execution remains a separate operation.

## Commands

Examples below assume the canonical workspace as the current directory. Replace
`/absolute/skill` with the actual skill directory, not the workspace or a guessed path.

```bash
python /absolute/skill/scripts/assets.py search forest --provider all --limit 5 --godot 4.7
python /absolute/skill/scripts/assets.py search 角色 --provider catalog
python /absolute/skill/scripts/assets.py search "Phantom Camera" --provider store --page 1
python /absolute/skill/scripts/assets.py info catalog:kaykit-forest
python /absolute/skill/scripts/assets.py download catalog:kaykit-forest --out asset_sources --extract
python /absolute/skill/scripts/assets.py info store:ramokz/phantom-camera --godot 4.7
# Copy the selected release's key from info; do not guess it or assume it remains current.
python /absolute/skill/scripts/assets.py download store:ramokz/phantom-camera --file RELEASE_KEY --godot 4.7 --out asset_sources --extract
```

All commands emit JSON. Exit 0 means the requested operation succeeded; exit 2 means
failure, including a partially failed federated search. Search still returns successful
providers' results with `partial: true`, per-provider errors and pagination. Never treat
an unavailable provider as an empty catalog. `--limit` (1–50) applies per provider;
`--page` is one-based. Remote search uses the provider's ranking and terminology;
the curated catalog additionally includes Chinese keywords.

Resource IDs are stable identifiers returned by search:

- `catalog:kenney-nature`, `catalog:kaykit-adventurers`, etc.: entries in `resources/catalog.json`.
- `store:publisher-slug/asset-slug`: the current Godot Store.
- `assetlib:1822`: the older Godot AssetLib's numeric entry ID.

`info` fetches current download choices without saving asset files. It may create an
anonymous itch free-download session; it does not log in, purchase or submit an email.
File keys identify releases/uploads, not display names. When there is more than one
choice, `download` requires `--file KEY` so a model cannot silently choose the wrong
version or edition. It refreshes temporary download URLs immediately before fetching.

## Supported sources and boundaries

| Source | Search | Download |
| --- | --- | --- |
| Godot Store | Live `/api/v1/search/query/`, with engine version and stable-release filters | Live asset details/releases, exact release key, zero-price ZIP only |
| Legacy AssetLib | Live documented API, with engine version and addon/project entries | Current entry ZIP with version/commit and optional upstream hash; different major/newer engine targets are rejected |
| Kenney | Curated pack names/tags | Discover the ZIP link on the actual official pack page |
| KayKit / selected Quaternius itch packs | Curated pack names/tags | Public zero-price flow, only uploads actually offered on the free download page |
| GDQuest Stylized Sky | Curated entry | Resolve the official GitHub repository HEAD to a commit SHA, then fetch that commit's ZIP |
| Quaternius Drive folder, shader-text pages, Poly Haven multi-file resources | Curated discovery/reference | Explicit manual status with provider-specific next steps; these are not implemented automatic download adapters |

The curated list is maintained in JSON, so adding another supported Kenney/itch pack
does not require changing the CLI. Keep official URLs and accurate free-edition/license
notes. Extending to a new API requires an adapter and verification. Catalog descriptions
and licenses are recorded metadata, not an automatic legal or aesthetic judgment.

The free itch flow does not expose paid Extra/Source files. A page/API change, login,
unknown price, missing free link or unsupported format produces a structured failure.
There is no silent mirror fallback, purchase, CAPTCHA bypass or automatic plugin install.
Provider version filtering is a discovery aid; native binaries, renderer requirements,
animation rigs, code behavior and visual quality still need inspection in our environment.

## Staging and receipts

Default limits: 120 seconds for network/archive work, 64 MiB downloaded, 512 MiB
expanded, 20,000 ZIP entries. Override deliberately with `--deadline`, `--max-mib`
and `--max-expanded-mib` for an appropriate pack. Also bound the surrounding Bash call;
allow time for cleanup. Requests have socket timeouts and work loops check the deadline.

ZIP integrity is checked while reading every file. Extraction rejects parent/absolute
paths, backslashes/drive paths, symlinks/special files and case-insensitive duplicate
paths. Work is staged in a temporary directory; failures remove partial output.
Successful output is published as:

```text
asset_sources/<resource-id>-<archive-hash>/
  original.zip
  manifest.json
  files/             # Only with --extract; original relative paths preserved
```

The destination is never overwritten. If the same archive already exists, inspect its
manifest and reuse the local files. The manifest records source/page, license metadata,
selected release/upload, source revision where available, time, archive SHA-256, per-file
hashes, license-file paths and pending import/review status. Signed URL queries and itch
session tokens are excluded. `--sha256 HEX` verifies a previously recorded or publisher
supplied expected archive digest; an independently computed hash alone is not a publisher
signature. Full file lists stay in the manifest, not the live prompt.

Review the license and addon code, then copy only the chosen resources into the game,
preserving glTF buffers/textures and required attribution. Never mass-copy every format,
demo, script and Blender file into the import tree. Update `ASSET_SOURCES.md` and the
plan's art work item with manifest paths and actual import/visual results. Acquisition
success is not evidence that a game is beautiful, playable or that agent evolution occurred.

## API references

- [Godot Store's published OpenAPI schema](https://store.godotengine.org/api/v1/openapi.json)
- [Legacy Godot AssetLib API](https://github.com/godotengine/godot-asset-library/blob/master/API.md)
- [Poly Haven API for manual multi-file acquisition](https://polyhaven.com/our-api)

The Store and itch contracts were checked against live responses on 2026-09-09.
itch's web download flow is not a promised stable public API; adapter failures should
remain explicit if the website changes.

Real acquisition checks on that date downloaded/validated KayKit Forest and a selected
Store Phantom Camera release inside `python:3.12-slim` with bridge networking. CLI checks
also acquired Kenney UI, the older AssetLib Phantom Camera entry and a commit-pinned
GDQuest sky archive. Terrain3D and Quaternius character free-upload metadata resolved.
Local receipts are under `output/asset-library-verification/cli/`; this does not certify
installation, renderer compatibility or aesthetic quality. Inspecting the sky archive
also confirmed its mixed MIT-code / CC-BY-NC-SA-4.0-art licensing.
