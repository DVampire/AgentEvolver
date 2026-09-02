"""Run AgentEvolver on SWE-bench Pro — the same launcher shape as run_programbench.py.

A launcher on the host starts one task container per instance and runs the MetaAgent
inside it; the agent edits the repository, and whatever it changes is collected as a git
patch and scored by the official ``swe_bench_pro_eval.py`` grader. Two arms
(configs/swebench_pro_agent.py and .._baseline.py) differ only in the evolution roster.

Key differences from ProgramBench, all deliberate:

- **The repo lives at ``/app``** in the instance image (``WORKDIR /app; git clone; git
  checkout <base_commit>``). The session workspace is seeded from that ``/app`` on the
  host, then mounted into the agent's container at ``/workspace`` — the framework's
  own workspace root, where the bash tool defaults its cwd and the eval tool checkpoints
  its git state, so reusing it avoids fighting ``config.workspace_root``. The agent works
  at ``/workspace`` and the patch is ``git diff`` there against the base commit. Only the
  grader's throwaway peer sandbox uses the image-native ``/app`` (see ``_build_entryscript``).

- **GT-SAFETY by field filtering.** The HuggingFace row carries the answer key —
  ``patch`` (gold fix), ``test_patch``, ``fail_to_pass``, ``pass_to_pass``,
  ``selected_test_files_to_run``. Only ``problem_statement`` / ``requirements`` /
  ``interface`` (plus repo/base_commit for setup) are ever handed to the container;
  ``_safe_instance`` strips the rest. The oracle stays on the host, where the grader runs.

- **The grader runs in a PEER SANDBOX, counts-only.** ``swebench_pro_eval_tool`` bridges
  out to ``_grade_once`` on the host, which spins a THROWAWAY container from the same
  instance image (via the framework's own ``sandbox_manager`` — the machinery that runs the
  agent's sandbox, not Scale AI's Beta ``--use_local_docker`` path), applies the agent's
  patch, injects the resolution tests THERE (never in the agent's sandbox), runs the hidden
  suite, and returns only how many ``fail_to_pass`` now pass and how many ``pass_to_pass``
  still pass — never a test name, body, or expected output.

- **Grader assets** — the per-instance ``run_script.sh`` / ``parser.py`` and the dockerfiles
  (for their ENV) live in the cloned checkout (``--grader-repo``, default
  ``others/SWE-bench_Pro``); the entryscript mirrors the official ``create_entryscript``.

Modes: ``launcher`` (default, host) selects instances and runs a container each; ``inner``
(``--instance-json``) is the actual MAS run inside that container.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import sys
import time

# Reuse the proven, benchmark-agnostic host machinery from the ProgramBench launcher rather
# than duplicate it. Importing is safe: its main() is guarded by __main__, and module-level
# code only computes paths/constants.
from examples.run_programbench import (  # noqa: E402
    CONTAINER_EVAL_BRIDGE,
    CONTAINER_USER,
    CONTAINER_WORKSPACE,
    _LANDING_MARGIN_SECONDS,
    _summarise_spend,
    bind_task_workspace,
    check_shared_roots_readable,
    grant_to_container_user,
    host_repo_root,
    interpreter_prefix,
    restore_ownership,
)

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager

root = host_repo_root()

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
#: HuggingFace dataset (public 731-instance split), the Docker Hub user that hosts the
#: instance images, and the derived image prefix. The grader recomputes the image URI from
#: the instance_id + this username (helper_code/image_uri.py), so it must match.
HF_DATASET = "ScaleAI/SWE-bench_Pro"
DOCKERHUB_USER = "jefzda"
IMAGE_PREFIX = f"{DOCKERHUB_USER}/sweap-images"

#: The repository is cloned into /app inside every instance image. That is the path the
#: grader's peer sandbox uses (image-native); the agent instead gets the seeded repo
#: mounted at CONTAINER_WORKSPACE (=/workspace), the framework's own workspace root.
CONTAINER_APP = "/app"
CONTAINER_REPO = "/AgentEvolver"

DEFAULT_TASK_FILE = os.path.join(root, "examples", "tasks", "swebench_pro_resolution.html")
DEFAULT_GRADER_REPO = os.path.join(root, "others", "SWE-bench_Pro")

#: Per-run grader-eval budget (see swebench_pro_eval.py) and how long one grade may take.
_EVAL_BUDGET = 6
_GRADE_TIMEOUT_SECONDS = 1800

#: The ONLY instance fields that may enter the container. Everything else in the row is the
#: answer key or host-side scoring metadata and is withheld. Order fixes the task-doc slots.
_SAFE_FIELDS = ("instance_id", "repo", "repo_language", "base_commit",
                "problem_statement", "requirements", "interface")

_FORWARDED_ENV = (
    "OPENAI_API_KEY", "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE",
    "GOOGLE_API_KEY", "GOOGLE_API_BASE",
    "OPENROUTER_API_KEY", "OPENROUTER_API_BASE",
    "LLM_HUB_API_KEY", "LLM_HUB_API_BASE",
)


# --------------------------------------------------------------------------- #
# Dataset + GT-safe field filtering
# --------------------------------------------------------------------------- #
def load_instances() -> list:
    """Load the SWE-bench Pro public split as a list of dict rows (full, incl. oracle)."""
    from datasets import load_dataset
    return list(load_dataset(HF_DATASET, split="test"))


def _safe_instance(row: dict) -> dict:
    """The subset of a row the container may see — never the gold patch or the tests."""
    return {k: row.get(k, "") for k in _SAFE_FIELDS}


def image_ref(row: dict) -> str:
    return f"{IMAGE_PREFIX}:{row['dockerhub_tag']}"


def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset by id list or index range. task_ids wins over start/end."""
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")
    if task_ids:
        by_id = {i["instance_id"]: i for i in instances}
        unknown = [t for t in task_ids if t not in by_id]
        if unknown:
            logger.warning(f"| ⚠️ Unknown instance_ids ignored: {unknown}")
        return [by_id[t] for t in task_ids if t in by_id]
    return list(instances[start:end])


