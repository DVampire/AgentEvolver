"""Prepare only the uploaded release; never access the authoring workspace."""
import json
import re
from pathlib import Path


def prepare(root):
    root = Path(root)
    project = root / "project.godot"
    if not project.is_file():
        raise ValueError("source_dir must be the Godot project directory containing project.godot")
    override = root / "override.cfg"
    if override.is_file():
        text = override.read_text()
        text = re.sub(
            r"; godot-agent-loop: begin interaction server[^\n]*\n.*?"
            r"; godot-agent-loop: end interaction server[^\n]*\n?", "", text, flags=re.S,
        )
        override.write_text(text)
    name = re.search(r'^config/name=(".*")$', project.read_text(), flags=re.M)
    title = json.loads(name.group(1)) if name else "Godot playtest"
    return {"title": title}


if __name__ == "__main__":
    metadata = prepare(Path.cwd())
    Path("/opt/godot-play/public/game.json").write_text(json.dumps(metadata, ensure_ascii=False))
