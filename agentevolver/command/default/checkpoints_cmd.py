"""/checkpoints — list saved checkpoints (CONTROL, read-only)."""
import os
import glob
import json
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.paths import P, path_manager
from agentevolver.response.types import Response


@COMMAND.register_module(force=True)
class CheckpointsCommand(Command):
    name: str = "checkpoints"
    description: str = "List saved checkpoints (restore points)."
    type: CommandType = CommandType.CONTROL
    usage: str = "/checkpoints"
    permission_mode: str = "read_only"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        from agentevolver.config import config
        ckpt_dir = str(path_manager.under(config.log_root, P.LOG_COMMAND_CHECKPOINTS))
        files = sorted(glob.glob(os.path.join(ckpt_dir, "*.json")))
        if not files:
            return self.ok("No checkpoints saved yet.")

        lines: List[str] = []
        for p in files:
            try:
                d = json.load(open(p, encoding="utf-8"))
                lines.append(f"  {d.get('label')}  —  {len(d.get('snapshot', {}))} component(s)  —  {d.get('created_at', '')}")
            except Exception:
                lines.append(f"  {os.path.splitext(os.path.basename(p))[0]}")
        return self.ok("\n".join(lines))