# --------------------------------------------------------------------------- #
# Task document
# --------------------------------------------------------------------------- #
def build_task_content(instance_full: dict, task_file=None, task_log_root=None):
    """Resolve the task document, substituting the SAFE fields only.

    ``instance_full`` may be the full row; only ``_SAFE_FIELDS`` are read from it, so the
    oracle can never reach the document by accident.
    """
    from agentevolver.task import load_task_document
    from agentevolver.visual import render_task_page

    safe = _safe_instance(instance_full)
    path = task_file or DEFAULT_TASK_FILE
    doc = load_task_document(path)
    slots = {
        "{repository}": str(safe.get("repo", "")),
        "{language}": str(safe.get("repo_language", "")),
        "{problem_statement}": str(safe.get("problem_statement", "")),
        "{requirements}": str(safe.get("requirements", "") or "(none given)"),
        "{interface}": str(safe.get("interface", "") or "(none given)"),
    }

    content = doc.content
    for key, value in slots.items():
        if key not in content:
            logger.warning(f"| ⚠️ Task document has no {key} placeholder: {path}")
        content = content.replace(key, value)

    metadata = {"task_doc": doc.source_path, "task_kind": doc.type}
    if task_log_root:
        html_body = doc.html_body
        for key, value in slots.items():
            html_body = html_body.replace(key, value)
        view_path = str(path_manager.under(
            task_log_root,
            P.LOG_TASK_VIEW,
            filename=f"task_view_{safe.get('instance_id', 'task')}.html",
        ))
        os.makedirs(task_log_root, exist_ok=True)
        render_task_page(html_body, view_path, title=doc.title)
        metadata["task_view"] = view_path

    return content, [doc.source_path], metadata


# --------------------------------------------------------------------------- #
# Patch collection + grading (host side — has the oracle)
# --------------------------------------------------------------------------- #
def collect_patch(workspace: str, base_commit: str) -> str:
    """The agent's change as a git patch: everything the tree differs from base_commit.

    Both committed and uncommitted edits are captured — the agent is told to commit, but a
    forgotten commit must not lose the work. Returns '' when the tree is unchanged. This is
    the raw agent patch, exactly like an SWE-agent ``.pred``; binary noise from the image's
    baked-in git quirks is dropped later by ``strip_binary_hunks`` at grade time, matching the
    official harness.
    """
    git_dir = os.path.join(workspace, ".git")

    def _git(*args, env=None):
        # The seeded tree belongs to the sandbox uid, which need not match the host
        # watcher uid.  Explicit git/work-tree paths avoid Git's ownership discovery
        # check without weakening the user's global safe.directory configuration.
        return subprocess.run(
            ["git", f"--git-dir={git_dir}", f"--work-tree={workspace}", *args],
            capture_output=True, text=True, timeout=120, env=env,
        )

    # Use a disposable index: ``add -N`` makes untracked files visible to diff, but the
    # host watcher must never write the sandbox-owned repository index.
    with tempfile.TemporaryDirectory(prefix="agentevolver-patch-index-") as tmp_dir:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = os.path.join(tmp_dir, "index")
        object_dir = os.path.join(tmp_dir, "objects")
        os.makedirs(object_dir)
        env["GIT_OBJECT_DIRECTORY"] = object_dir
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = os.path.join(git_dir, "objects")
        initialized = _git("read-tree", "HEAD", env=env)
        if initialized.returncode != 0:
            raise RuntimeError(
                f"git read-tree failed while collecting patch: {initialized.stderr.strip()}"
            )
        staged = _git("add", "-A", "-N", env=env)
        if staged.returncode != 0:
            raise RuntimeError(f"git add -N failed while collecting patch: {staged.stderr.strip()}")
        diff = _git("diff", "--binary", base_commit, env=env)
        if diff.returncode != 0:
            raise RuntimeError(f"git diff failed while collecting patch: {diff.stderr.strip()}")
        return diff.stdout


