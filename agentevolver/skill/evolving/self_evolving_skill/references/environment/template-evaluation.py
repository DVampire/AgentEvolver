"""TEMPLATE — independent artifact-to-artifact evaluation.

Copy as environment.py and pair with an adapted ENVIRONMENT.md. Replace the small
calculation with one trial. Manager supplies call isolation, thread offload, path
claims and bounded admission; this class has no scheduler or persistent run state.
The JSON example accepts {"values": [1, 2, 3]} and publishes a versioned sum.
"""
import hashlib
import json
import math
import tempfile
from pathlib import Path

from pydantic import Field

from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.registry import ENVIRONMENT


@ENVIRONMENT.register_module(force=True)
class MyEvaluationEnvironment(Environment):
    name: str = "my_evaluation_environment"
    description: str = "Evaluate independent pinned JSON inputs into immutable results."
    metadata: dict = Field(default={})
    enable_evolving: bool = True
    state_scope: str = "call"
    max_concurrency: int = Field(default=2, ge=1)
    concurrency_group: str = "example-evaluation"  # cooperating engines use the same limit

    async def get_state(self, ctx=None, **kwargs):
        return {"success": True, "state": {
            "available_actions": ["evaluate"], "state_scope": self.state_scope,
            "prerequisites": "Save input JSON; choose a distinct result path. Results persist on disk."}}

    @environment_manager.action(
        name="evaluate", description="Compute one trial; input JSON has a nonempty numeric values list.",
        read_only=False, destructive=False, idempotent=True, open_world=False,
        read_paths=("input_path",), write_paths=("result_path",),
        permission_op="write", permission_target="result_path",
    )
    def evaluate(self, input_path: str, result_path: str, ctx=None):
        # Native arguments are normalized before permission checks and file claims.
        source, destination = Path(input_path), Path(result_path)
        temporary = None
        try:
            if source == destination or source in destination.parents or destination in source.parents:
                raise ValueError("Input and result paths must not overlap")
            raw = source.read_bytes()
            value = json.loads(raw)
            numbers = value.get("values") if isinstance(value, dict) else None
            if not isinstance(numbers, list) or not numbers or any(
                type(n) not in (int, float) or not math.isfinite(n) for n in numbers
            ):
                raise ValueError("Input requires a nonempty list of finite numbers: values")
            result = {"schema": 1, "method": "sum-v1",  # change identity with numerical behavior
                      "input_sha256": hashlib.sha256(raw).hexdigest(), "value": math.fsum(numbers)}
            encoded = json.dumps(result, sort_keys=True, allow_nan=False)
            cached = destination.exists()
            if cached:
                if destination.read_text() != encoded:
                    raise ValueError("Existing result differs; preserve it and choose a new version path")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(encoded)
                temporary.replace(destination)
            return {"success": True, "message": f"Result saved: {destination}",
                    "data": {"result_path": str(destination), "cached": cached, **result}}
        except (OSError, ValueError, TypeError, OverflowError) as error:
            return {"success": False, "message": f"Evaluation failed: {error}",
                    "data": {"code": type(error).__name__, "result_path": str(destination)}}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
