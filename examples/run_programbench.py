"""Run MetaAgent on selected ProgramBench tasks, modeled on run_meta_agent.py.

Runs tasks only — no scoring. See agentevolver/benchmark/default/programbench.py
for the existing `programbench eval`-based scorer, which can be pointed at this
script's output (submission.tar.gz per task) separately.

Each selected instance gets its own real Docker sandbox
(`sandbox_manager.acquire("opensandbox", image=f"{image_name}:task_cleanroom_v6", ...)`)
— the same image tag the official ProgramBench mini-swe-agent baseline uses, so the
agent actually sees the compiled `./executable` + docs in `/workspace`. Network
isolation (the baseline's `--network none`, its anti-cheat mechanism) is wired up and
ON by default — set `AGENTEVOLVER_SANDBOX_ISOLATE_NETWORK=0` to disable it. Requires
`disable_ipv6 = false` under `[egress]` in `~/.sandbox.toml` on hosts where IPv6 is
disabled at the kernel level (no `/proc/sys/net/ipv6`): opensandbox's egress sidecar
otherwise fails to start there, since it unconditionally asks Docker to set
`net.ipv6.*.disable_ipv6` sysctls that don't exist without an IPv6 stack. No
`--cap-drop`/`--user` parity either — the `opensandbox` package has no equivalent
parameter.

Requires the `benchmark`/`sandbox` extras (`pip install -e ".[benchmark,sandbox]"`)
and a reachable Docker daemon.

Launch it through ``scripts/run-in-sandbox.sh`` (Model X): the MAS itself runs in
the base container, and the per-task cleanroom is a *sibling* container created
through the mounted Docker socket. Running the bare ``python`` command instead
puts the agent process on the host — everything still works, because tool calls
route into the peer either way, but the framework is then outside the sandbox it
is supposed to be inside.

The two containers are not a redundancy: the base one has network so the agent can
reach its model, while the peer is created with ``network=False`` so the task
environment cannot. That is the benchmark's anti-cheat, and it is load-bearing —
runs have been observed attempting ``git clone`` and ``curl`` against the upstream
repository. Putting the MAS *inside* the task container would force a choice
between "no model access" and "no anti-cheat".

``programbench`` (the pip package carrying the task definitions) must be importable
in the image; ``agentevolver/base:latest`` does not ship it, hence the install in
the examples below.

Usage
-----
# Two named instances, self-evolution roster per the default config
scripts/run-in-sandbox.sh -- bash -c "pip install -q programbench && \
    python examples/run_programbench.py --task-ids <instance_id_1>,<instance_id_2>"

# The control arm. It is a config, not a flag, so a result's roster is readable
# from the repo instead of reconstructed from a command line.
scripts/run-in-sandbox.sh -- bash -c "pip install -q programbench && \
    python examples/run_programbench.py --start 0 --end 5 \
    --config configs/programbench_agent_baseline.py"

# Override config options
scripts/run-in-sandbox.sh -- bash -c "pip install -q programbench && \
    python examples/run_programbench.py --start 0 --end 1 \
    --cfg-options model_name=openai/o3"
"""

import argparse
import asyncio
import json
import os
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
from agentevolver.model import model_manager
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.memory import memory_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.agent import agent_manager
from agentevolver.extension import extension_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.session.types import SessionContext
from agentevolver.session.project import ensure_session_sandbox, bind_session_roots
from agentevolver.utils import make_id, dedent
from agentevolver.sandbox import sandbox_manager


# Official ProgramBench baseline (mini-swe-agent) image tag for the agent's own
# environment — an "artifact-free" build with the compiled binary + docs but no
# leftover build artifacts/source (verified by pulling and inspecting two instances;
# see the design spec §3.4). NOT ":latest" — that tag doesn't exist.
IMAGE_TAG = "task_cleanroom_v6"
#: Where the agent is told to stash the provided binary before its first build
#: overwrites it (see SYSTEM_PROMPT), and what extract_submission() strips back out
#: so the reference never reaches the graded tarball.
REFERENCE_COPY = "reference_executable"


# opensandbox-server's default port (8080) was found already occupied by an
# unrelated service on this shared machine when this was tested — ensure_server()'s
# health check only verifies "some HTTP server answers with status < 500", not "it's
# actually opensandbox-server", so it silently adopted the foreign service and every
# sandbox-create call 404'd. Use a dedicated port instead of the shared default.
SANDBOX_DOMAIN = os.environ.get("AGENTEVOLVER_OPENSANDBOX_DOMAIN", "localhost:28080")

