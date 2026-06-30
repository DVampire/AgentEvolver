"""Code-interpreter sandbox.

An OpenSandbox container running ``opensandbox/code-interpreter`` with the
``code_interpreter`` SDK attached, so ``run_code`` executes Python/Bash/JS/...
in a persistent kernel and returns stdout/stderr/rich results.
"""

from __future__ import annotations

from typing import Any, Optional

from src.logger import logger
from src.registry import SANDBOX
from src.sandbox.default.base import OpenSandbox, execution_to_result
from src.sandbox.types import ExecResult, SandboxConfig


@SANDBOX.register_module(name="code_interpreter", force=True)
class CodeInterpreterSandbox(OpenSandbox):
    """OpenSandbox + code-interpreter kernel (Python/Bash/JS/TS/Go/Java)."""

    name: str = "code_interpreter"
    description: str = "Sandboxed multi-language code interpreter (persistent kernel)."
    default_image: str = "opensandbox/code-interpreter:v1.1.0"
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
        if self._interpreter is None:
            raise RuntimeError("CodeInterpreterSandbox not started; call await start() first.")
        from code_interpreter import SupportedLanguage

        lang = self._resolve_language(language)
        try:
            execution = await self._interpreter.codes.run(code, language=lang)
            return execution_to_result(execution)
        except Exception as e:
            return ExecResult(success=False, error=f"code execution failed: {e}")

    @staticmethod
    def _resolve_language(language: str):
        from code_interpreter import SupportedLanguage
        key = (language or "python").strip().upper()
        return getattr(SupportedLanguage, key, SupportedLanguage.PYTHON)