def strip_binary_hunks(patch: str) -> str:
    """Drop binary diff sections from a git patch — verbatim from the official harness.

    swe_bench_pro_eval.py:assemble_workspace_files runs this before ``git apply``, so the
    benchmark grades text changes only. We do the same so a binary hunk the agent's ``git
    diff`` picked up (notably these images' baked-in "deleted but present" test fixtures)
    cannot fail the whole apply and zero out an otherwise-correct patch.
    """
    if not patch:
        return patch
    sections = re.split(r'(?=^diff --git )', patch, flags=re.MULTILINE)
    kept: list = []
    for section in sections:
        if not section.strip():
            continue
        if re.search(r'^Binary files .* differ$', section, re.MULTILINE):
            continue
        if re.search(r'^GIT binary patch$', section, re.MULTILINE):
            continue
        kept.append(section)
    return "".join(kept)


def _as_list(row: dict, field: str) -> list:
    v = row.get(field, "")
    try:
        if not isinstance(v, str):
            return list(v or [])
        if not v.strip():
            return []
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            # SWE-bench Pro follows the official evaluator's Python-literal format for
            # a few rows whose test names are not valid JSON strings.
            parsed = ast.literal_eval(v)
        return list(parsed or [])
    except Exception:  # noqa: BLE001
        return []


def _load_script(grader_repo: str, instance_id: str, name: str) -> str:
    with open(os.path.join(grader_repo, "run_scripts", instance_id, name), encoding="utf-8") as h:
        return h.read()