# --network none (the official baseline's anti-cheat mechanism, see design spec §3.4)
# is what isolating the sandbox's network requests, via the opensandbox egress sidecar.
# That sidecar used to fail to start on hosts with no /proc/sys/net/ipv6 tree (IPv6
# disabled at the kernel level, as on this machine) — it unconditionally asked Docker to
# set net.ipv6.*.disable_ipv6 sysctls in the sidecar's netns, which don't exist when the
# kernel has no IPv6 stack at all. Fixed with no host changes needed: set
# `disable_ipv6 = false` under `[egress]` in ~/.sandbox.toml (opensandbox-server's own
# config, defaults `disable_ipv6 = true`) — skips that sysctl step entirely, which costs
# nothing on a host with no IPv6 to disable in the first place. Verified live: a direct
# `acquire(..., network=False)` now succeeds and a `curl` from inside the sandbox to a
# real external host returns no connection (HTTP code 000), confirming egress is actually
# blocked. Defaults ON now that the sidecar reliably starts; set
# AGENTEVOLVER_SANDBOX_ISOLATE_NETWORK=0 to fall back to allowing network (e.g. on a host
# where ~/.sandbox.toml hasn't been given the disable_ipv6 fix yet).
SANDBOX_ISOLATE_NETWORK = os.environ.get("AGENTEVOLVER_SANDBOX_ISOLATE_NETWORK", "1") == "1"

# Per the official baseline's system prompt (minisweagent/config/benchmarks/
# programbench.yaml) and verified container layout: the binary is always at
# ./executable relative to /workspace (the image's own WORKDIR), regardless of the
# instance; docs/man pages/README sit alongside it; an empty .git repo is provided
# for committing the solution into.
#
# The "preserve the reference first" step is not housekeeping. `compile.sh` must
# produce `./executable`, which is the exact path the reference binary occupies —
# so the agent's first build overwrites the only oracle it has. Observed on
# cmatrix: the reference was gone by command 22 of 65, and the remaining 43
# commands could not compare anything against it. The run that scored well had
# happened to dump `./executable -h` to a file beforehand; the run that scored
# 11 points lower had not, and neither run was told to.
SYSTEM_PROMPT = dedent("""
    You are an expert software engineer. You are given only a compiled binary of a
    program together with its documentation, running inside an offline sandbox with
    no internet access. Your task is to architect and implement, from scratch, a
    complete codebase that reproduces the original program's observable behavior as
    faithfully as possible.

    The binary is at `./executable` in your workspace (not named after the project).
    Documentation (README, man pages, etc.) sits alongside it. An empty git repository
    is already initialized in the workspace — commit your solution into it.

    Your absolute working directory is `/workspace` — always use that path (or a
    relative path from it). Any path under `/home/...` refers to the host machine, not
    your sandbox, and will not contain the files described above.

    FIRST, PRESERVE THE REFERENCE. Your `compile.sh` has to write `./executable`,
    which is where the reference binary sits — so your first build destroys the only
    thing you can check your work against, and nothing will warn you. Before you
    build anything, copy it aside:

        cp /workspace/executable /workspace/reference_executable

    From then on run `./reference_executable` for expected behavior and `./executable`
    for yours, and compare them directly. Do not delete or commit
    `reference_executable`; it is scaffolding, not part of your deliverable.

    HOW TO REPRODUCE BEHAVIOR. Reading the docs tells you which flags exist; only
    running the reference tells you what they do. Work flag by flag: run the
    reference with an input, run your build with the same input, diff the two, and
    fix the difference. Both exit status and output text matter. Getting `--help` to
    match is the easy part and it is not the same as reproducing the program — cover
    the behavior each flag controls, flags in combination, and how invalid input is
    rejected.

    ALWAYS PUT `timeout` IN FRONT OF EITHER BINARY. Never run `./executable` or
    `./reference_executable` bare:

        timeout 2 ./reference_executable -h; echo "exit=$?"
        timeout 2 ./executable -h;           echo "exit=$?"

    The program you are reconstructing may not exit on its own — a TUI, a REPL, a
    server, a loop — and an unfinished reconstruction may hang on inputs the
    reference handles instantly. That is not a rare case: it is a *defect you are
    hunting*, because "the reference prints usage and exits 0, mine hangs" is exactly
    the kind of difference the tests check. Discover it in two seconds via a timeout
    exit code of 124, not by blocking your own shell until the tool kills it — one
    hang costs more wall-clock than dozens of real experiments. This applies most to
    the very commands you most want to batch: a batch of comparisons with no
    `timeout` stalls on the first hang and you lose the rest of the batch with it.

    WHAT YOU MAY LEARN FROM THE BINARY — a whitelist, not a blacklist. You may
    **run** it and read what it produces: stdout, stderr, exit status, files it
    creates, how it responds to input. That is all. Anything that inspects the binary
    as a *file* rather than as a *program* is out of bounds — no disassembly, no
    decompilation, no `readelf`/`nm`/`objdump`/`strings`/`xxd`/`hexdump`, no debugger,
    no `strace`/`ltrace`. If you catch yourself grepping a symbol table, stop: you are
    reading the compiler's output instead of reproducing the program's behavior, and
    those symbols are mostly libc and kernel constants that tell you nothing about
    what this program does. One run of `./reference_executable --help` is worth more
    than every symbol in the file.

    Work entirely from running the binary and reading its documentation — you have no
    internet access and no access to the original source code. Do not search the
    internet, clone repositories, or install the project from any package manager.
    Your final deliverable must include a `./compile.sh` that builds a fresh
    `./executable` of equivalent behavior from your own source tree, on a clean
    checkout, with no dependency on the originally provided binary.

    Task:
""")


