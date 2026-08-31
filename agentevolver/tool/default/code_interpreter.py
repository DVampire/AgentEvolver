"""Code interpreter tool — runs code in the project's live kernel.

Like every other tool this runs in the base environment: the agent system and
its tools ship together, so there is nothing to route anywhere. Execution goes
to a Jupyter kernel held open per project (see ``agentevolver.kernel``), which
is what makes state persist — a later call sees the variables an earlier one
defined.

It used to start a peer container of its own. That bought no isolation the
agent did not already have (``bash_tool`` runs here too) and cost it the thing
that mattered: the container mounted nothing, so code could not read the files
the agent had just written into the workspace.

Code that calls *tools* rather than computing is a different tool: `batch_call_tool`
(``agentevolver/tool/default/code_mode/``) runs a program whose tool calls go back through
the agent's guarded dispatch. It keeps no state, which is exactly the trade this one makes
in the other direction.

``use_kernel=False`` trades the kernel for one-shot execution: the script is written
out and run as a subprocess here, in the same environment and against the same
filesystem as the agent's shell commands. That matters when the kernel is not looking
at the files the agent is working on — a kernel held open from before the run's
working directory existed answered ``FileNotFoundError`` to the very script that
would have fixed a run's largest defect. The cost is real — no state across calls, no
captured figures — so the kernel stays the default.
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.config import config
from agentevolver.kernel import kernel_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.session import resolve_workspace_root
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Execute code in a persistent interpreter and return everything it produced."

_GUIDANCE = """
State persists across calls: variables, imports and open files from one call are
still there in the next. The interpreter starts in the workspace, so relative
paths mean what they mean to bash and to the files pane — code can read the
files you just wrote.

Figures come back as images rather than as `<Figure ...>`: plot with matplotlib
and the picture is captured alongside the text.
"""

_EXAMPLES = [
    '{"name": "code_interpreter_tool", "args": {"code": "print(2 + 2)", "language": "python"}}',
]
#: Used when ``use_kernel=False``. The persistence and figure-capture promises are
#: dropped rather than softened: an agent that believes a variable survived the
#: previous call writes code that silently reads a stale name.
_ONE_SHOT_GUIDANCE = """
Run a self-contained script and return everything it produced.

Each call is a fresh process. NOTHING carries over between calls — no variables,
no imports, no open files. Write each script so it stands alone: re-read what it
needs from disk and write what it produces back to disk.

Figures are not captured. Save plots to a file and inspect the file.

