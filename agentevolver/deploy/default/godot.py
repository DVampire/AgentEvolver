"""Publish a native Godot source snapshot with browser keyboard/mouse access."""
from agentevolver.registry import DEPLOYER
from agentevolver.deploy.types import Deployer, DeploymentSpec, DeployRequest, HealthCheck


@DEPLOYER.register_module(name="godot", force=True)
class GodotDeployer(Deployer):
    name = "godot"
    description = "Native Godot game streamed to the browser, with isolated preview saves."
    default_image = "agentevolver/godot-play:4.7-v1"
    default_port = 6080
    default_backend = "docker"

    def make_spec(self, request: DeployRequest) -> DeploymentSpec:
        return DeploymentSpec(
            runtime=self.name, image=self.default_image, workspace_root="/app",
            build=["python3 /opt/godot-play/prepare.py"],
            start="exec /opt/godot-play/start.sh", port=request.port or self.default_port,
            health=HealthCheck(type="http", path="/", timeout_s=180),
        )
