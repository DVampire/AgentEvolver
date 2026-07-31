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

``use_kernel=False`` trades the kernel for one-shot execution and, with a peer
sandbox bound, runs the code *inside that container*. That is the only mode that
works when the agent's files live somewhere the kernel cannot reach: on
ProgramBench the task fixture is in a peer cleanroom, and a kernel started in the
base container answered ``FileNotFoundError: /workspace/cmatrix.c`` to the very
script that would have fixed the run's largest defect. The cost is real —
no state across calls, no captured figures — so the kernel stays the default.
"""

from typing import Any, Dict, Optional

from pydantic import Field

from agentevolver.config import config
from agentevolver.kernel import kernel_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Execute code in a persistent interpreter and return everything it produced."

_INSTRUCTION = """
## Function
Execute code in a persistent interpreter and return everything it produced.

## Guidance
State persists across calls: variables, imports and open files from one call are
still there in the next. The interpreter starts in the workspace, so relative
paths mean what they mean to bash and to the files pane — code can read the
files you just wrote.

Figures come back as images rather than as `<Figure ...>`: plot with matplotlib
and the picture is captured alongside the text.

## Parameters
- code (str): The code to execute.
- language (str, optional): One of python (default), bash, javascript, typescript, r.

## Example
{"name": "code_interpreter_tool", "args": {"code": "print(2 + 2)", "language": "python"}}
"""

#: Used when ``use_kernel=False``. The persistence and figure-capture promises are
#: dropped rather than softened: an agent that believes a variable survived the
#: previous call writes code that silently reads a stale name.
_INSTRUCTION_ONE_SHOT = """
## Function
Run a self-contained script and return everything it produced.

## Guidance
Each call is a fresh process. NOTHING carries over between calls — no variables,
no imports, no open files. Write each script so it stands alone: re-read what it
needs from disk and write what it produces back to disk.

Figures are not captured. Save plots to a file and inspect the file.

The script runs in the same filesystem your shell commands see, so relative paths
and the files you just wrote mean the same thing here.

## Parameters
- code (str): The script to run.
- language (str, optional): One of python (default), bash, javascript, typescript, r.

## Example
{"name": "code_interpreter_tool", "args": {"code": "print(2 + 2)", "language": "python"}}
"""

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
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="danger_full_access", description="Runs code in the project's kernel.")
    use_kernel: bool = Field(
        default=True,
        description=(
            "Execute in the project's persistent Jupyter kernel (state carries across "
            "calls, figures are captured). Set False for one-shot execution, which is "
            "what a peer-sandboxed run needs: the kernel starts in the base "
            "environment and cannot see a peer container's files."
        ),
    )

    def __init__(self, enable_evolving: bool = False, use_kernel: bool = True, **kwargs):
        super().__init__(enable_evolving=enable_evolving, use_kernel=use_kernel, **kwargs)
        # The instruction is what the agent plans against, so it has to describe the
        # mode actually in force — promising persistence that is not there produces
        # scripts that read variables no longer in scope.
        if not use_kernel:
            self.instruction = _INSTRUCTION_ONE_SHOT

    async def _run_one_shot(self, code: str, language: str, ctx: Any) -> Response:
        """Write the script out and run it once, in the sandbox when one is bound.

        Goes through the peer sandbox so the script sees the same filesystem the
        agent's shell commands do. A non-zero exit is reported as an observation with
        the exit code, matching bash_tool: a script that runs and fails has told the
        agent something, and calling that a tool malfunction hides the output it needs.
        """
        import shlex

        entry = _ONE_SHOT_RUNNERS.get((language or "python").strip().lower())
        if entry is None:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"Unsupported language {language!r} without a kernel. "
                         f"Available: {', '.join(sorted(_ONE_SHOT_RUNNERS))}."),
            )
        suffix, runner = entry
        sandbox = (getattr(ctx, "extra", None) or {}).get("sandbox")
        workspace = getattr(sandbox, "container_workspace", None) if sandbox else None
        directory = workspace or "/tmp"
        path = f"{directory}/.agentevolver_snippet.{suffix}"

        try:
            if sandbox is not None:
                await sandbox.write_file(path, code)
                result = await sandbox.run_command(
                    f"cd {shlex.quote(directory)} && {runner} {shlex.quote(path)} 2>&1"
                )
                ran = result.exit_code is not None
                logger.info(f"| {'✅' if ran else '⚠️'} code_interpreter ran one-shot {language} in sandbox")
                return Response(
                    type=ResponseType.TOOL,
                    success=result.success or ran,
                    message=result.as_message(),
                    data={"exit_code": result.exit_code, "language": language,
                          "sandboxed": True, "use_kernel": False},
                )

            # No peer bound: run here, which under Model X already IS the container.
            import asyncio
            import os
            import tempfile

            directory = os.path.abspath(config.workspace_root or tempfile.gettempdir())
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
            message = output or f"Command completed with exit code: {exit_code}"
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
        """Execute ``code`` — in this project's kernel, or as a one-shot script."""
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
            # relative paths mean the same thing to both.
            workspace=getattr(ctx, "workspace_root", None), origin="agent")
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
