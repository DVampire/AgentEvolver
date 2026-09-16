# Workbench recordings

The eleven clips and their JPEG posters were captured on September 16, 2026,
against a live Gateway and a prepared sample project. The MP4 files use H.264,
1600 × 1000 pixels, without audio. The homepage screenshot is 1440 × 1240.

The sample plan and files come from the
[recording fixture](../../../scripts/ui-tour-fixture.json). No model task is
submitted. Science executes a Python cell on synthetic data and saves a notebook;
Canvas connects two nodes. Runtime is idle, and the machines clip shows the
launch controls. These recordings demonstrate interface behavior.

## Refresh the assets

Use a dedicated local Gateway with an isolated `AGENTEVOLVER_HOME`. Create a
sample session, then copy the fixture's relative files into that session's
project root. Keep the normal development Gateway separate so real projects,
credentials and remote-host settings do not appear in the recordings.

Prepare the [Code image](../../../docker/vscode/README.md) and install the
[Science dependencies](../../../frontend/README.md) before recording.

```bash
UI_URL=http://127.0.0.1:5175 UI_SESSION_ID=your-sample-session \
  python scripts/record-ui-clips.py /tmp/uiclips
scripts/encode-ui-clips.sh /tmp/uiclips docs/assets/ui
```

The [recorder](../../../scripts/record-ui-clips.py) requires successful UI steps,
captures an intentional poster for each clip, and records the start/end marks
in `manifest.json`. The [encoder](../../../scripts/encode-ui-clips.sh) uses those
marks to remove setup time. Failed recordings are not included in the manifest.
Review the resulting clips before updating the assets and the bilingual copy
in the [tour page](../../ui.html).
