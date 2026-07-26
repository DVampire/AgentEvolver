"""Cleanlab Remediator — from the Langflow `cleanlab` bundle (ported)."""

from typing import Any, Optional

from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response
from agentevolver.plugins.types import BundlePlugin


@PLUGIN.register_module(force=True)
class CleanlabCleanlabRemediatorPlugin(BundlePlugin):
    name: str = "cleanlab.cleanlab_remediator"
    display_name: str = 'Cleanlab Remediator'
    description: str = ''
    kind: str = "tool"
    bundle: str = "cleanlab"
    bundle_label: str = 'Cleanlab'
    category: str = "evaluation"
    source: str = "langflow/bundles/cleanlab"
    status: str = "complete"

    async def __call__(self, response: str = "", score: float = 0.0, threshold: float = 0.7, fallback: str = "I am not sure.", **kwargs) -> Response:
        if not response:
            return self._fail("cleanlab.remediator: 'response' is required.")
        remediated = response if float(score) >= float(threshold) else fallback
        return self._ok("Remediation applied." if remediated == fallback else "Response passed threshold.",
                        response=remediated, remediated=remediated == fallback, score=score)
