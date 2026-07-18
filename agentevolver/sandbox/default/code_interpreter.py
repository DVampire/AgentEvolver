"""Code-interpreter sandbox.

An OpenSandbox container running ``opensandbox/code-interpreter`` with the
``code_interpreter`` SDK attached, so ``run_code`` executes Python/Bash/JS/...
in a persistent kernel and returns stdout/stderr/rich results.

R (``language="r"``/``"rscript"``) is handled differently: the vendor's
``execd`` binary enforces its Jupyter-kernel language dispatch to a fixed set
(python/bash/javascript/typescript/go/java) server-side, so a 7th kernel
language isn't reachable without forking that binary. Instead R runs via the
generic shell-exec path (``OpenSandbox.run_command``/``write_file``, the same
mechanism ``deploy_tool`` already uses) — write the code to a temp ``.R`` file
and run it with ``Rscript``, one fresh process per call (no cross-call
variable persistence, unlike the kernel-backed languages). This requires the
``docker/code-interpreter`` image (or any image with ``Rscript`` on PATH)
rather than the stock ``opensandbox/code-interpreter`` image.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.default.base import OpenSandbox, execution_to_result
from agentevolver.sandbox.types import ExecResult, SandboxConfig

_SHELL_EXEC_LANGUAGES = {"r", "rscript"}


@SANDBOX.register_module(name="code_interpreter", force=True)
class CodeInterpreterSandbox(OpenSandbox):
    """OpenSandbox + code-interpreter kernel (Python/Bash/JS/TS/Go/Java/R).

    R is the odd one out: it runs via shell-exec (``Rscript``), not the
    persistent Jupyter kernel the other six languages share. See the module
    docstring for why, and ``docker/code-interpreter`` for the image.
    """

    name: str = "code_interpreter"
    description: str = "Sandboxed multi-language code interpreter (persistent kernel; R via Rscript)."
    default_image: str = "agentevolver/code-interpreter:latest"
    default_entrypoint = ["/opt/code-interpreter/code-interpreter.sh"]

    def __init__(self, config: Optional[SandboxConfig] = None, **kwargs: Any):
        super().__init__(config, **kwargs)
        self._interpreter = None

    async def start(self) -> None:
        if self._started:
            return
        await super().start()
        from code_interpreter import CodeInterpreter
        self._interpreter = await CodeInterpreter.create(self._sb)
        logger.info("| 🐍 Code interpreter kernel attached")

    async def destroy(self) -> None:
        self._interpreter = None
        await super().destroy()

    async def run_code(self, code: str, *, language: str = "python") -> ExecResult:
        if (language or "").strip().lower() in _SHELL_EXEC_LANGUAGES:
            return await self._run_r(code)

        if self._interpreter is None:
            raise RuntimeError("CodeInterpreterSandbox not started; call await start() first.")

        lang = self._resolve_language(language)
        try:
            execution = await self._interpreter.codes.run(code, language=lang)
            return execution_to_result(execution)
        except Exception as e:
            return ExecResult(success=False, error=f"code execution failed: {e}")

    async def _run_r(self, code: str) -> ExecResult:
        """Run R code via Rscript (shell-exec, no kernel/state persistence)."""
        script_path = f"/tmp/code_interpreter_r/{uuid.uuid4().hex}.R"
        await self.write_file(script_path, code)
        return await self.run_command(f"Rscript --vanilla {script_path}")

    @staticmethod
    def _resolve_language(language: str):
        from code_interpreter import SupportedLanguage
        key = (language or "python").strip().upper()
        return getattr(SupportedLanguage, key, SupportedLanguage.PYTHON)
