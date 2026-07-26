---
id: youtube
name: YouTube
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/youtube
status: complete
version: "1.0.0"
tools: 7
requirements: [google-api-python-client, pytube, youtube-transcript-api]
---

# YouTube

Migrated from the Langflow **youtube** bundle — the reference bundle for the
`BundleTool` mold. All 7 tools are **implemented** (faithful ports of the
Langflow component logic) and execute through the shared datasource →
`plugin_manager` path. Each returns the canonical `{message, data, files}`
`Response` with `data.records` (list of rows) instead of a pandas DataFrame.

## Tools

| id | name | needs | status |
|----|------|-------|--------|
| `youtube.search` | YouTube Search | API key | complete |
| `youtube.channel` | YouTube Channel | API key | complete |
| `youtube.comments` | YouTube Comments | API key | complete |
| `youtube.video_details` | YouTube Video Details | API key | complete |
| `youtube.trending` | YouTube Trending | API key | complete |
| `youtube.playlist` | YouTube Playlist | — (pytube) | complete |
| `youtube.transcripts` | YouTube Transcripts | — (youtube-transcript-api) | complete |

## Configuration

The API key resolves from (in order): the call `api_key` arg → the
`youtube_plugin` config block → the `YOUTUBE_API_KEY` environment variable.
`youtube.playlist` and `youtube.transcripts` need no key.

## Requirements

Provider libraries are imported lazily inside each tool's `__call__`, so the
bundle registers even when they are absent — a call then returns a clear
"failed result" rather than crashing. To actually run them:

```
pip install google-api-python-client pytube youtube-transcript-api
```

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`
(synced to the frontend by `scripts/sync-bundle-icons.sh`).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/youtube/`
- Shared helpers: `_base.py` (`YouTubeTool`: client + video-id extraction)
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