The script runs in the same filesystem your shell commands see, so relative paths
and the files you just wrote mean the same thing here.
"""

_ONE_SHOT_EXAMPLES = [
    '{"name": "code_interpreter_tool", "args": {"code": "print(2 + 2)", "language": "python"}}',
]
#: How to run a one-shot script per language. bash and python are the ones that
#: matter in practice; the rest are here so an image that ships them works without
#: editing this table.
_ONE_SHOT_RUNNERS = {
    "python": ("py", "python3"), "python3": ("py", "python3"), "py": ("py", "python3"),
    "bash": ("sh", "bash"), "sh": ("sh", "bash"),
    "javascript": ("js", "node"), "js": ("js", "node"),
    "typescript": ("ts", "npx tsx"), "ts": ("ts", "npx tsx"),
    "r": ("R", "Rscript"),
}

#: Language name -> kernelspec. Unlisted names pass through, so a kernel
#: installed in the image is reachable by its own name without editing this.
_KERNELS = {
    "python": "python3", "python3": "python3", "py": "python3",
    "bash": "bash", "sh": "bash",
    "javascript": "jslab", "js": "jslab",
    "typescript": "tslab", "ts": "tslab",
    "r": "ir",
}


def kernel_for(language: str) -> Optional[str]:
    return _KERNELS.get((language or "python").strip().lower(), language)


@TOOL.register_module(force=True)
class CodeInterpreterTool(Tool):
    """Execute code in the project's persistent, multi-language interpreter."""

    name: str = "code_interpreter_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="danger_full_access", description="Runs code in the project's kernel.")
    use_kernel: bool = Field(
        default=True,
        description=(
            "Execute in the project's persistent Jupyter kernel (state carries across "
            "calls, figures are captured). Set False for one-shot subprocess execution, "
            "which sees the filesystem as it is now rather than as it was when the "
            "kernel started."
        ),
    )

    def __init__(self, enable_evolving: bool = False, use_kernel: bool = True, **kwargs):
        super().__init__(enable_evolving=enable_evolving, use_kernel=use_kernel, **kwargs)
        # The guidance is what the agent plans against, so it has to describe the
        # mode actually in force — promising persistence that is not there produces
        # scripts that read variables no longer in scope.
        if not use_kernel:
            self.guidance = _ONE_SHOT_GUIDANCE
            self.examples = list(_ONE_SHOT_EXAMPLES)

    async def _run_one_shot(self, code: str, language: str, ctx: Any) -> Response:
        """Write the script out and run it once as a subprocess.

        A non-zero exit is reported as an observation carrying the exit code, matching
        bash_tool: a script that ran and failed has told the agent something, and calling
        that a tool malfunction hides the output it needs.
        """
        entry = _ONE_SHOT_RUNNERS.get((language or "python").strip().lower())
        if entry is None:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"Unsupported language {language!r} without a kernel. "
                         f"Available: {', '.join(sorted(_ONE_SHOT_RUNNERS))}."),
            )
        suffix, runner = entry

        try:
            import asyncio
            import os
            import shlex
            import tempfile

            directory = os.path.abspath(
                resolve_workspace_root(ctx, tempfile.gettempdir())
            )
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f".agentevolver_snippet.{suffix}")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(code)
            process = await asyncio.create_subprocess_shell(
                f"cd {shlex.quote(directory)} && {runner} {shlex.quote(path)} 2>&1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
            output = stdout.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode
            logger.info(f"| ✅ code_interpreter ran one-shot {language} locally")
            message = output if output else f"Command completed with exit code: {exit_code}"
            if exit_code:
                message = f"{message}\n\nExit code: {exit_code}"
            return Response(
                type=ResponseType.TOOL, success=True, message=message,
                data={"exit_code": exit_code, "language": language, "use_kernel": False},
            )
        except Exception as e:  # noqa: BLE001
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Error running one-shot {language}: {e}")

    async def __call__(self, code: str, language: str = "python", **kwargs) -> Response:
        """Execute ``code`` — in this project's kernel, or as a one-shot script.

        Args:
            code: The code to execute.
            language: One of python (default), bash, javascript, typescript, r.
        """
        ctx = kwargs.get("ctx")
        if not self.use_kernel:
            return await self._run_one_shot(code, language, ctx)
        # Keyed by project, not by ctx.id: ctx.id is the *state* scope — one per
        # conversation, one per workflow run — so keying the interpreter off it
        # would hand every new line of dialogue a blank one. The kernel is a
        # resource, shared like the project's files.
        extra = getattr(ctx, "extra", None) or {}
        key = extra.get("project_id") or getattr(ctx, "id", None) or "default"

        result = await kernel_manager.execute(
            code, key=key, kernel_name=kernel_for(language), language=language,
            # The project's workspace, so the kernel starts where bash starts and
            # relative paths mean the same thing to both. Read from the config rather
            # than from `ctx`: neither context class declares `workspace_root` and
            # nothing assigns it, so `getattr(ctx, ...)` was always None and the kernel
            # fell through to this same value.
            workspace=resolve_workspace_root(ctx), origin="agent")
        logger.info(f"| {'✅' if result.success else '⚠️'} code_interpreter ran {language} code")
        return Response(
            type=ResponseType.TOOL,
            success=result.success,
            message=result.as_message(),
            data={
                "execution_count": result.execution_count,
                "error": result.error,
                # Every output in order with its MIME bundle intact, so a
                # notebook view can render the figures the message can only name.
                "outputs": [output.model_dump(mode="json") for output in result.outputs],
            },
        )
