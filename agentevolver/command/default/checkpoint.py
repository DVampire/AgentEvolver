"""/checkpoint — snapshot the current version of every capability (CONTROL).

The evolve loop's undo anchor: record where everything is *before* an evolution round,
so a regression can be rolled back to a known-good set. Writes a JSON snapshot under
``<run_dir>/command/checkpoints/`` so it survives the process and MetaAgent can read it.
"""
import os
import json
import datetime
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response


@COMMAND.register_module(force=True)
class CheckpointCommand(Command):
    name: str = "checkpoint"
    description: str = "Snapshot the current version of every registered capability to a restore point."
    type: CommandType = CommandType.CONTROL
    usage: str = "/checkpoint [label]"
    permission_mode: str = "workspace_write"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        from agentevolver.version import version_manager
        from agentevolver.config import config
        from agentevolver.utils import assemble_project_path

        label = args[0] if args else datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        data = await version_manager.list()
        snapshot = {}
        for ctype, names in data.items():
            for n in names:
                snapshot[f"{ctype}/{n}"] = await version_manager.get_current_version(ctype, n)

        ckpt_dir = assemble_project_path(os.path.join(config.run_dir, "command", "checkpoints"))
        os.makedirs(ckpt_dir, exist_ok=True)
        path = os.path.join(ckpt_dir, f"{label}.json")
        payload = {
            "label": label,
            "created_at": datetime.datetime.now().isoformat(),
            "snapshot": snapshot,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        return self.ok(
            f"Checkpoint '{label}' saved: {len(snapshot)} component(s) → {path}",
            data={"label": label, "path": path, "snapshot": snapshot},
        )
