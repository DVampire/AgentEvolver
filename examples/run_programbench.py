"""Run MetaAgent on selected ProgramBench tasks.

Runs tasks only — no scoring. See agentevolver/benchmark/default/programbench.py for
the `programbench eval`-based scorer, which can be pointed at this script's output
(submission.tar.gz per task) separately.

Architecture
------------
The agent runs **inside the task's own image**. That is the only arrangement where the
toolchain it probes is the toolchain that will build its code: each instance ships a
~3GB image carrying that project's compiler, headers and reference binary, and no
general-purpose image can carry 201 projects' build dependencies. cmatrix needs
`ncurses.h`; shellharden needs a Rust toolchain; a base image has neither, so an agent
working anywhere else would probe one environment and build in another.

So this script has two modes:

* **launcher** (default, run on the host) — selects instances and, for each, starts a
  container from that instance's image with the framework and its interpreter mounted
  in, then runs the inner mode inside it.
* **inner** (``--instance-json …``, run inside that container) — the actual MAS run.
  There is no sandbox routing: the tools operate on the local filesystem, which *is*
  the task environment, and ``/workspace`` is right there with the docs, the reference
  binary and the git repo.

Because the body of the per-instance loop is a container, the loop lives in the
launcher rather than inside the agent process.

Network
-------
The container is created with no network interface. Its only route out is a Unix socket
belonging to a relay on the host, which permits the model endpoints and nothing else
(``sandbox_allow_model_endpoints`` / ``sandbox_deny_hosts`` in the config). So the
agent's brain can reach a model while its shell cannot reach the internet — the
benchmark's anti-cheat, and load-bearing: runs have been observed attempting `git clone`
and `curl` against the upstream repository. Verified from inside a container:
`git clone https://github.com/…` returns "HTTP code 403 from proxy after CONNECT", and a
raw connect to 1.1.1.1 returns "Network is unreachable". Every attempt is recorded in the
run's results, so "the work was offline" is checkable rather than assumed.

Nothing can be installed inside the container either, so the launcher passes each
instance's metadata in on the command line rather than expecting the benchmark dataset
to be importable there.

Usage
-----
    # Two named instances, self-evolution roster per the default config
    python examples/run_programbench.py --task-ids <instance_id_1>,<instance_id_2>

    # The control arm. A config, not a flag, so a result's roster is readable from the
    # repo instead of reconstructed from a command line.
    python examples/run_programbench.py --start 0 --end 5 \
        --config configs/programbench_agent_baseline.py

    # Override config options
    python examples/run_programbench.py --start 0 --end 1 \
        --cfg-options model_name=openai/o3
"""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(verbose=True)

from mmengine import DictAction

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.sandbox import sandbox_manager
from agentevolver.utils import make_id


# Official ProgramBench baseline (mini-swe-agent) image tag: an "artifact-free" build
# carrying the compiled binary + docs but no leftover build artifacts or source
# (verified by pulling and inspecting two instances). NOT ":latest" — no such tag.
IMAGE_TAG = "task_cleanroom_v6"

#: Where the agent is told to stash the provided binary before its first build
#: overwrites it, and what the deliverable strips back out so the reference never
#: reaches the graded tarball. `compile.sh` must produce `./executable`, which is the
#: exact path the reference occupies, so the agent's first build destroys the only
#: oracle it has: observed on cmatrix, the reference was gone by command 22 of 65 and
#: the remaining 43 could not compare anything against it.
REFERENCE_COPY = "reference_executable"

#: Ours, not the agent's, and therefore not part of the reconstruction. `inputs/` is where
#: the framework stages task attachments so the agent can read them: the task document
#: lives in the checkout, outside the workspace, and `check_session_path` would otherwise
#: refuse to open it. Necessary, and no more a deliverable than the stashed reference is.
_SCAFFOLDING = (REFERENCE_COPY, "inputs")

#: Paths inside the task container.
CONTAINER_REPO = "/AgentEvolver"
CONTAINER_WORKSPACE = "/workspace"

#: The task document. Lives under examples/tasks/ like every other run script's task, so
#: the instructions are readable and diffable on their own, render as a styled page
#: through resolve_task()'s view, and can be swapped per run with --task-file without
#: editing this file. Its ``<objective>`` carries the per-instance placeholders.
DEFAULT_TASK_FILE = os.path.join(root, "examples", "tasks", "programbench_reconstruction.html")