def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset of ProgramBench instances by id list and/or index range.

    `task_ids` takes precedence over `start`/`end` (a warning is returned, not
    raised, if both are given). Unknown ids are skipped, also as a warning.
    Raises ValueError if neither selector is given.

    Returns (selected: list[dict], warnings: list[str]).
    """
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")

    warnings = []
    if task_ids:
        if start is not None or end is not None:
            warnings.append("--start/--end ignored because --task-ids was given")
        by_id = {inst["instance_id"]: inst for inst in instances}
        selected = [by_id[tid] for tid in task_ids if tid in by_id]
        unknown = [tid for tid in task_ids if tid not in by_id]
        if unknown:
            warnings.append(f"unknown task id(s) skipped: {unknown}")
        return selected, warnings

    return list(instances[start:end]), warnings


def build_task_content(instance):
    """Build the MetaAgent task content for one ProgramBench instance.

    Folds SYSTEM_PROMPT into the content (agent_manager has no separate
    system-prompt override hook here, matching how run_meta_agent.py / run_hle.py
    already thread only task content through).
    """
    question = dedent(f"""
        Reconstruct the program `{instance.get('repository', '')}` (language: {instance.get('language', '')}).

        Implement a complete codebase that reproduces `./executable`'s behavior, with
        a `./compile.sh` that builds it from a clean checkout. Produce the full source
        tree (your git commits in the workspace) as your final answer.
    """)
    return f"{SYSTEM_PROMPT}\n\n{question}"


def parse_args():
    parser = argparse.ArgumentParser(description="Run MetaAgent on selected ProgramBench tasks")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "programbench_agent.py"),
        help="Config file path",
    )
    parser.add_argument("--task-ids", default=None, help="Comma-separated ProgramBench instance_id list")
    parser.add_argument("--start", type=int, default=None, help="Start index (inclusive) into the loaded instance list")
    parser.add_argument("--end", type=int, default=None, help="End index (exclusive) into the loaded instance list")
    parser.add_argument("--out", default=None, help="Results JSON output directory")
    parser.add_argument(
        "--cfg-options", nargs="+", action=DictAction,
        help="Override config options in key=value format",
    )
    args = parser.parse_args()
    if not args.task_ids and args.start is None and args.end is None:
        parser.error("specify --task-ids or --start/--end; refusing to run the entire benchmark by default")
    return args


async def run_agent(record: TaskRecord, ctx: SessionContext, sandbox):
    """Run MetaAgent on the task with tool execution routed into ``sandbox``.

    Binding the peer sandbox on ``ctx.extra["sandbox"]`` makes bash/file/git tools
    execute inside the per-task cleanroom container (it propagates to the agent and
    its sub-agents via BaseContext.from_context, which copies ``extra``). The agent
    brain itself runs where this process runs (the base container), so it keeps LLM
    network access while the task's shell work stays isolated in the peer.
    """
    ctx.extra["sandbox"] = sandbox
    response = await agent_manager(
        name="meta_agent",
        input={
            "task": record.task.content,
            "files": record.task.files,
        },
        ctx=ctx,
    )
    return response


async def extract_submission(sandbox, dest_dir: str) -> str:
    """Tar `/workspace` inside `sandbox` and pull it out to dest_dir/submission.tar.gz.

    Matches the official baseline's own copy_submission() approach, and produces
    exactly the file ProgramBenchmark._prepare_submission() expects for future scoring.

    The peer cleanroom is deliberately NOT bind-mounted (its /workspace is the task
    fixture — executable + docs + git — and mounting would destroy it and break the
    anti-cheat model), so this tar is the only channel by which the reconstructed
    codebase reaches the host. To keep the result inspectable without manual untarring,
    the tarball is also unpacked host-side into ``dest_dir/submission/``.

    Returns the local tarball path. Raises on failure — the caller treats this as
    best-effort.
    """
    tar_path = "/tmp/submission.tar.gz"
    # The reference copy the agent is told to make (see SYSTEM_PROMPT) is scaffolding
    # for differential testing, not deliverable source. It must not ride along in the
    # tarball: the submission would then ship the original binary, which the task
    # forbids depending on, and a `compile.sh` that quietly copied it into place would
    # score as a solved reconstruction. Excluded here rather than trusted to the
    # agent's housekeeping, because this tar takes the whole workspace regardless of
    # what was committed.
    result = await sandbox.run_command(
        f"tar -czf {tar_path} --exclude=./{REFERENCE_COPY} -C /workspace . 2>&1"
    )
    if not result.success:
        raise RuntimeError(f"tar failed: {result.as_message()}")
    data = await sandbox.read_bytes(tar_path)
    os.makedirs(dest_dir, exist_ok=True)
    local_path = os.path.join(dest_dir, "submission.tar.gz")
    with open(local_path, "wb") as f:
        f.write(data)

    # Unpack a browsable copy alongside the tarball so the reconstructed source tree
    # is directly visible on the host (the scorer still consumes submission.tar.gz).
    import tarfile
    extract_dir = os.path.join(dest_dir, "submission")
    os.makedirs(extract_dir, exist_ok=True)
    with tarfile.open(local_path, "r:gz") as tf:
        try:
            tf.extractall(extract_dir, filter="data")  # py3.12+: block path traversal / unsafe members
        except TypeError:
            tf.extractall(extract_dir)  # older Pythons without the filter kwarg
    logger.info(f"| 📂 Submission unpacked for inspection: {extract_dir}")
    return local_path


def warn_if_not_containerized() -> bool:
    """Say so when the MAS is running on the host rather than in the base container.

    Model X puts the whole framework inside a container; ``scripts/run-in-sandbox.sh``
    is how that happens, and it marks the environment with AGENTEVOLVER_HOST_ROOT.
    A bare ``python examples/run_programbench.py`` still produces valid submissions —
    bash and the file tools route into the peer cleanroom regardless — so the
    difference is invisible in the results and easy to not notice for a whole
    afternoon. Hence a line in the log rather than a silent divergence.

    Returns True when containerized, so callers can assert on it in tests.
    """
    containerized = os.path.exists("/.dockerenv") or bool(os.environ.get("AGENTEVOLVER_HOST_ROOT"))
    if not containerized:
        logger.warning(
            "| ⚠️ Running on the host, not in the base container. The task cleanroom is "
            "still isolated, so results are valid — but this is not Model X. To run the "
            "MAS inside the sandbox: scripts/run-in-sandbox.sh -- bash -c "
            "\"pip install -q programbench && python examples/run_programbench.py ...\""
        )
    return containerized


async def teardown():
    """Shut the subsystems down in run_meta_agent.py's order, and as forgivingly.

    Sandbox cleanup has to happen while the event loop and the opensandbox SDK
    executor are still alive — leaving it to the asyncio-atexit hooks fails at
    process exit ("Executor shutdown has been called") and leaks peer containers.
    Each step is guarded so one failure still lets the rest run; a leaked
    container is worse than a noisy log line. No environment_manager step:
    ProgramBench runs with `env_names = []` (see configs/programbench_agent.py).
    """
    for label, step in (
        ("task manager", task_manager.stop),
        ("sandbox cleanup", sandbox_manager.cleanup),
        ("trace manager", trace_manager.stop),
    ):
        try:
            await step()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ {label}: {e}")


async def main():
    args = parse_args()
    config.initialize(config_path=args.config, args=args)

    # Capture the shared component library before any session rebases config —
    # every per-task session below layers onto this same extension root.
    bootstrap_extension_root = config.extension_root

    # Sessions resolve through the path manager (P.SESSION -> output/<owner>/
    # sessions/<id>), exactly like run_meta_agent.py, so a run started here lands
    # where the gateway would put it and shows up in the browser's project list.
    # Deliberately NOT derived from config.project_root: bind_session_roots()
    # repoints that at each task's sandbox, so task N+1 would otherwise nest
    # inside task N.
    bootstrap_session_id = f"programbench_bootstrap_{make_id()}"
    bootstrap_ctx = SessionContext(id=bootstrap_session_id, name="programbench_bootstrap")
    bootstrap_sandbox = ensure_session_sandbox(
        bootstrap_ctx, shared_extension_root=bootstrap_extension_root,
    )
    bind_session_roots(config, bootstrap_sandbox)
    bootstrap_log_root = config.log_root
    extension_manager.set_base_dir(bootstrap_extension_root)
    logger.initialize(config=config)
    warn_if_not_containerized()
    logger.info(f"| Config: {config.pretty_text}")

    from agentevolver.config import validate_assembly
    for _problem in validate_assembly(config):
        logger.warning(f"| ⚠️ Config: {_problem}")

    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()
    logger.info(f"| ✅ Versions: {await version_manager.list()}")

    await trace_manager.initialize()
    await trace_manager.start()

    await trajectory_manager.initialize()
    await hook_manager.initialize()

    logger.info("| 🧠 Initializing model manager...")
    await model_manager.initialize()
    logger.info(f"| ✅ Models: {model_manager.list()}")

    logger.info("| 📁 Initializing prompt manager...")
    await prompt_manager.initialize()

    logger.info("| 📁 Initializing memory manager...")
    await memory_manager.initialize(memory_names=config.memory_names)

    logger.info("| 🛠️  Initializing tools...")
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| ✅ Tools: {await tool_manager.list()}")

    logger.info("| 🎯 Initializing skills...")
    await skill_manager.initialize(skill_names=config.skill_names)
    logger.info(f"| ✅ Skills: {await skill_manager.list()}")

    logger.info("| 🔌 Initializing connectors...")
    await connector_manager.initialize(connector_names=config.connector_names)
    logger.info(f"| ✅ Connectors: {await connector_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    _ext_manifest = await extension_manager.initialize()
    logger.info(f"| ✅ Extensions loaded: {[f'{c.module}:{c.name}' for c in _ext_manifest.components]}")

    # TaskManager: one fixed log_root for the whole batch run (not rebased per
    # task — its bookkeeping covers every submitted task in one place).
    task_log_root = os.path.join(bootstrap_log_root, "tasks")
    await task_manager.initialize(log_root=task_log_root, handler=None)
    await task_manager.start(num_workers=1)

    logger.info("| 🧩 Initializing sandbox manager...")
    await sandbox_manager.initialize()

    # --- Load & select ProgramBench instances ---
    from agentevolver.data.programbench import ProgramBenchDataset
    dataset = ProgramBenchDataset()
    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()] if args.task_ids else None
    selected, selection_warnings = select_instances(
        dataset.data, task_ids=task_ids, start=args.start, end=args.end,
    )
    for w in selection_warnings:
        logger.warning(f"| ⚠️ {w}")
    logger.info(f"| 📋 ProgramBench: {len(selected)}/{len(dataset.data)} instance(s) selected")

    if not selected:
        logger.warning("| ⚠️ No instances selected — nothing to run.")
        await teardown()
        return

    # --- Run each selected instance sequentially, in its own session sandbox ---
    out_dir = args.out or os.path.join(bootstrap_log_root, "results", "programbench")
    os.makedirs(out_dir, exist_ok=True)
    results_path = os.path.join(out_dir, f"run_{bootstrap_session_id}.json")
    results = []

    for i, instance in enumerate(selected, start=1):
        instance_id = instance["instance_id"]
        image_ref = f"{instance.get('image_name', '')}:{IMAGE_TAG}"
        logger.info(f"| ▶️ [{i}/{len(selected)}] Starting ProgramBench task: {instance_id} (image={image_ref})")

        # The session id IS the instance id, so the run lands in
        # output/<owner>/sessions/<instance_id>/ and is findable by task name
        # instead of by an opaque hash. Re-running a task reuses its directory
        # (roots are mkdir(exist_ok=True)) and overwrites the previous
        # submission — copy anything you want to keep out first.
        session_id = instance_id
        ctx = SessionContext(id=session_id, name=f"programbench_{instance_id}")
        fs_sandbox = ensure_session_sandbox(
            ctx, shared_extension_root=bootstrap_extension_root,
        )
        bind_session_roots(config, fs_sandbox)
        logger.info(f"| 📂 [{instance_id}] Session root: {fs_sandbox.project_root}")

        content = build_task_content(instance)
        t0 = time.time()
        status_value = "failed"
        error_message = None
        submission_path = None
        docker_sandbox = None
        try:
            # network=False -> egress denied entirely (agentevolver/sandbox/default/
            # base.py wires this to opensandbox's NetworkPolicy(default_action="deny")).
            # Matches the official baseline's --network none: the anti-cheat mechanism
            # that stops the agent from downloading the original source instead of
            # reverse-engineering it, not optional hardening — see
            # SANDBOX_ISOLATE_NETWORK's own comment for why it currently defaults to
            # off (network allowed) on this host.
            docker_sandbox = await sandbox_manager.acquire(
                "opensandbox",
                image=image_ref,
                network=not SANDBOX_ISOLATE_NETWORK,
                reuse_key=instance_id,
                timeout_minutes=180,
                domain=SANDBOX_DOMAIN,
            )
            task_manager.set_handler(lambda record, _ctx=ctx, _sb=docker_sandbox: run_agent(record, _ctx, _sb))

            task_id = await task_manager.submit(
                content=content,
                category=TaskCategory.USER,
                priority=TaskPriority.HIGH,
                metadata={
                    "instance_id": instance_id,
                    "repository": instance.get("repository", ""),
                    "language": instance.get("language", ""),
                    "image_name": instance.get("image_name", ""),
                },
                session_id=session_id,
            )
            while True:
                record = await task_manager.get(task_id)
                if record and record.task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    break
                await asyncio.sleep(1)
            status_value = record.task.status.value
            error_message = record.error

            # Pull the reconstructed codebase out before the container is destroyed —
            # bash_tool ran entirely inside it once a sandbox was bound (see design
            # spec §8), so nothing landed in fs_sandbox.workspace_root on the host.
            try:
                submission_path = await extract_submission(docker_sandbox, str(fs_sandbox.workspace_root))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"| ⚠️ [{instance_id}] submission extraction failed: {e}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{instance_id}] runner-level failure: {e}", exc_info=True)
            error_message = str(e)
        finally:
            if docker_sandbox is not None:
                await sandbox_manager.release("opensandbox", reuse_key=instance_id)
        elapsed = time.time() - t0

        mark = "✅" if status_value == "done" else "❌"
        logger.info(f"| {mark} [{i}/{len(selected)}] {instance_id} finished as {status_value} in {elapsed:.1f}s")

        results.append({
            "instance_id": instance_id,
            "status": status_value,
            "time_seconds": round(elapsed, 1),
            "session_id": session_id,
            "session_path": str(fs_sandbox.project_root),
            "workspace_path": str(fs_sandbox.workspace_root),
            "submission_path": submission_path,
            "error": error_message,
        })

        # Write after every task so a mid-run crash still leaves a usable partial file.
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump({
                "config": args.config,
                "selection": {"task_ids": task_ids, "start": args.start, "end": args.end},
                "records": results,
            }, f, ensure_ascii=False, indent=2)

    done_count = sum(1 for r in results if r["status"] == "done")
    print(f"\n✅ ProgramBench run done: {done_count}/{len(results)} task(s) completed")
    print(f"   Results: {results_path}")
    for r in results:
        print(f"   {'✅' if r['status'] == 'done' else '❌'} {r['instance_id']:45} {r['session_path']}")

    await teardown()


if __name__ == "__main__":
    asyncio.run(main())
