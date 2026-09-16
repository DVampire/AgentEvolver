"""/restore-checkpoint — restore every capability to a saved checkpoint (CONTROL, danger).

The whole-round undo: roll the entire system back to the versions recorded by a prior
``/checkpoint``. Complements the single-capability ``/rollback``.
"""
import os
import json
from typing import List, Optional

from agentevolver.registry import COMMAND
from agentevolver.command.types import Command, CommandType, CommandContext
from agentevolver.response.types import Response
from agentevolver.command.default._helpers import get_manager
from agentevolver.paths import P, path_manager


@COMMAND.register_module(force=True)
class RestoreCheckpointCommand(Command):
    name: str = "restore-checkpoint"
    description: str = "Restore every capability to the versions saved in a checkpoint."
    type: CommandType = CommandType.CONTROL
    usage: str = "/restore-checkpoint <label>"
    permission_mode: str = "danger_full_access"

    async def __call__(self, args: List[str], ctx: Optional[CommandContext] = None) -> Response:
        if not args:
            return self.fail(f"usage: {self.usage}")
        label = args[0]

        from agentevolver.config import config
        path = str(path_manager.under(
            config.log_root,
            P.LOG_COMMAND_CHECKPOINT,
            filename=f"{label}.json",
        ))
        if not os.path.exists(path):
            return self.fail(f"Checkpoint '{label}' not found.")

        snapshot = json.load(open(path, encoding="utf-8")).get("snapshot", {})
        restored = 0
        failed: List[str] = []
        for key, version in snapshot.items():
            if not version:
                continue
            ctype, _, name = key.partition("/")
            mgr = get_manager(ctype)
            if mgr is None or not hasattr(mgr, "restore"):
                failed.append(key)
                continue
            try:
                result = await mgr.restore(name, version)
                if result is None:
                    failed.append(f"{key}@{version}")
                else:
                    restored += 1
            except Exception:
                failed.append(key)

        msg = f"Restored {restored} component(s) to checkpoint '{label}'."
        if failed:
            msg += f" Failed ({len(failed)}): {failed}"
        return self.ok(msg, data={"restored": restored, "failed": failed})