#: Environment the inner run needs. Nothing else is forwarded into the container.
_FORWARDED_ENV = (
    "OPENAI_API_KEY", "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE",
    "GOOGLE_API_KEY", "GOOGLE_API_BASE",
    "OPENROUTER_API_KEY", "OPENROUTER_API_BASE",
)


def host_repo_root() -> str:
    """The repo root as the Docker daemon sees it.

    Bind mounts resolve against the host filesystem, so a launcher that is itself
    inside a container has to name the host path rather than its own.
    """
    return os.environ.get("AGENTEVOLVER_HOST_ROOT") or root


def interpreter_prefix() -> str:
    """Root of the Python installation running this process.

    Mounted into the task image at the *same* path, because a conda or virtual
    environment records absolute paths — its scripts, its ``sys.prefix`` and its
    site-packages all resolve against where it was created, so attaching it at a
    different mount point breaks it.
    """
    return sys.base_prefix if sys.base_prefix != sys.prefix else sys.prefix


# --------------------------------------------------------------------------- #
# Instance selection and task construction (shared by both modes)
# --------------------------------------------------------------------------- #
def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset of ProgramBench instances by id list and/or index range.

    `task_ids` takes precedence over `start`/`end` (a warning is returned, not
    raised, so a run does not die over a redundant argument).
    """
    warnings = []
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")

    if task_ids:
        by_id = {i["instance_id"]: i for i in instances}
        selected = [by_id[tid] for tid in task_ids if tid in by_id]
        if start is not None or end is not None:
            warnings.append("--start/--end ignored because --task-ids was given")
        unknown = [tid for tid in task_ids if tid not in by_id]
        if unknown:
            warnings.append(f"unknown task id(s) skipped: {unknown}")
        return selected, warnings

    return list(instances[start:end]), warnings


def build_task_content(instance, task_file=None, task_log_root=None):
    """Resolve the task document for one ProgramBench instance.

    Returns ``(content, files, metadata)``, matching what ``resolve_task`` hands to
    ``task_manager.submit``. This does not simply call ``resolve_task`` because the
    document carries per-instance placeholders: only the target program varies across
    the 201 instances, and it is substituted into the objective rather than appended
    after it — the agent should know what it is rebuilding before reading five
    thousand characters of method.

    Substitution is a literal replace rather than ``str.format``: the document is
    full of shell and code samples whose braces would otherwise need escaping, and
    an escaping mistake there fails silently.

    Both the agent's text and the rendered view are substituted. Filling only the
    text leaves whoever opens ``task_view.html`` looking at a raw ``{repository}``.
    """
    from agentevolver.task import load_task_document
    from agentevolver.visual import render_task_page

    path = task_file or DEFAULT_TASK_FILE
    doc = load_task_document(path)
    slots = {
        "{repository}": str(instance.get("repository", "")),
        "{language}": str(instance.get("language", "")),
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
        view_path = os.path.join(task_log_root, f"task_view_{instance.get('instance_id', 'task')}.html")
        os.makedirs(task_log_root, exist_ok=True)
        render_task_page(html_body, view_path, title=doc.title)
        metadata["task_view"] = view_path

    return content, [doc.source_path], metadata


def parse_args():
    parser = argparse.ArgumentParser(description="Run MetaAgent on selected ProgramBench tasks")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "programbench_agent.py"),
        help="Config file path",
    )
    parser.add_argument(
        "--task-file", default=DEFAULT_TASK_FILE,
        help="Task document (.html/.md) carrying the reconstruction instructions. "
             "Swap it to try a different prompt without editing this script.",
    )
    parser.add_argument("--task-ids", default=None, help="Comma-separated ProgramBench instance_id list")
    parser.add_argument("--start", type=int, default=None, help="Start index (inclusive) into the loaded instance list")
    parser.add_argument("--end", type=int, default=None, help="End index (exclusive) into the loaded instance list")
    parser.add_argument("--out", default=None, help="Results JSON output directory")
    parser.add_argument(
        "--instance-json", default=None,
        help="Inner mode: JSON metadata for the single instance to run. Set by the "
             "launcher when it starts this script inside a task container, where the "
             "benchmark dataset is not importable and nothing can be installed, so the "
             "metadata travels on the command line.",
    )
    parser.add_argument(
        "--cfg-options", nargs="+", action=DictAction,
        help="Override config options in key=value format",
    )
    args = parser.parse_args()
    if not args.instance_json and not args.task_ids and args.start is None and args.end is None:
        parser.error("specify --task-ids or --start/--end; refusing to run the entire benchmark by default")
    return args


# --------------------------------------------------------------------------- #
# Deliverable
# --------------------------------------------------------------------------- #
def collect_submission(workspace: str, dest_dir: str) -> dict:
    """Package the reconstruction from `workspace` into `dest_dir/submission.tar.gz`.

    Prefers the committed tree (``git archive HEAD``), which is what makes the task
    document's "commit your solution" and ".gitignore your build artifacts" mean
    anything: an agent that ignores its scratch files then ships a clean submission.
    Left to a plain tar of the directory, neither instruction affected the deliverable
    at all — one run shipped 70 files of which 6 were the solution, the rest comparison
    dumps like `ref_dumb.out` and `my_V.od`.

    With one exception, and it is the important one: **if the archive does not contain
    `compile.sh`, ship the whole tree instead.** A submission without it scores zero by
    definition, so when the committed tree lacks the build script — the agent forgot to
    commit, or committed only part of its work — the fuller tree can only help. Being
    strict there would turn a scoring mistake into a guaranteed zero.

    Either way the stashed reference binary is excluded. The official grader also
    deletes byte-identical copies of it by hash, so this is the belt to that braces.

    Returns a description of what was shipped, for the run's results file.
    """
    import tarfile

    os.makedirs(dest_dir, exist_ok=True)
    tarball = os.path.join(dest_dir, "submission.tar.gz")

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", workspace, *args], capture_output=True, text=True, timeout=120,
        )

    info: dict = {"source": None, "uncommitted": [], "note": None}

    counted = _git("rev-list", "--count", "HEAD")
    commit_count = int(counted.stdout.strip() or 0) if counted.returncode == 0 else 0

    # Files the agent left behind that git is not tracking and .gitignore does not
    # cover. Recorded rather than acted on: it is the most useful thing to know when a
    # submission turns out to be missing something.
    status = _git("status", "--porcelain", "--untracked-files=all")
    if status.returncode == 0:
        info["uncommitted"] = [
            entry for entry in (line[3:] for line in status.stdout.splitlines())
            if entry and not entry.split("/")[0] in _SCAFFOLDING
        ]

    # The repository arrives with one commit holding the shipped documentation, so
    # "has commits" is not the question — "has the agent committed anything" is.
    if commit_count > 1:
        archived = _git("archive", "--format=tar.gz", "-o", tarball, "HEAD")
        if archived.returncode != 0:
            info["note"] = (
                f"git archive failed ({archived.stderr.strip()[:200]}); shipped the whole workspace"
            )
        else:
            with tarfile.open(tarball, "r:gz") as handle:
                names = handle.getnames()
            if any(name.lstrip("./") == "compile.sh" for name in names):
                info["source"] = "git-archive"
            else:
                info["note"] = (
                    "the committed tree has no compile.sh, so the whole workspace was "
                    "shipped instead — a submission without it scores zero"
                )
    else:
        info["note"] = (
            "no commit beyond the repository's initial one; shipped the whole workspace"
            if commit_count else "no commits at all; shipped the whole workspace"
        )

    if info["source"] is None:
        info["source"] = "full-tree"
        with tarfile.open(tarball, "w:gz") as handle:
            for entry in sorted(os.listdir(workspace)):
                if entry in _SCAFFOLDING:
                    continue
                handle.add(os.path.join(workspace, entry), arcname=entry)

    # Unpack a browsable copy beside the tarball so the reconstruction can be read
    # without untarring it by hand. The scorer still consumes submission.tar.gz.
    extract_dir = os.path.join(dest_dir, "submission")
    with tarfile.open(tarball, "r:gz") as handle:
        handle.extractall(extract_dir)

    info["tarball"] = tarball
    info["files"] = len(os.listdir(extract_dir))
    return info


def audit_reference_binary(log_root: str) -> dict:
    """Check the run's own trace for binary analysis of the reference.

    The task rules forbid inspecting the reference binary as a *file*; the official
    image enforces that with permissions, by running as a user who cannot read it. This
    run is root, which bypasses the permission bits — so the prohibition holds by
    instruction rather than by the filesystem, and an instruction is worth checking.
    Recording the answer makes "no binary analysis was used" substantiable rather than
    assumed.

    Analysis of the agent's *own* build is explicitly allowed, so only actions naming
    the reference are flagged.
    """
    tools = ("objdump", "readelf", "strings", "nm ", "xxd", "hexdump", "gdb", "ltrace", "strace")
    hits: list = []
    trace_dir = Path(log_root) / "trace"
    for path in sorted(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            # Only what the agent *ran*. Scanning raw trace lines instead flagged the task
            # document itself: its rules name `objdump` in the course of forbidding it, and
            # the prompt travels through the trace, so the audit accused every run of the
            # thing it was checking for.
            payload = event.get("input")
            if not isinstance(payload, dict):
                continue
            command = " ".join(
                str(payload.get(key, "")) for key in ("command", "code", "args", "path")
            )
            if not command.strip():
                continue
            if REFERENCE_COPY not in command and "./executable" not in command:
                continue
            lowered = command.lower()
            for tool in tools:
                if tool in lowered:
                    hits.append({
                        "tool": tool.strip(),
                        "action": event.get("action_name"),
                        "command": command[:200],
                    })
                    break
    return {"checked": True, "suspicious_actions": hits}


# --------------------------------------------------------------------------- #
# Inner mode — the MAS run for one instance, inside that instance's container
# --------------------------------------------------------------------------- #
def bind_task_workspace(ctx, fs_sandbox) -> None:
    """Point the run's working directory at the task workspace, and check it landed.

    ``bind_session_roots`` puts ``workspace_root`` inside the session directory, which is
    right when an agent is building a deliverable from nothing and wrong here: the
    material already exists, at ``/workspace``, and that is where the build has to
    happen. Leaving it has two consequences, both fatal and neither loud —

    * bash starts in the session directory rather than the task's, so every relative path
      in the agent's own commands misses; and
    * ``check_session_path`` permits reads only under the session roots, so
      ``read_file_tool('/workspace/README.md')`` — the documentation that *is* the task
      specification — answers "Sandbox denied read outside allowed roots".

    The session tree still holds the logs, the trace and the collected submission. It is
    just not where the work happens.

    The second half is the mirror image: the framework's output tree must not land inside
    the task workspace, or it ships in the submission — a session directory full of logs
    is not a reconstruction. That depends on the process's working directory, so it is
    checked rather than assumed.
    """
    config.workspace_root = CONTAINER_WORKSPACE
    if getattr(ctx, "extra", None) is not None:
        ctx.extra["workspace_root"] = CONTAINER_WORKSPACE

    project_root = str(fs_sandbox.project_root)
    if project_root == CONTAINER_WORKSPACE or project_root.startswith(CONTAINER_WORKSPACE + os.sep):
        raise RuntimeError(
            f"the session tree resolved inside the task workspace ({project_root}); run "
            f"the inner mode with its working directory at {CONTAINER_REPO} so relative "
            f"output paths resolve against the checkout instead of the task"
        )



async def run_inner(args) -> int:
    """Run the agent on one instance. Called inside the task container."""
    from agentevolver.agent import agent_manager
    from agentevolver.connector import connector_manager
    from agentevolver.extension import extension_manager
    from agentevolver.hook import hook_manager
    from agentevolver.memory import memory_manager
    from agentevolver.model import model_manager
    from agentevolver.prompt import prompt_manager
    from agentevolver.session.project import bind_session_roots, ensure_session_sandbox
    from agentevolver.session.types import SessionContext
    from agentevolver.skill import skill_manager
    from agentevolver.task import TaskCategory, TaskPriority, TaskStatus, task_manager
    from agentevolver.tool import tool_manager
    from agentevolver.trace import trace_manager
    from agentevolver.trajectory import trajectory_manager
    from agentevolver.version import version_manager

    instance = json.loads(args.instance_json)
    instance_id = instance["instance_id"]

    shared_extension_root = config.extension_root
    # The session id IS the instance id, so a run lands in
    # output/<owner>/sessions/<instance_id>/ and is findable by task name rather than by
    # an opaque hash. Re-running reuses the directory and overwrites the previous
    # submission — copy anything worth keeping out first.
    ctx = SessionContext(id=instance_id, name=f"programbench_{instance_id}")
    fs_sandbox = ensure_session_sandbox(ctx, shared_extension_root=shared_extension_root)
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

    task_log_root = os.path.join(config.log_root, "tasks")
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
            metadata={
                "instance_id": instance_id,
                "repository": instance.get("repository", ""),
                "language": instance.get("language", ""),
                "image_name": instance.get("image_name", ""),
                **task_meta,
            },
            session_id=instance_id,
        )
        while True:
            record = await task_manager.get(task_id)
            if record and record.task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                break
            await asyncio.sleep(1)

        data = getattr(record.result, "data", None) or {}
        # Running out of budget is a *reason*, not a different outcome: the workspace is
        # still there and still worth grading, and the official harness submits at the
        # step limit rather than discarding the attempt. Treating it as a failure here
        # would report a gradeable submission as no submission.
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

    # Collected whatever the status: an unfinished attempt still has a workspace, and
    # grading it is how partial credit happens.
    try:
        result["submission"] = collect_submission(CONTAINER_WORKSPACE, str(fs_sandbox.workspace_root))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️ [{instance_id}] could not collect the submission: {e}")
        result["submission"] = {"error": str(e)}

    result["reference_audit"] = audit_reference_binary(config.log_root)
    result["time_seconds"] = round(time.time() - started, 1)
    result["session_path"] = str(fs_sandbox.project_root)

    # Written where the launcher reads it back through the mount.
    with open(os.path.join(str(fs_sandbox.project_root), "result.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for label, step in (("task manager", task_manager.stop), ("trace manager", trace_manager.stop)):
        try:
            await step()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ {label}: {e}")

    logger.info(
        f"| {'✅' if result['status'] == 'done' else '❌'} [{instance_id}] {result['status']} "
        f"in {result['time_seconds']}s, {result.get('steps')} steps"
    )
    return 0 if result["status"] == "done" else 1


# --------------------------------------------------------------------------- #
# Launcher — one container per instance, on the host
# --------------------------------------------------------------------------- #
def inner_command(instance, args) -> str:
    """The command the launcher runs inside the task container.

    Config and task-file paths are rewritten from the host's repo root to the mount
    point, since the same checkout is visible at a different path in there.
    """
    payload = json.dumps({
        key: instance.get(key, "")
        for key in ("instance_id", "repository", "language", "image_name")
    })
    return " ".join([
        shlex.quote(sys.executable),
        shlex.quote(f"{CONTAINER_REPO}/examples/run_programbench.py"),
        "--instance-json", shlex.quote(payload),
        "--config", shlex.quote(args.config.replace(root, CONTAINER_REPO)),
        "--task-file", shlex.quote(args.task_file.replace(root, CONTAINER_REPO)),
    ])


def restore_ownership(path: str) -> None:
    """Give a root-created output directory back to the user running the launcher.

    The agent runs as root inside the container, so everything it writes into the
    mounted output tree lands root-owned — and the user then cannot delete it without a
    privileged helper, which is exactly the situation this avoids by using one here.
    """
    if not os.path.isdir(path):
        return
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{path}:/target", "alpine",
         "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/target"],
        capture_output=True, text=True, timeout=600,
    )


def session_dir(instance_id: str) -> str:
    """Where the inner run's session lands, as the launcher sees it."""
    from agentevolver.paths import P, path_manager

    owner = Path(str(path_manager.get(P.SESSION))).parent.name
    return os.path.join(root, "output", owner, "sessions", instance_id)