def _build_entryscript(row: dict, grader_repo: str) -> str:
    """Mirror swe_bench_pro_eval.create_entryscript: ENV from the dockerfiles, then reset to
    base, apply the agent's patch, run before_repo_set_cmd (which checks out the resolution
    test files from the graded commit), run the selected tests, and parse to output.json.

    The test files are injected here, INSIDE this throwaway grading sandbox — never in the
    agent's sandbox — so the oracle stays out of the agent's reach.
    """
    iid = row["instance_id"]
    env_cmds = []
    for kind in ("base_dockerfile", "instance_dockerfile"):
        p = os.path.join(grader_repo, "dockerfiles", kind, iid, "Dockerfile")
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line.startswith("ENV"):
                    env_cmds.append(line.replace("ENV", "export", 1))
    before = row.get("before_repo_set_cmd", "").strip().split("\n")[-1]
    test_files = " ".join(_as_list(row, "selected_test_files_to_run"))
    base = row.get("base_commit", "")
    return "\n".join(env_cmds) + f"""
set +e
cd /app
git config --global --add safe.directory /app
git reset --hard {base}
git checkout {base}
git apply -v /workspace/patch.diff
{before}
bash /workspace/run_script.sh {test_files} > /workspace/stdout.log 2> /workspace/stderr.log
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""


def _counts(results, fail_to_pass: list, pass_to_pass: list) -> dict:
    """Per-test {name,status} → COUNTS ONLY (no names cross back to the agent)."""
    status = {}
    for t in (results if isinstance(results, list) else results.get("tests", [])):
        n = t.get("name")
        if n:
            status[n] = str(t.get("status", "")).upper()
    f2p = sum(1 for n in fail_to_pass if status.get(n) == "PASSED")
    p2p = sum(1 for n in pass_to_pass if status.get(n) == "PASSED")
    return {
        "fail_to_pass_resolved": f2p, "fail_to_pass_total": len(fail_to_pass),
        "pass_to_pass_preserved": p2p, "pass_to_pass_total": len(pass_to_pass),
        "resolved": (f2p == len(fail_to_pass) and p2p == len(pass_to_pass) and len(fail_to_pass) > 0),
    }


async def _grade_once(workspace_dir: str, row: dict, grader_repo: str) -> dict:
    """Score the current patch in a PEER SANDBOX (the framework's own machinery, like the
    agent's own container) — not Scale AI's Beta ``--use_local_docker`` path. Returns COUNTS
    ONLY; the oracle (which tests, the resolution criteria) never leaves the host.
    """
    from agentevolver.sandbox import sandbox_manager

    instance_id = row["instance_id"]
    try:
        patch = strip_binary_hunks(collect_patch(workspace_dir, row.get("base_commit", "")))
    except Exception as e:  # noqa: BLE001
        return {"error_code": "patch_collection_failed", "error_details": str(e)}
    if not patch.strip():
        return {"error_code": "empty_patch", "error_details": "the working tree is unchanged from base"}
    try:
        run_script = _load_script(grader_repo, instance_id, "run_script.sh")
        parser_py = _load_script(grader_repo, instance_id, "parser.py")
        entryscript = _build_entryscript(row, grader_repo)
    except Exception as e:  # noqa: BLE001
        return {"error_code": "scripts_missing", "error_details": str(e)}

    reuse = f"eval-{instance_id}"
    sbx = None
    try:
        # A separate, throwaway container from the SAME image the agent used. Root + FULLY
        # OPEN network (empty allow/deny) so the test setup (npm/pip/go install, redis) can
        # run; a fully-open policy needs no egress relay/forwarder (and thus no framework
        # mount). It is a grader, not the agent, so network here does not weaken the agent's
        # anti-cheat isolation — the agent's own sandbox stays network=False.
        sbx = await sandbox_manager.acquire(
            "docker", reuse_key=reuse, image=image_ref(row), network=True,
            allow_hosts=[], deny_hosts=[], user="0:0", workdir="/app")
        for path, data in (("/workspace/patch.diff", patch),
                           ("/workspace/run_script.sh", run_script),
                           ("/workspace/parser.py", parser_py),
                           ("/workspace/entryscript.sh", entryscript)):
            await sbx.write_file(path, data)
        await sbx.run_command("bash /workspace/entryscript.sh",
                              timeout=_GRADE_TIMEOUT_SECONDS)
        try:
            output = await sbx.read_file("/workspace/output.json")
            results = json.loads(output)
        except Exception as e:  # noqa: BLE001
            return {"error_code": "no_output_json",
                    "error_details": f"the test run produced no output.json ({e})"}
        return _counts(results, _as_list(row, "fail_to_pass"), _as_list(row, "pass_to_pass"))
    except Exception as e:  # noqa: BLE001
        return {"error_code": "grade_failed", "error_details": str(e)}
    finally:
        if sbx is not None:
            with contextlib.suppress(Exception):
                await sandbox_manager.release("docker", reuse_key=reuse)


async def eval_bridge_watcher(bridge_dir: str, workspace_dir: str, row: dict,
                              grader_repo: str, budget: int, counter: dict) -> None:
    """Serve swebench_pro_eval_tool requests from inside the sandbox, host-side.

    One grade per request (capped at budget); writes back response-<n>.json carrying only
    the counts. Cancelled when the inner run finishes.
    """
    os.makedirs(bridge_dir, exist_ok=True)
    served: set = set()
    instance_id = row["instance_id"]
    try:
        while True:
            for name in sorted(os.listdir(bridge_dir)):
                if not (name.startswith("request-") and name.endswith(".json")):
                    continue
                seq = name[len("request-"):-len(".json")]
                response_path = os.path.join(bridge_dir, f"response-{seq}.json")
                if seq in served or os.path.isfile(response_path):
                    continue
                served.add(seq)
                try:
                    seq_n = int(seq)
                except ValueError:
                    continue
                if seq_n >= budget:
                    report = {"error_code": "budget_exhausted",
                              "error_details": f"{seq_n} requested, budget {budget}"}
                else:
                    logger.info(f"| 🧪 [{instance_id}] grading eval #{seq_n} for the agent…")
                    report = await _grade_once(workspace_dir, row, grader_repo)
                    counter["count"] = counter.get("count", 0) + 1
                    logger.info(
                        f"| 🧪 [{instance_id}] eval #{seq_n}: "
                        f"f2p={report.get('fail_to_pass_resolved')}/{report.get('fail_to_pass_total')} "
                        f"error={report.get('error_code')}")
                tmp = response_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(report, handle)
                os.chmod(tmp, 0o644)  # the container runs as another uid and must read it
                os.replace(tmp, response_path)
            await asyncio.sleep(2.0)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.error(f"| ❌ [{instance_id}] eval bridge watcher stopped: {e}")


# --------------------------------------------------------------------------- #
# Per-instance disk reclaim (for large runs on a full volume)
# --------------------------------------------------------------------------- #
def _reclaim_instance_disk(workspace_dir, image_ref_str) -> None:
    """Free one graded instance's disk: its seeded workspace and its (unique) image.

    Runs in a worker thread. The workspace is container-uid-owned, so its contents are
    removed through a throwaway root container before the (host-owned) top dir is dropped.
    The image's dockerhub_tag is unique to the instance, so ``docker rmi`` frees it without
    affecting any other in-flight instance. Best-effort — never raises.
    """
    if workspace_dir and os.path.isdir(workspace_dir):
        with contextlib.suppress(Exception):
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{workspace_dir}:/w", "alpine", "sh", "-c",
                 "rm -rf /w/..?* /w/.[!.]* /w/* 2>/dev/null; true"],
                capture_output=True, timeout=300,
            )
        with contextlib.suppress(Exception):
            shutil.rmtree(workspace_dir, ignore_errors=True)
    if image_ref_str:
        with contextlib.suppress(Exception):
            subprocess.run(["docker", "rmi", "-f", image_ref_str], capture_output=True, timeout=180)


# --------------------------------------------------------------------------- #
# Workspace seeding (from /app in the image)
# --------------------------------------------------------------------------- #
def seed_workspace(image_ref_str: str, base_commit: str, destination: str) -> None:
    """Copy the image's /app into destination at base_commit, the way the grader starts.

    Done by a throwaway root container so modes/ownership copy exactly, then handed to the
    container uid so the agent can edit it. A pre-existing dir is emptied first (root-owned).

    The setup mirrors the official grader's tree — ``git reset --hard`` then ``git checkout``
    at base_commit, and *no* ``git clean``. It additionally removes every ref except a fresh
    benchmark-base branch and prunes unreachable objects. Some published instance images
    retain later upstream commits, including the gold fix; leaving those objects addressable
    lets an agent recover answer metadata or the complete patch with ``git show``. Keeping the
    base commit and its ancestors preserves normal repository history without exposing future
    commits. Remotes are removed as a second line of defence behind the network policy.

    ``git clean -fdx`` is deliberately avoided: these images carry a baked-in index quirk
    where a tracked, present file (e.g. ``test/files/toobig.jpg``) reads as deleted, and clean
    would wrongly remove it. Any binary noise that still lands in the diff is dropped by
    ``strip_binary_hunks`` at grade time, again matching the official harness
    (swe_bench_pro_eval.py: assemble_workspace_files).
    """
    os.makedirs(destination, exist_ok=True)
    if not base_commit:
        raise ValueError("base_commit is required to seed a GT-safe SWE-bench Pro workspace")
    quoted_base = shlex.quote(base_commit)
    reset = (
        f"git reset --hard {quoted_base} && "
        f"git checkout -B benchmark-base {quoted_base} && "
        "git remote | while IFS= read -r remote; do git remote remove \"$remote\"; done && "
        "git for-each-ref --format='%(refname)' | while IFS= read -r ref; do "
        "[ \"$ref\" = refs/heads/benchmark-base ] || git update-ref -d \"$ref\"; done && "
        "git reflog expire --expire=now --all && git gc --prune=now; "
    )
    script = (
        'rm -rf /seed/..?* /seed/.[!.]* /seed/* 2>/dev/null; '
        'cp -a /app/. /seed/ && cd /seed && '
        f'git config --global --add safe.directory /seed; {reset}'
        'chown -R 1000:1000 /seed'
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh",
         "-v", f"{destination}:/seed", image_ref_str, "-c", script],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not seed the workspace from {image_ref_str}: "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


async def seed_workspace_async(
    image_ref_str: str, base_commit: str, destination: str,
) -> None:
    """Seed without blocking concurrent agents on the launcher's event loop."""
    await asyncio.to_thread(seed_workspace, image_ref_str, base_commit, destination)


# --------------------------------------------------------------------------- #
# Inner run (inside the container)
# --------------------------------------------------------------------------- #
async def run_inner(args) -> int:
    """Run the MetaAgent on one instance. Called inside the task container."""
    from agentevolver.agent import agent_manager
    from agentevolver.connector import connector_manager
    from agentevolver.extension import extension_manager
    from agentevolver.hook import hook_manager
    from agentevolver.memory import memory_manager
    from agentevolver.model import model_manager
    from agentevolver.prompt import prompt_manager
    from agentevolver.session.context import (
        bind_session_roots,
        configured_session_owner,
        ensure_session_sandbox,
    )
    from agentevolver.session.types import SessionContext
    from agentevolver.skill import skill_manager
    from agentevolver.task import TaskCategory, TaskPriority, TaskStatus, task_manager
    from agentevolver.tool import tool_manager
    from agentevolver.trace import trace_manager
    from agentevolver.trajectory import trajectory_manager
    from agentevolver.version import version_manager

    instance = json.loads(args.instance_json)  # already the SAFE subset (see inner_command)
    instance_id = instance["instance_id"]

    shared_extension_root = config.extension_root
    ctx = SessionContext(id=instance_id, name=f"swebench_pro_{instance_id}")
    fs_sandbox = ensure_session_sandbox(
        ctx,
        owner=configured_session_owner(config),
        shared_extension_root=shared_extension_root,
    )
    bind_session_roots(config, fs_sandbox)
    bind_task_workspace(ctx, fs_sandbox)

    extension_manager.set_base_dir(shared_extension_root)
    logger.initialize(config=config)
    logger.info(f"| 📂 [{instance_id}] Session root: {fs_sandbox.project_root}")
    logger.info(f"| 🧱 [{instance_id}] Working directory: {config.workspace_root}")

    from agentevolver.config import validate_assembly
    for problem in validate_assembly(config):
        logger.warning(f"| ⚠️ Config: {problem}")

    await version_manager.initialize()
    await trace_manager.initialize()
    await trace_manager.start()
    await trajectory_manager.initialize()
    await hook_manager.initialize()
    await model_manager.initialize()
    await prompt_manager.initialize()
    await memory_manager.initialize(memory_names=config.memory_names)
    await tool_manager.initialize(tool_names=config.tool_names)
    await skill_manager.initialize(skill_names=config.skill_names)
    await connector_manager.initialize(connector_names=config.connector_names)
    await agent_manager.initialize(agent_names=config.agent_names)
    manifest = await extension_manager.initialize()
    logger.info(f"| ✅ Extensions: {[f'{c.module}:{c.name}' for c in manifest.components]}")

    task_log_root = str(path_manager.under(config.log_root, P.LOG_MODULE, module="tasks"))
    await task_manager.initialize(log_root=task_log_root, handler=None)
    await task_manager.start(num_workers=1)

    content, task_files, task_meta = build_task_content(instance, args.task_file, task_log_root)

    async def handler(record):
        return await agent_manager(
            name="meta_agent",
            input={"task": record.task.content, "files": record.task.files},
            ctx=ctx,
        )

    task_manager.set_handler(handler)

    started = time.time()
    result: dict = {"instance_id": instance_id, "status": "failed", "error": None}
    try:
        task_id = await task_manager.submit(
            content=content,
            category=TaskCategory.USER,
            priority=TaskPriority.HIGH,
            files=task_files,
            metadata={"instance_id": instance_id, "repository": instance.get("repo", ""),
                      "language": instance.get("repo_language", ""), **task_meta},
            session_id=instance_id,
        )
        while True:
            record = await task_manager.get(task_id)
            if record and record.task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                break
            await asyncio.sleep(1)

        data = getattr(record.result, "data", None) or {}
        exhausted = bool(data.get("stopped_by_constraint"))
        result.update(
            status="done" if (record.task.status == TaskStatus.DONE or exhausted) else record.task.status.value,
            error=record.error,
            steps=data.get("step"),
            max_step=data.get("max_step"),
            stopped_by_constraint=exhausted,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"| ❌ [{instance_id}] run failed: {e}", exc_info=True)
        result["error"] = str(e)

    # Collect the patch (the diff from base_commit), whatever the status — an unfinished
    # attempt still has edits worth grading.
    try:
        patch = collect_patch(CONTAINER_WORKSPACE, instance.get("base_commit", ""))
        patch_path = str(path_manager.under(fs_sandbox.project_root, P.PROJECT_PATCH))
        with open(patch_path, "w", encoding="utf-8") as handle:
            handle.write(patch)
        result["patch_path"] = patch_path
        result["patch_bytes"] = len(patch)
        result["has_patch"] = bool(patch.strip())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️ [{instance_id}] could not collect the patch: {e}")
        result["patch_error"] = str(e)

    result["time_seconds"] = round(time.time() - started, 1)
    result["session_path"] = str(fs_sandbox.project_root)
    result["spend"] = _summarise_spend(str(fs_sandbox.project_root), instance_id)

    result_path = path_manager.under(fs_sandbox.project_root, P.PROJECT_RESULT)
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for label, step in (("task manager", task_manager.stop), ("trace manager", trace_manager.stop)):
        try:
            await step()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ {label}: {e}")

    logger.info(f"| {'✅' if result['status'] == 'done' else '❌'} [{instance_id}] {result['status']} "
                f"in {result['time_seconds']}s, {result.get('steps')} steps")
    return 0 if result["status"] == "done" else 1


def inner_command(row: dict, args) -> str:
    """The command the launcher runs inside the container — SAFE fields only in the payload."""
    payload = json.dumps(_safe_instance(row))
    parts = [
        shlex.quote(sys.executable),
        shlex.quote(f"{CONTAINER_REPO}/examples/run_swebench_pro.py"),
        "--instance-json", shlex.quote(payload),
        "--config", shlex.quote(args.config.replace(root, CONTAINER_REPO)),
        "--task-file", shlex.quote(args.task_file.replace(root, CONTAINER_REPO)),
    ]
    overrides = getattr(args, "user_cfg_options", None) or {}
    if overrides:
        parts.append("--cfg-options")
        parts.extend(shlex.quote(f"{k}={v}") for k, v in overrides.items())
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Launcher (host) — one container per instance
# --------------------------------------------------------------------------- #
async def run_launcher(args) -> int:
    from agentevolver.sandbox import sandbox_manager

    check_shared_roots_readable()

    # A benchmark instance may be run repeatedly with different models. Session state
    # includes memory, trajectories, prompts and grader counters, so keying it only by
    # instance_id can leak an earlier model's work into a later run. Give every launcher
    # invocation its own owner namespace unless the caller deliberately supplied one.
    overrides = dict(getattr(args, "user_cfg_options", None) or {})
    if not overrides.get("output_owner"):
        run_namespace = f"{config.get('tag') or 'swebench_pro'}_{time.strftime('%Y%m%d_%H%M%S')}"
        overrides["output_owner"] = run_namespace
        args.user_cfg_options = overrides
        setattr(config, "output_owner", run_namespace)
        logger.info(f"| 🧰 Isolated run namespace: {run_namespace}")

    grader_repo = os.path.abspath(args.grader_repo)
    if not os.path.isfile(os.path.join(grader_repo, "swe_bench_pro_eval.py")):
        logger.error(f"| ❌ grader repo not found at {grader_repo} — clone scaleapi/SWE-bench_Pro-os there")
        return 1

    instances = load_instances()
    task_ids = [t.strip() for t in args.task_ids.split(",")] if args.task_ids else None
    selected = select_instances(instances, task_ids=task_ids, start=args.start, end=args.end)
    if not selected:
        logger.error("| ❌ no instances selected")
        return 1
    logger.info(f"| 📋 SWE-bench Pro: {len(selected)} instance(s), concurrency={args.concurrency}")

    wall_clock = int(getattr(config, "WALL_CLOCK", 7200) or 7200)
    forwarded = {k: os.environ[k] for k in _FORWARDED_ENV if os.environ.get(k)}
    prefix = interpreter_prefix()
    mounts = {root: CONTAINER_REPO, prefix: prefix}

    # The shared extension library lives inside the mounted repo at
    # ``/AgentEvolver/extension``; the inner run writes its manifest there as the
    # container user. Grant it once here (not per instance — concurrent instances
    # would chown it under one another); ``restore_ownership`` below hands it back.
    grant_to_container_user(str(path_manager.get(P.EXTENSION)))

    results: list = []
    results_lock = asyncio.Lock()
    out_dir = args.out or str(path_manager.under(
        config.project_root,
        P.PROJECT_RESULT_RUN,
        run_id=time.strftime("run_%Y%m%d_%H%M%S"),
    ))
    os.makedirs(out_dir, exist_ok=True)

    def _write_results():
        results_path = path_manager.resolve_under(out_dir, "results.json")
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=2)

    async def _run_one_instance(index, row):
        instance_id = row["instance_id"]
        ref = image_ref(row)
        logger.info(f"| ▶️ [{index}/{len(selected)}] {instance_id} (image={ref})")
        started = time.time()
        record: dict = {"instance_id": instance_id, "status": "failed", "error": None}
        sandbox = None
        watcher_task = None
        workspace_dir = None
        eval_counter = {"count": 0}
        try:
            owner = str(config.get("output_owner") or config.tag)
            workspace_dir = str(path_manager.get(
                P.SESSION_WORKSPACE, owner=owner, session_id=instance_id,
            ))
            session_path = str(path_manager.get(
                P.SESSION, owner=owner, session_id=instance_id,
            ))
            await seed_workspace_async(ref, row.get("base_commit", ""), workspace_dir)

            bridge_dir = str(path_manager.under(session_path, P.PROJECT_EVAL_BRIDGE))
            os.makedirs(bridge_dir, exist_ok=True)
            os.chmod(bridge_dir, 0o777)
            grant_to_container_user(session_path)
            logger.info(f"| 🌱 [{instance_id}] Seeded and mounted at {CONTAINER_WORKSPACE} (owned by {CONTAINER_USER})")

            sandbox = await sandbox_manager.acquire(
                "docker", reuse_key=instance_id, image=ref, network=False, user=CONTAINER_USER,
                workdir=CONTAINER_WORKSPACE,
                mounts={**mounts, workspace_dir: CONTAINER_WORKSPACE, bridge_dir: CONTAINER_EVAL_BRIDGE},
                env={
                    "PYTHONPATH": CONTAINER_REPO,
                    "AGENTEVOLVER_EVAL_BRIDGE": CONTAINER_EVAL_BRIDGE,
                    "AGENTEVOLVER_EVAL_BUDGET": str(_EVAL_BUDGET),
                    "AGENTEVOLVER_HOME": CONTAINER_REPO,
                    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                    **forwarded,
                },
                timeout_minutes=(wall_clock + _LANDING_MARGIN_SECONDS) // 60,
            )
            command = inner_command(row, args)
            logger.info(f"| 🚀 [{instance_id}] {command[:160]}")
            watcher_task = asyncio.create_task(
                eval_bridge_watcher(bridge_dir, workspace_dir, row, grader_repo, _EVAL_BUDGET, eval_counter))
            execution = await sandbox.run_command(
                command, workspace_root=CONTAINER_REPO, timeout=wall_clock + _LANDING_MARGIN_SECONDS)
            if execution.stdout:
                print(execution.stdout)
            if execution.stderr:
                print(execution.stderr, file=sys.stderr)

            inner_result = str(path_manager.under(session_path, P.PROJECT_RESULT))
            if os.path.isfile(inner_result):
                with open(inner_result, encoding="utf-8") as handle:
                    record.update(json.load(handle))
            else:
                record["error"] = f"the inner run left no result.json (container exit {execution.exit_code})"
            record["egress_audit"] = sandbox_manager.egress_audit(sandbox)

            # Final leaderboard-style grade of the patch the agent left behind. Counts only,
            # exactly as the mid-run bridge would return; the oracle never crosses back.
            logger.info(f"| ⚖️ [{instance_id}] final grade of the collected patch…")
            final = await _grade_once(workspace_dir, row, grader_repo)
            record["final_grade"] = final
            record["resolved"] = bool(final.get("resolved"))
            logger.info(f"| {'🟢 RESOLVED' if record['resolved'] else '🔴 unresolved'} "
                        f"[{instance_id}] {final}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{instance_id}] launcher failure: {e}", exc_info=True)
            record["error"] = str(e)
        finally:
            if watcher_task is not None:
                watcher_task.cancel()
                with contextlib.suppress(Exception):
                    await watcher_task
            record["grader_evals"] = eval_counter["count"]
            if eval_counter["count"]:
                record["leaderboard_comparable"] = False
            if sandbox is not None:
                await sandbox_manager.release("docker", reuse_key=instance_id)
            # Bounded disk on a near-full shared volume: each instance has its own ~1GB
            # image and a seeded repo workspace. Reclaim both once graded — result.json and
            # agent.patch live in the session root (not the workspace), so scores survive.
            if getattr(args, "reclaim_disk", True):
                await asyncio.to_thread(_reclaim_instance_disk, workspace_dir, ref)

        record.setdefault("time_seconds", round(time.time() - started, 1))
        logger.info(f"| {'✅' if record['status'] == 'done' else '❌'} [{index}/{len(selected)}] "
                    f"{instance_id} → {record['status']} in {record['time_seconds']}s")
        async with results_lock:
            results.append(record)
            _write_results()
        return record

    sem = asyncio.Semaphore(max(1, int(args.concurrency or 1)))

    async def _bounded(index, row):
        async with sem:
            try:
                return await _run_one_instance(index, row)
            except Exception as e:  # noqa: BLE001
                logger.error(f"| ❌ [{row['instance_id']}] crashed: {e}")
                return {"instance_id": row["instance_id"], "status": "failed", "error": str(e)}

    try:
        await asyncio.gather(*[_bounded(i + 1, r) for i, r in enumerate(selected)])
    finally:
        for shared in (str(path_manager.get(P.OUTPUT)), config.extension_root):
            with contextlib.suppress(Exception):
                restore_ownership(shared)

    done = sum(1 for r in results if r.get("status") == "done")
    logger.info(f"| ✅ SWE-bench Pro run done: {done}/{len(selected)} completed. Results: {out_dir}")
    return 0


