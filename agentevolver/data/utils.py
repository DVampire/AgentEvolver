"""File presence and Hub transport shared by dataset classes; no parsing or dispatch."""

import os
from typing import Optional

from agentevolver.logger import logger
from agentevolver.utils import assemble_workspace_path


_NOT_DATA = {
    ".cache", ".huggingface", ".gitattributes", ".gitignore",
    "readme.md", "license", "license.md", "license.txt",
    "croissant.json", "eval.yaml",
}


def has_dataset_files(path: str) -> bool:
    """Check for nonempty data files, excluding cards and download metadata.

    Presence is an offline check, not proof of snapshot completeness or validity.
    Use the dataset class's ``inspect()`` to check parsing and instance counts.
    """
    path = assemble_workspace_path(path)

    def is_data(filename: str) -> bool:
        name = os.path.basename(filename)
        return (name.lower() not in _NOT_DATA and not name.startswith(".")
                and os.path.isfile(filename) and os.path.getsize(filename) > 0)

    if os.path.isfile(path):
        return is_data(path)
    for current, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d.lower() not in _NOT_DATA and not d.startswith(".")]
        if any(is_data(os.path.join(current, name)) for name in files):
            return True
    return False


def download_snapshot(path: str, hf_repo_id: str, *, revision: Optional[str] = None,
                      repair: bool = False) -> str:
    """Return a local snapshot, downloading only when absent or repair is requested.

    The dataset class supplies the path and source. Ordinary reads of populated
    snapshots stay offline. ``repair=True`` asks the Hub to
    reconcile an existing directory, filling missing files without deleting local
    files. Hub cache metadata determines which files need transferring again.
    A supplied revision applies to downloads, not to an existing offline snapshot.
    """
    local_dir = assemble_workspace_path(path)
    if not repair and has_dataset_files(local_dir):
        return local_dir
    if not hf_repo_id:
        raise FileNotFoundError(
            f"Dataset cannot be {'repaired' if repair else 'downloaded'} at "
            f"{local_dir}: no `hf_repo_id` configured."
        )
    if os.path.isfile(local_dir):
        raise ValueError(f"Snapshot download requires a directory, not a file: {local_dir}")

    from dotenv import load_dotenv
    from huggingface_hub import snapshot_download

    load_dotenv(assemble_workspace_path(".env"))
    logger.info(f"| 📥 Fetching dataset '{hf_repo_id}' into {local_dir}")
    os.makedirs(local_dir, exist_ok=True)
    snapshot_download(
        repo_id=hf_repo_id, repo_type="dataset", revision=revision,
        local_dir=local_dir, token=os.environ.get("HF_TOKEN") or None,
    )
    if not has_dataset_files(local_dir):
        raise FileNotFoundError(f"Download of '{hf_repo_id}' left no dataset files at {local_dir}.")
    logger.info(f"| ✅ Dataset available at {local_dir}")
    return local_dir