async def run_launcher(args) -> int:
    from agentevolver.data.programbench import ProgramBenchDataset

    await sandbox_manager.initialize()

    dataset = ProgramBenchDataset()
    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()] if args.task_ids else None
    selected, warnings = select_instances(
        dataset.data, task_ids=task_ids, start=args.start, end=args.end,
    )
    for warning in warnings:
        logger.warning(f"| ⚠️ {warning}")
    logger.info(f"| 📋 ProgramBench: {len(selected)}/{len(dataset.data)} instance(s) selected")
    if not selected:
        logger.warning("| ⚠️ No instances selected — nothing to run.")
        return 0

    prefix = interpreter_prefix()
    mounts = {host_repo_root(): CONTAINER_REPO, prefix: prefix}
    forwarded = {key: os.environ[key] for key in _FORWARDED_ENV if os.environ.get(key)}
    wall_clock = int(config.get("WALL_CLOCK", 21600))

    out_dir = args.out or os.path.join(root, "output", "results", "programbench")
    os.makedirs(out_dir, exist_ok=True)
    results_path = os.path.join(out_dir, f"run_{make_id()}.json")
    results = []

    for index, instance in enumerate(selected, start=1):
        instance_id = instance["instance_id"]
        image_ref = f"{instance.get('image_name', '')}:{IMAGE_TAG}"
        logger.info(f"| ▶️ [{index}/{len(selected)}] {instance_id} (image={image_ref})")

        started = time.time()
        record: dict = {"instance_id": instance_id, "status": "failed", "error": None}
        sandbox = None
        try:
            sandbox = await sandbox_manager.acquire(
                "docker",
                reuse_key=instance_id,
                image=image_ref,
                # No interface at all; the allow/deny lists come from the config and are
                # served by a relay on the host. See the module docstring.
                network=False,
                user="root",
                workdir=CONTAINER_WORKSPACE,
                mounts=mounts,
                env={
                    "PYTHONPATH": CONTAINER_REPO,
                    # Stop litellm fetching its model-price map at import time. It falls
                    # back to a bundled copy anyway, and the attempt would otherwise
                    # appear in the egress audit as a denial the agent never made.
                    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                    **forwarded,
                },
                timeout_minutes=wall_clock // 60 + 30,
            )
            command = inner_command(instance, args)
            logger.info(f"| 🚀 [{instance_id}] {command[:160]}")
            # Generous margin over the agent's own wall clock: the inner run enforces
            # its budget itself, and this only has to outlast it so a run that finishes
            # normally is never cut off mid-write.
            execution = await sandbox.run_command(
                command, workspace_root=CONTAINER_REPO, timeout=wall_clock + 600,
            )
            if execution.stdout:
                print(execution.stdout)
            if execution.stderr:
                print(execution.stderr, file=sys.stderr)

            inner_result = os.path.join(session_dir(instance_id), "result.json")
            if os.path.isfile(inner_result):
                with open(inner_result, encoding="utf-8") as handle:
                    record.update(json.load(handle))
            else:
                record["error"] = (
                    f"the inner run left no result.json (container exit {execution.exit_code}); "
                    f"see the container output above"
                )
            record["egress_audit"] = sandbox_manager.egress_audit(sandbox)
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{instance_id}] launcher failure: {e}", exc_info=True)
            record["error"] = str(e)
        finally:
            if sandbox is not None:
                await sandbox_manager.release("docker", reuse_key=instance_id)
            restore_ownership(os.path.join(root, "output"))

        record.setdefault("time_seconds", round(time.time() - started, 1))
        results.append(record)
        logger.info(
            f"| {'✅' if record['status'] == 'done' else '❌'} [{index}/{len(selected)}] "
            f"{instance_id} → {record['status']} in {record['time_seconds']}s"
        )

        # Written after every task so a mid-run crash still leaves a usable file.
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump({
                "config": args.config,
                "selection": {"task_ids": task_ids, "start": args.start, "end": args.end},
                "records": results,
            }, handle, ensure_ascii=False, indent=2)

    await sandbox_manager.cleanup()

    done = sum(1 for r in results if r["status"] == "done")
    print(f"\n✅ ProgramBench run done: {done}/{len(results)} task(s) completed")
    print(f"   Results: {results_path}")
    for r in results:
        submission = (r.get("submission") or {}).get("source", "-")
        denied = len(((r.get("egress_audit") or {}).get("denied")) or [])
        print(f"   {'✅' if r['status'] == 'done' else '❌'} {r['instance_id']:45} "
              f"steps={str(r.get('steps', '-')):>5}  submission={submission:12} "
              f"egress-denied={denied}")
    return 0


async def main() -> int:
    args = parse_args()
    config.initialize(config_path=args.config, args=args)
    if args.instance_json:
        return await run_inner(args)
    logger.initialize(config=config)
    return await run_launcher(args)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