# --------------------------------------------------------------------------- #
# Args + main
# --------------------------------------------------------------------------- #
def parse_args():
    parser = argparse.ArgumentParser(description="Run AgentEvolver on SWE-bench Pro.")
    parser.add_argument("--instance-json", default=None,
                        help="Inner mode: the SAFE instance subset as JSON. Omit for launcher mode.")
    parser.add_argument("--task-ids", default=None, help="Comma-separated instance_ids.")
    parser.add_argument("--start", type=int, default=None, help="Start index (inclusive).")
    parser.add_argument("--end", type=int, default=None, help="End index (exclusive).")
    parser.add_argument("--config", default=os.path.join(root, "configs", "swebench_pro_agent.py"),
                        help="Config file (evolution arm by default; use ..._baseline.py for the control).")
    parser.add_argument("--task-file", default=DEFAULT_TASK_FILE)
    parser.add_argument("--grader-repo", default=DEFAULT_GRADER_REPO,
                        help="Path to the cloned scaleapi/SWE-bench_Pro-os checkout.")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--reclaim-disk", dest="reclaim_disk", action="store_true", default=True,
                        help="After each instance is graded, delete its seeded workspace and its "
                             "docker image to bound disk on a full volume (default on).")
    parser.add_argument("--no-reclaim-disk", dest="reclaim_disk", action="store_false",
                        help="Keep each instance's workspace and image (needs ~1GB per instance).")
    parser.add_argument("--out", default=None, help="Results output directory.")
    parser.add_argument("--cfg-options", nargs="+", default=None,
                        help="Config overrides, e.g. model_name=llm_hub/claude-opus-5")
    return parser.parse_args()


def parse_cfg_options(items) -> dict[str, str]:
    """Normalize repeatable ``key=value`` CLI overrides before config loading."""
    return dict(item.split("=", 1) for item in (items or []) if "=" in item)


async def main() -> int:
    args = parse_args()
    overrides = parse_cfg_options(args.cfg_options)
    # Config.initialize expects a mapping, while argparse collects repeatable options
    # as a list. Normalize before crossing that boundary.
    args.cfg_options = overrides
    config.initialize(config_path=args.config, args=args)
    args.user_cfg_options = overrides
    if overrides:
        for key, value in overrides.items():
            with contextlib.suppress(Exception):
                setattr(config, key, value)

    if args.instance_json:
        return await run_inner(args)
    return await run_launcher(args)


if __name__ == "__main__":
    import signal

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
    sys.exit(asyncio.run(main()))
