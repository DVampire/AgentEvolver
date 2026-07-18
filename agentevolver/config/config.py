import os
from mmengine import Config as MMConfig
from argparse import Namespace
from typing import Union

from agentevolver.utils import assemble_project_path, assemble_resource_path, Singleton

def process_general(config: MMConfig) -> MMConfig:
    """Process general configuration and ensure paths are strings"""
    work_dir = str(assemble_project_path(config.work_dir))
    config.work_dir = work_dir

    if "run_dir" in config:
        run_dir = str(assemble_project_path(config.run_dir))
        config.run_dir = run_dir
    else:
        run_dir = work_dir
        config.run_dir = run_dir  # always expose run_dir (defaults to work_dir) so managers can read it

    if "workspace_dir" in config:
        workspace_dir = str(assemble_project_path(config.workspace_dir))
        config.workspace_dir = workspace_dir

    log_path = os.path.join(run_dir, config.log_path)
    config.log_path = log_path

    return config

def process_tools(config: MMConfig) -> MMConfig:
    for key in config:
        # Skip agent configs (e.g. tool_generate_agent): their key contains "tool"
        # but they are agents — handled by process_agent, not as tools.
        if "tool" in key and not key.endswith("_agent"):
            if "base_dir" in config[key]:
                # base_dir in config is already a relative path from project root
                # (e.g., "defaultdir/tool_calling_agent/browser"), so just assemble it
                base_dir = str(assemble_project_path(os.path.join(config.run_dir, config[key]["base_dir"])))
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
                base_dir = str(assemble_project_path(os.path.join(config.run_dir, config[key]["base_dir"])))
                config[key].update(dict(
                    base_dir = base_dir
                ))
    return config

def process_memory(config: MMConfig)->MMConfig:
    for key in config:
        if "memory" in key:
            if "base_dir" in config[key]:
                base_dir = str(assemble_project_path(os.path.join(config.run_dir, config[key]["base_dir"])))
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
        # An agent's base_dir is relative to the project root (e.g.
        # "work_dir/<tag>/extension"), so assemble it directly. Do NOT join
        # run_dir — that double-prefixed the path (work_dir/.../default/work_dir/...).
        if entry.get("base_dir") is not None:
            config[key].update(dict(
                base_dir = str(assemble_project_path(entry["base_dir"]))
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
