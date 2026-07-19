import os
from mmengine import Config as MMConfig
from argparse import Namespace
from typing import Union

from agentevolver.utils import assemble_resource_path, assemble_workspace_path, project_path, Singleton

def process_general(config: MMConfig) -> MMConfig:
    """Resolve and validate the per-run output directory hierarchy."""
    required_roots = ("project_root", "workspace_root", "log_root")
    missing_roots = [root for root in required_roots if root not in config]
    if missing_roots:
        missing = ", ".join(missing_roots)
        raise ValueError(f"Configuration is missing required output root(s): {missing}")

    # Runtime output is project-owned.  Do not resolve these through the
    # user-level ``~/.agentevolver`` directory.
    project_root = os.path.realpath(project_path(config.project_root))
    workspace_root = os.path.realpath(project_path(config.workspace_root))
    log_root = os.path.realpath(project_path(config.log_root))
    # Extensions are durable project assets, deliberately kept alongside the
    # project's ``output/`` directory rather than inside one run's output tree.
    extension_root = os.path.realpath(project_path(config.get("extension_root", "extension")))

    for root_name, root_path in (("workspace_root", workspace_root), ("log_root", log_root)):
        if os.path.commonpath((project_root, root_path)) != project_root:
            raise ValueError(f"{root_name} must be located under project_root: {root_path}")

    config.project_root = project_root
    config.workspace_root = workspace_root
    config.log_root = log_root
    config.extension_root = extension_root
    # Make the project extension root available to components initialized after
    # configuration, without conflating it with user-level AgentEvolver state.
    os.environ["AGENTEVOLVER_EXTENSION_ROOT"] = extension_root
    # ``workspace_root`` and ``log_root`` are templates until a session is bound.
    # Creating them here would leave empty tag-level directories beside sessions.
    for root_path in (project_root, extension_root):
        os.makedirs(root_path, exist_ok=True)

    log_path = os.path.join(log_root, config.log_path)
    config.log_path = log_path

    return config

def process_tools(config: MMConfig) -> MMConfig:
    for key in config:
        # Skip agent configs (e.g. tool_generate_agent): their key contains "tool"
        # but they are agents — handled by process_agent, not as tools.
        if "tool" in key and not key.endswith("_agent"):
            if "base_dir" in config[key]:
                # Tool state belongs to the run's log root.
                base_dir = str(assemble_workspace_path(os.path.join(config.log_root, config[key]["base_dir"])))
                config[key].update(dict(
                    base_dir = base_dir
                ))
    return config

def process_environments(config: MMConfig) -> MMConfig:
    for key in config:
        # Skip agent configs (e.g. environment_generate_agent): key contains
        # "environment" but they are agents — handled by process_agent.
        if "environment" in key and not key.endswith("_agent"):
            if "base_dir" in config[key]:
                base_dir = str(assemble_workspace_path(os.path.join(config.log_root, config[key]["base_dir"])))
                config[key].update(dict(
                    base_dir = base_dir
                ))
    return config

def process_memory(config: MMConfig)->MMConfig:
    for key in config:
        if "memory" in key:
            if "base_dir" in config[key]:
                base_dir = str(assemble_workspace_path(os.path.join(config.log_root, config[key]["base_dir"])))
                config[key].update(dict(
                    base_dir = base_dir
                ))
            if "model_name" in config[key]:
                model_name = config.model_name
                config[key].update(
                    dict(
                        model_name = model_name
                    )
                )
    return config

def process_agent(config: MMConfig) -> MMConfig:
    for key in config:
        if key != "agent" and not key.endswith("_agent"):
            continue
        entry = config[key]
        if not hasattr(entry, "get"):   # not a config dict (e.g. agent_names list)
            continue
        # An agent's base_dir is its explicit workspace location. Do not join log_root.
        if entry.get("base_dir") is not None:
            config[key].update(dict(
                base_dir = str(assemble_workspace_path(entry["base_dir"]))
            ))
        if entry.get("model_name") is not None:
            config[key].update(dict(
                model_name = config.model_name
            ))
    return config

class Config(MMConfig, metaclass=Singleton):
    def __init__(self):
        super(Config, self).__init__()

    def initialize(self, config_path: Union[str], args: Namespace, verbose: bool = True) -> None:
        # Config files are shipped resources the user may override — resolve them in
        # home → repo → package order so this works both in a checkout and when installed.
        config_path = str(assemble_resource_path(config_path))
        mmconfig = MMConfig.fromfile(filename=config_path)
        if 'cfg_options' not in args or args.cfg_options is None:
            cfg_options = dict()
        else:
            cfg_options = args.cfg_options
        for item in args.__dict__:
            if item not in ['config', 'cfg_options'] and args.__dict__[item] is not None:
                cfg_options[item] = args.__dict__[item]

        mmconfig.merge_from_dict(cfg_options)

        # Process general configuration
        mmconfig = process_general(mmconfig)
        mmconfig = process_tools(mmconfig)
        mmconfig = process_environments(mmconfig)
        mmconfig = process_memory(mmconfig)
        mmconfig = process_agent(mmconfig)
        if verbose:
            print(mmconfig.pretty_text)

        self.__dict__.update(mmconfig.__dict__)

    def dump(self) -> str:
        """Dump the configuration"""
        return super().dump()

config = Config()
# Auto-load the default config at import so `config` is populated for callers that don't
# initialize explicitly. Kept quiet and fault-tolerant: importing the package must not
# crash just because a default config isn't present (an explicit initialize() can follow).
try:
    config.initialize(config_path="configs/base.py", args=Namespace(), verbose=False)
except Exception as _e:  # noqa: BLE001
    import warnings
    warnings.warn(f"agentevolver: default config not loaded at import ({_e}). "
                  f"Call config.initialize(<your_config>) explicitly.", RuntimeWarning)
