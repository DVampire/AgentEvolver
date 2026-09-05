"""Run AgentEvolver on SWE-bench Pro — the same launcher shape as run_programbench.py.

A launcher on the host runs one MetaAgent process per instance; repository commands run
in its task container. The final edits are frozen as a git patch and scored with the
SWE Pro grading scripts. Two arms
(configs/swebench_pro_agent.py and .._baseline.py) differ only in the evolution roster.

Key differences from ProgramBench, all deliberate:

- **The repo lives at ``/app``** in the instance image (``WORKDIR /app; git clone; git
  checkout <base_commit>``). The session workspace is seeded from that ``/app`` on the
  host, then mounted into the agent's container at ``/workspace`` — the framework's
  own workspace root, where the bash tool defaults its cwd. The agent works
  at ``/workspace`` and the patch is ``git diff`` there against the base commit. Only the
  grader's throwaway peer sandbox uses the image-native ``/app`` (see ``_build_entryscript``).

- **GT-SAFETY by field filtering.** The HuggingFace row carries the answer key —
  ``patch`` (gold fix), ``test_patch``, ``fail_to_pass``, ``pass_to_pass``,
  ``selected_test_files_to_run``. Only ``problem_statement`` / ``requirements`` /
  ``interface`` (plus repo/base_commit for setup) are ever handed to the container;
  ``_safe_instance`` strips the rest. The oracle stays on the host, where the grader runs.

- **Final-only grading.** The agent verifies locally. After its process and task sandbox
  stop, the host freezes one patch and grades it in a throwaway peer sandbox. No scores
  return to the agent. Resume after a grader failure reuses that exact frozen submission.

- **Grader assets** — the per-instance ``run_script.sh`` / ``parser.py`` and the dockerfiles
  (for their ENV) live in the cloned checkout (``--grader-repo``, default
  ``others/SWE-bench_Pro``); the entryscript mirrors the official ``create_entryscript``.

Modes: ``launcher`` (default, host) selects instances and owns final grading; ``inner``
(``--instance-json``) is the host-side Agent worker bound to its task container.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import os
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from agentevolver.benchmark.default.swebench import SWEBenchProBenchmark
from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager

# Reuse the proven, benchmark-agnostic host machinery from the ProgramBench launcher rather
# than duplicate it. Importing is safe: its main() is guarded by __main__, and module-level
# code only computes paths/constants.
from examples.run_programbench import (  # noqa: E402
    _LANDING_MARGIN_SECONDS,
    CONTAINER_USER,
    CONTAINER_WORKSPACE,
    _summarise_spend,
    bind_task_workspace,
    check_shared_roots_readable,
    grant_to_container_user,
    host_repo_root,
    restore_ownership,
)

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

#: Hidden grading is never an agent tool in this protocol.
EVALUATION_PROTOCOL = "swe-pro-final-only-v2"
_GRADE_TIMEOUT_SECONDS = 1800
_GRADER_WORKERS = 2

#: Seeding copies a whole checkout out of the image and then garbage-collects its history.
#: 900s was not enough for the largest repos in the set.
_SEED_TIMEOUT_SECONDS = 2700
_PULL_TIMEOUT_SECONDS = 2700
_PULL_ATTEMPTS = 4

# The model/runtime stays in the known ``agentos`` host environment. Only repository
# commands execute in the task image, so an Alpine/musl image never has to load a
# glibc-linked host Python.
_EXEC_CONTAINER_ENV = "AGENTEVOLVER_EXEC_CONTAINER"
_EXEC_WORKDIR_ENV = "AGENTEVOLVER_EXEC_WORKDIR"
_TASK_WORKSPACE_ENV = "AGENTEVOLVER_TASK_WORKSPACE"

#: The ONLY instance fields that may enter the container. Everything else in the row is the
#: answer key or host-side scoring metadata and is withheld. Order fixes the task-doc slots.
#: Derived from the benchmark class rather than restated here. Which fields may
#: enter a container IS the GT-safety design, and a second copy of that list is a
#: second thing to keep in step.
_SAFE_FIELDS = SWEBenchProBenchmark.model_fields["safe_fields"].default

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
def _benchmark():
    """The one loader for this dataset, shared with the benchmark registry.

    This used to call `load_dataset` here, which put the rows in the HuggingFace cache
    instead of under `datasets/` where every other benchmark keeps its data — and stated
    a second time, in this file, which fields an agent may see. That list is the whole
    GT-safety design, and a copy of it is a copy that can drift.
    """
    return SWEBenchProBenchmark(base_dir="output/benchmark/swebench_pro")


async def load_instances() -> list:
    """Load the SWE-bench Pro split as a list of dict rows (full, incl. oracle).

    Awaited rather than wrapping `asyncio.run`: the only caller is `run_launcher`, which
    is already inside the loop `main()` opened, and a nested `asyncio.run` raises there.
    """
    benchmark = _benchmark()
    await benchmark.initialize()
    return benchmark.instances()


def _safe_instance(row: dict) -> dict:
    """The subset of a row the container may see — never the gold patch or the tests."""
    return {key: row.get(key, "") for key in _SAFE_FIELDS}


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


def _argv_safe_run_script(script: str) -> str:
    """Make the common grader wrapper consume the argv we already parsed.

    Some generated scripts also treat a comma in their *first argument* as a legacy list
    separator. A real Go subtest name can itself contain a comma, so that heuristic changes
    the test identity and silently executes nothing. The dataset field is already a list;
    preserve its elements exactly.
    """
    legacy = '''if [[ "$1" == *","* ]]; then
    IFS=',' read -r -a TEST_FILES <<< "$1"
else
    TEST_FILES=("$@")
fi'''
    script = script.replace(legacy, 'TEST_FILES=("$@")')
    # ansible-test chooses its own xdist worker count, ignoring CPU quotas on some
    # images. Bound that inner parallelism independently from benchmark concurrency.
    return re.sub(r"(\bansible-test\s+units\b)(?![^\n]*--num-workers)",
                  rf"\1 --num-workers {_GRADER_WORKERS}", script)


def _restore_test_fixtures(repo: str, base: str, before: str) -> list[str]:
    """Restore missing added Go test data in the grader only, never reference code.

    Self-contained because this function also runs in the isolated grading image.
    Existing files, executable files, symlinks and production directories are excluded.
    """
    import os
    import re
    import shlex
    import subprocess
    from pathlib import Path, PurePosixPath

    command = shlex.split(before)
    if len(command) < 5 or command[:2] != ["git", "checkout"] or command[3] != "--":
        return []
    revision = command[2]
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
        return []
    roots = sorted({str(PurePosixPath(p).parent / "testdata") for p in command[4:]
                    if p.endswith("_test.go") and not PurePosixPath(p).is_absolute()
                    and ".." not in PurePosixPath(p).parts})
    if not roots:
        return []

    def git(*args):
        return subprocess.check_output(["git", "-C", repo, *args])

    paths = git("diff", "--name-only", "-z", "--diff-filter=A", base, revision, "--", *roots)
    restored = []
    allowed = {".json", ".yaml", ".yml", ".txt", ".csv", ".tsv", ".xml", ".golden"}
    for raw in paths.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        relative = PurePosixPath(path)
        target = Path(repo) / path
        if relative.is_absolute() or ".." in relative.parts or relative.suffix not in allowed:
            continue
        if os.path.lexists(target) or any(p.is_symlink() for p in target.parents):
            continue
        entry = git("ls-tree", revision, "--", path)
        if not entry.startswith(b"100644 blob "):
            continue
        git("restore", "--source", revision, "--worktree", "--", path)
        restored.append(path)
    return restored


def _parser_with_fail_boundaries(script: str) -> str:
    """Keep Jest FAIL sections from being attributed to the preceding PASS file.

    A number of shipped parsers use PASS as the only file boundary. Jest prints a FAIL
    header in the same position, and a failed suite may still contain hundreds of passing
    tests. Parsing both headers preserves those passing results while the failed assertion
    remains absent/failed as before.
    """
    script = script.replace(
        'if line.startswith("PASS"):',
        'if line.startswith(("PASS", "FAIL")):',
    )
    return script.replace(
        'not lines[i].strip().startswith("PASS")',
        'not lines[i].strip().startswith(("PASS", "FAIL"))',
    )


def _build_entryscript(row: dict, grader_repo: str, *, profile: str = "diagnostic") -> str:
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
    test_files = shlex.join(str(item) for item in _as_list(row, "selected_test_files_to_run"))
    base = row.get("base_commit", "")
    if profile == "official":
        # Preserve upstream invocation semantics, including its comma-joined selector.
        env = "\n".join(env_cmds)
        selected = ",".join(str(item) for item in _as_list(row, "selected_test_files_to_run"))
        return f"""
{env}
# apply patch
cd /app
git reset --hard {base}
git checkout {base}
git apply -v /workspace/patch.diff
{before}
# run test and save stdout and stderr to separate files
bash /workspace/run_script.sh {selected} > /workspace/stdout.log 2> /workspace/stderr.log
# run parsing script
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""
    return "\n".join(env_cmds) + f"""
set -e
stage=setup
test_exit=0
parser_exit=0
write_status() {{
    code=$?
    printf '{{"stage":"%s","exit_code":%s,"test_exit":%s,"parser_exit":%s}}' "$stage" "$code" "$test_exit" "$parser_exit" > /workspace/status.json
}}
trap write_status EXIT
export PYTEST_XDIST_AUTO_NUM_WORKERS={_GRADER_WORKERS}
export GOMAXPROCS={_GRADER_WORKERS}
export GOFLAGS="${{GOFLAGS:+$GOFLAGS }}-p={_GRADER_WORKERS}"
cd /app
git config --global --add safe.directory /app
git reset --hard {shlex.quote(base)}
git checkout {shlex.quote(base)}
stage=patch
git apply -v /workspace/patch.diff
stage=tests_restore
{before}
stage=fixtures_restore
python /workspace/restore_fixtures.py
stage=tests
bash /workspace/run_script.sh {test_files} > /workspace/stdout.log 2> /workspace/stderr.log || test_exit=$?
stage=parser
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json || parser_exit=$?
stage=complete
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


#: Where a grading round leaves its own evidence, beside the bridge's request/response.
_GRADER_EVIDENCE = ("stdout.log", "stderr.log", "output.json", "status.json", "fixtures.json")


async def _save_grader_evidence(sbx, workspace_dir: str, run) -> dict:
    """Copy the grading container's own logs out before it is destroyed.

    The container is thrown away at the end of the round, so anything not copied here is
    gone. Failures are swallowed: evidence is what explains a score, never what decides it.
    """
    evidence = {"entry.log": (getattr(run, "stdout", "") or "") + (getattr(run, "stderr", "") or "")}
    for name in _GRADER_EVIDENCE:
        with contextlib.suppress(Exception):
            evidence[name] = await sbx.read_file(f"/workspace/{name}")
    dest = os.path.join(workspace_dir, os.pardir, "eval_bridge")
    with contextlib.suppress(Exception):
        os.makedirs(dest, exist_ok=True)
        numbers = [int(m.group(1)) for name in os.listdir(dest)
                   if (m := re.fullmatch(r"grader-(\d+)\.entry\.log", name))]
        seq = max(numbers, default=-1) + 1
        for name, data in evidence.items():
            with contextlib.suppress(Exception):
                with open(os.path.join(dest, f"grader-{seq}.{name}"), "w", encoding="utf-8") as h:
                    h.write(data if isinstance(data, str) else str(data))
    return evidence


def _grade_report(results, row: dict, evidence: dict, *, profile: str = "diagnostic") -> dict:
    """Classify this invocation from its own evidence, not another round's log tail.

    Failed assertions stay benchmark failures. A partial/empty parser result is not a
    valid score when setup, dependencies or the test runner prevented target execution.
    All messages crossing the bridge are generic: test identities stay in host logs.
    """
    counts = _counts(results, _as_list(row, "fail_to_pass"), _as_list(row, "pass_to_pass"))
    tests = results if isinstance(results, list) else results.get("tests", [])
    try:
        status = json.loads(evidence.get("status.json", "{}"))
        fixtures = json.loads(evidence.get("fixtures.json", "[]"))
    except (TypeError, ValueError):
        status, fixtures = {}, []
    text = "\n".join(str(evidence.get(name, "")) for name in ("stdout.log", "stderr.log"))
    error = None
    if status.get("exit_code") and status.get("stage") != "complete":
        error = (f"{status.get('stage', 'setup')}_failed", "the grader stopped before completing its test and parser stages")
    elif status.get("parser_exit"):
        error = ("parser_failed", "the test result parser exited unsuccessfully")
    elif ("INTERNALERROR>" in text or (not tests and re.search(
            r"Too many open files|can't start new thread|Resource temporarily unavailable", text))):
        error = ("test_runner_failed", "the test runner failed internally or exhausted host resources")
    elif not tests and re.search(r"(?:testdata|fixtures?)/[^\n]*[Nn]o such file or directory", text):
        error = ("test_fixture_missing", "required test data is missing in the grading checkout")
    elif ("[build failed]" in text or (not tests and re.search(
            r"undefined[: ]|unknown field |cannot find package|ModuleNotFoundError:|ImportError:", text))):
        error = ("test_build_failed", "test compilation or imports failed; patch correctness and test compatibility need separate review")
    else:
        executed = bool(tests) or bool(re.search(
            r"^=== RUN\s|^--- (?:PASS|FAIL):|\b\d+ (?:passed|failed)\b|[✓✕✔✖]", text, re.M))
        if not executed:
            code = "test_selection_failed" if "no tests to run" in text else "no_tests_executed"
            error = (code, "no test execution was observed in the grader output")
        elif not tests:
            error = ("test_results_missing", "tests ran but the parser produced no test results")
    if error:
        counts.update(error_code=error[0], error_details=error[1], resolved=False)
        # A failed test build does not establish an infrastructure fault: the patch may
        # omit an interface required by tests. Keep it unscored and explicitly uncertain.
        counts["failure_kind"] = (
            "test_compatibility" if error[0] == "test_build_failed" else
            "grading_setup" if error[0] in {
                "test_fixture_missing", "parser_failed", "test_selection_failed",
                "no_tests_executed", "test_results_missing",
            } else "evaluation"
        )
    elif not counts["resolved"]:
        counts["failure_kind"] = "test_failure"
    if not counts["resolved"] and re.search(
            r"(?:testdata|fixtures?)/[^\n]*[Nn]o such file or directory", text):
        # Negative tests can intentionally reference absent files. Preserve a diagnostic
        # hint, not an automatic fixture repair or a change to the official score.
        counts["diagnostic_notes"] = [
            "Missing fixture paths appear in the test log; check whether expected by a "
            "negative test or required by a failing test before attributing the failure."
        ]
    if fixtures:
        counts.update(fixture_files_restored=len(fixtures), leaderboard_comparable=False)
    counts.update(grader_protocol=f"swe-pro-{profile}-v1", grader_profile=profile)
    if profile == "diagnostic":
        counts["leaderboard_comparable"] = False
    return counts


async def _grade_once(workspace_dir: str, row: dict, grader_repo: str, *, patch: str,
                      profile: str = "official") -> dict:
    """Grade a frozen submission; never collect a mutable workspace or call an agent."""
    from agentevolver.sandbox import sandbox_manager

    instance_id = row["instance_id"]
    patch = strip_binary_hunks(patch)
    if not patch.strip():
        return {**_counts([], _as_list(row, "fail_to_pass"), _as_list(row, "pass_to_pass")),
                "resolved": False, "grader_note": "empty submission"}
    try:
        run_script = _load_script(grader_repo, instance_id, "run_script.sh")
        parser_py = _load_script(grader_repo, instance_id, "parser.py")
        if profile == "diagnostic":
            run_script = _argv_safe_run_script(run_script)
            parser_py = _parser_with_fail_boundaries(parser_py)
        elif profile != "official":
            raise ValueError(f"unknown grader profile: {profile}")
        entryscript = _build_entryscript(row, grader_repo, profile=profile)
        before = row.get("before_repo_set_cmd", "").strip().split("\n")[-1]
        fixture_script = (
            "from __future__ import annotations\n" + inspect.getsource(_restore_test_fixtures)
            + "\nimport json\n"
            + f"restored = _restore_test_fixtures('/app', {row.get('base_commit', '')!r}, {before!r})\n"
            + "with open('/workspace/fixtures.json', 'w') as f:\n    json.dump(restored, f)\n"
        ) if profile == "diagnostic" else ""
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
                           ("/workspace/restore_fixtures.py", fixture_script),
                           ("/workspace/entryscript.sh", entryscript)):
            await sbx.write_file(path, data)
        run = await sbx.run_command("bash /workspace/entryscript.sh",
                                    timeout=_GRADE_TIMEOUT_SECONDS)
        # Keep the grader's own evidence beside the counts. Without it a 0/N result cannot
        # be told apart from a patch that never applied, a build that never compiled, and a
        # test selector that matched nothing — three different failures that look identical
        # from the counts alone. Setup is fail-fast; test failures still reach the parser.
        evidence = await _save_grader_evidence(sbx, workspace_dir, run)
        try:
            results = json.loads(evidence.get("output.json", ""))
        except (ValueError, TypeError):
            report = _grade_report({"tests": []}, row, evidence, profile=profile)
            if report.get("error_code") in {"no_tests_executed", "test_results_missing", None}:
                report.update(error_code="no_output_json", error_details="the test run produced no valid output.json", resolved=False)
            return report
        return _grade_report(results, row, evidence, profile=profile)
    except Exception as e:  # noqa: BLE001
        return {"error_code": "grade_failed", "error_details": str(e)}
    finally:
        if sbx is not None:
            with contextlib.suppress(Exception):
                await sandbox_manager.release("docker", reuse_key=reuse)


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
async def _provision_command(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Keep provisioning cancellable instead of leaving an uninterruptible worker thread."""
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        raise
    return subprocess.CompletedProcess(
        argv, process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace"),
    )


async def ensure_image(image_ref_str: str) -> None:
    """Ensure one task image is local, retrying transient registry failures."""
    present = await _provision_command(["docker", "image", "inspect", image_ref_str], 60)
    if present.returncode == 0:
        return

    last = ""
    for attempt in range(1, _PULL_ATTEMPTS + 1):
        try:
            pulled = await _provision_command(["docker", "pull", image_ref_str], _PULL_TIMEOUT_SECONDS)
            if pulled.returncode == 0:
                return
            last = (pulled.stderr or pulled.stdout).strip()
        except asyncio.TimeoutError as error:
            last = f"pull exceeded {_PULL_TIMEOUT_SECONDS}s: {error}"
        if attempt < _PULL_ATTEMPTS:
            await asyncio.sleep((2 ** attempt) + random.uniform(0.0, 1.0))
    raise RuntimeError(
        f"could not pull {image_ref_str} after {_PULL_ATTEMPTS} attempts: "
        f"{last[-1000:] or '(no Docker output)'}"
    )


async def seed_workspace_async(image_ref_str: str, base_commit: str, destination: str) -> None:
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
    await ensure_image(image_ref_str)
    container_name = "agentevolver-seed-" + hashlib.sha256(os.path.abspath(destination).encode()).hexdigest()[:20]
    quoted_base = shlex.quote(base_commit)
    reset = (
        f"git reset --hard {quoted_base}; "
        f"git checkout -B benchmark-base {quoted_base}; "
        "git remote | while IFS= read -r remote; do git remote remove \"$remote\"; done; "
        "git for-each-ref --format='%(refname)' | while IFS= read -r ref; do "
        "[ \"$ref\" = refs/heads/benchmark-base ] || git update-ref -d \"$ref\"; done; "
        "git reflog expire --expire=now --all; git gc --prune=now; "
    )
    script = (
        'set -eu; '
        'rm -rf /seed/..?* /seed/.[!.]* /seed/*; '
        'cp -a /app/. /seed/; cd /seed; '
        f'git config --global --add safe.directory /seed; {reset}'
        'chown -R 1000:1000 /seed'
    )
    # `cp -a` of a whole checkout plus a `git gc` runs long on a large repo and a busy
    # volume; navidrome hit the previous 900s and the instance was recorded failed without
    # ever running. The copy is bounded by disk, not by anything a shorter deadline makes
    # safer, so the deadline is the one thing here worth being generous about.
    try:
        await _provision_command(["docker", "rm", "-f", container_name], 60)
        result = await _provision_command(
            ["docker", "run", "--rm", "--name", container_name, "--entrypoint", "sh",
             "-v", f"{destination}:/seed", image_ref_str, "-c", script],
            _SEED_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as e:
        # Identify the setup stage rather than reporting an unlabelled timeout.
        raise RuntimeError(
            f"seeding the workspace from {image_ref_str} exceeded "
            f"{_SEED_TIMEOUT_SECONDS}s (copy + reset + gc of the checkout)."
        ) from e
    finally:
        # Killing the Docker CLI alone does not stop its daemon-owned container.
        with contextlib.suppress(Exception):
            await _provision_command(["docker", "rm", "-f", container_name], 60)
    if result.returncode != 0:
        raise RuntimeError(
            f"could not seed the workspace from {image_ref_str}: "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


def has_resumable_workspace(workspace_dir: str, base_commit: str) -> bool:
    """Whether ``workspace_dir`` is a usable checkout for an interrupted instance.

    A resume is intentionally coarse-grained: completed instances are skipped from the
    durable run ledger, while an in-flight instance reuses its checkout and starts a fresh
    agent turn over the existing edits.  Requiring both a valid HEAD and the benchmark base
    object avoids building on a half-copied or corrupted seed after a host failure.
    """
    git_dir = os.path.join(workspace_dir, ".git")
    if not base_commit or not os.path.isdir(git_dir):
        return False

    def _git(*items):
        return subprocess.run(
            ["git", f"--git-dir={git_dir}", f"--work-tree={workspace_dir}", *items],
            capture_output=True, text=True, timeout=30,
        )

    try:
        head = _git("rev-parse", "--verify", "HEAD^{commit}")
        base = _git("cat-file", "-e", f"{base_commit}^{{commit}}")
    except (OSError, subprocess.TimeoutExpired):
        return False
    return head.returncode == 0 and base.returncode == 0


def load_run_results(results_path: str) -> list[dict]:
    """Load the durable per-instance ledger used by ``--resume``."""
    if not os.path.isfile(results_path):
        return []
    with open(results_path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"invalid SWE-bench Pro results ledger: {results_path}")
    return value


def scored_instance_ids(records: list[dict]) -> set[str]:
    """Instances whose official final grade was already recorded.

    Resolved and unresolved scores are both terminal. Re-running only the unresolved ones
    would turn crash recovery into hidden-test retrying and invalidate the experiment.
    """
    completed = set()
    for record in records:
        grade = record.get("final_grade")
        if isinstance(grade, dict) and not grade.get("error_code"):
            instance_id = record.get("instance_id")
            if instance_id:
                completed.add(str(instance_id))
    return completed


def atomic_write_json(path: str, value) -> None:
    """Replace a JSON checkpoint atomically so interruption cannot truncate it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load_submission(path: str, instance_id: str, base_commit: str) -> dict:
    """A resumed grading job must use the same artifact, never launch another solution."""
    with open(path, encoding="utf-8") as handle:
        submission = json.load(handle)
    if (submission.get("protocol") != EVALUATION_PROTOCOL
            or submission.get("instance_id") != instance_id
            or submission.get("base_commit") != base_commit
            or not isinstance(submission.get("patch"), str)
            or not isinstance(submission.get("agent_result"), dict)
            or submission.get("sha256") != hashlib.sha256(submission["patch"].encode()).hexdigest()):
        raise ValueError(f"invalid frozen submission: {path}")
    return submission


def freeze_submission(path: str, workspace: str, row: dict, record: dict) -> dict:
    if os.path.exists(path):
        return load_submission(path, row["instance_id"], row["base_commit"])
    patch = collect_patch(workspace, row["base_commit"])
    submission = dict(protocol=EVALUATION_PROTOCOL, instance_id=row["instance_id"],
                      base_commit=row["base_commit"], patch=patch,
                      sha256=hashlib.sha256(patch.encode()).hexdigest(), agent_result=record)
    atomic_write_json(path, submission)
    return submission


def solver_environment(env: dict) -> dict:
    """Do not inherit an evaluator bridge from a parent shell or another benchmark."""
    return {key: value for key, value in env.items()
            if key not in {"AGENTEVOLVER_EVAL_BRIDGE", "AGENTEVOLVER_EVAL_BUDGET"}}


def validate_run_protocol(prior_state: dict, *, resume: bool, existing: bool) -> None:
    if existing and not resume:
        raise ValueError("existing run is preserved; use --resume or a new --out directory")
    if existing and prior_state.get("evaluation_protocol") != EVALUATION_PROTOCOL:
        raise ValueError("legacy feedback-based run is preserved; use a new --out and output_owner")


def grader_fingerprint(grader_repo: str) -> str:
    """Bind resume to the actual local grader assets, including uncommitted changes."""
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(grader_repo):
        dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
        for name in sorted(files):
            if name != "Dockerfile" and not name.endswith((".py", ".sh")):
                continue
            path = path_manager.resolve_under(directory, name)
            digest.update(os.path.relpath(path, grader_repo).encode() + b"\0")
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def isolated_extensions(owner: str, *, resume: bool) -> str:
    path = str(path_manager.under(path_manager.get(P.OWNER, owner=owner), P.PROJECT_EXTENSION))
    if not resume and os.path.isdir(path) and os.listdir(path):
        raise ValueError("fresh experiment requires an empty run extension directory")
    return path


def pending_submission(path: str, receipt: str, row: dict, previous: dict) -> dict | None:
    if os.path.isfile(path):
        submission = load_submission(path, row["instance_id"], row["base_commit"])
        if os.path.isfile(receipt):
            with open(receipt, encoding="utf-8") as handle:
                if json.load(handle).get("sha256") != submission["sha256"]:
                    raise ValueError("frozen submission does not match its grading receipt")
        return submission
    if os.path.exists(receipt) or previous.get("final_grade") is not None or previous.get("submission_sha256"):
        raise ValueError("frozen submission is missing; refusing to restart an already submitted Agent")
    return None


# --------------------------------------------------------------------------- #
# Inner run (inside the container)
# --------------------------------------------------------------------------- #
async def run_inner(args) -> int:
    """Run the MetaAgent on the host with repository tools bound to its task container."""
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
    for key in ("AGENTEVOLVER_EVAL_BRIDGE", "AGENTEVOLVER_EVAL_BUDGET"):
        os.environ.pop(key, None)
    if "swebench_pro_eval_tool" in config.tool_names:
        raise ValueError("SWE Pro requires local verification only; remove swebench_pro_eval_tool")

    shared_extension_root = config.extension_root
    ctx = SessionContext(id=instance_id, name=f"swebench_pro_{instance_id}")
    fs_sandbox = ensure_session_sandbox(
        ctx,
        owner=configured_session_owner(config),
        shared_extension_root=shared_extension_root,
    )
    bind_session_roots(config, fs_sandbox)
    bind_task_workspace(
        ctx,
        fs_sandbox,
        os.environ.get(_TASK_WORKSPACE_ENV, CONTAINER_WORKSPACE),
    )

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

    if getattr(args, "resume", False):
        content = (
            "## RESUMING an interrupted attempt — preserve useful work\n\n"
            "The repository may contain source edits and commits from an earlier process. "
            "Inspect `git status`, `git log`, and the current diff before changing anything. "
            "Continue from useful work, repair incomplete edits, run the repository tests, "
            "and do not reset or rewrite the solution merely because this is a new process.\n\n"
            "The original task contract follows.\n\n---\n\n" + content
        )

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
        patch = collect_patch(os.environ.get(_TASK_WORKSPACE_ENV, CONTAINER_WORKSPACE),
                              instance.get("base_commit", ""))
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


def inner_argv(row: dict, args, resume: bool = False) -> list[str]:
    """Host-runtime argv for one isolated Agent process — SAFE row fields only."""
    payload = json.dumps(_safe_instance(row))
    parts = [
        sys.executable,
        os.path.join(root, "examples", "run_swebench_pro.py"),
        "--instance-json", payload,
        "--config", os.path.abspath(args.config),
        "--task-file", os.path.abspath(args.task_file),
    ]
    if resume:
        parts.append("--resume")
    overrides = getattr(args, "user_cfg_options", None) or {}
    if overrides:
        parts.append("--cfg-options")
        parts.extend(f"{key}={value}" for key, value in overrides.items())
    return parts


def inner_command(row: dict, args, resume: bool = False) -> str:
    """Printable form of :func:`inner_argv`; never executed through a host shell."""
    return shlex.join(inner_argv(row, args, resume=resume))


async def run_inner_process(row: dict, args, env: dict, resume: bool, timeout: int) -> int:
    """Run one Agent brain in its own host process and bound its whole process group."""
    process = await asyncio.create_subprocess_exec(
        *inner_argv(row, args, resume=resume),
        env=env,
        start_new_session=True,
    )
    async def stop() -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        await stop()
        return 124
    except asyncio.CancelledError:
        await stop()
        raise


# --------------------------------------------------------------------------- #
# Launcher (host) — one container per instance
# --------------------------------------------------------------------------- #
async def run_launcher(args) -> int:
    from agentevolver.sandbox import sandbox_manager
    from agentevolver.visual import BenchmarkMonitor

    check_shared_roots_readable()

    if getattr(args, "resume", False) and not args.out:
        logger.error("| ❌ --resume requires a stable --out directory")
        return 1

    out_dir = args.out or str(path_manager.under(
        config.project_root,
        P.PROJECT_RESULT_RUN,
        run_id=time.strftime("run_%Y%m%d_%H%M%S"),
    ))
    os.makedirs(out_dir, exist_ok=True)
    state_path = path_manager.resolve_under(out_dir, "run_state.json")
    results_path = path_manager.resolve_under(out_dir, "results.json")

    prior_state = {}
    if getattr(args, "resume", False) and os.path.isfile(state_path):
        with open(state_path, encoding="utf-8") as handle:
            prior_state = json.load(handle)
    validate_run_protocol(prior_state, resume=bool(args.resume),
                          existing=os.path.exists(state_path) or os.path.exists(results_path))

    # A benchmark instance may be run repeatedly with different models. Session state
    # includes memory, trajectories, prompts and grader counters, so keying it only by
    # instance_id can leak an earlier model's work into a later run. A fresh launcher gets
    # an isolated owner; a resumed launcher recovers the exact owner from run_state.json.
    overrides = dict(getattr(args, "user_cfg_options", None) or {})
    prior_owner = prior_state.get("output_owner")
    requested_owner = overrides.get("output_owner")
    if prior_owner and requested_owner and prior_owner != requested_owner:
        logger.error(
            f"| ❌ resume owner mismatch: state={prior_owner}, requested={requested_owner}"
        )
        return 1
    if prior_owner:
        overrides["output_owner"] = prior_owner
        args.user_cfg_options = overrides
        setattr(config, "output_owner", prior_owner)
        logger.info(f"| ⏩ Restored run namespace: {prior_owner}")
    elif not requested_owner:
        run_namespace = f"{config.get('tag') or 'swebench_pro'}_{time.strftime('%Y%m%d_%H%M%S')}"
        overrides["output_owner"] = run_namespace
        args.user_cfg_options = overrides
        setattr(config, "output_owner", run_namespace)
        logger.info(f"| 🧰 Isolated run namespace: {run_namespace}")

    owner = str(config.get("output_owner") or config.tag)
    extension_root = isolated_extensions(owner, resume=bool(prior_state))
    overrides["extension_root"] = extension_root
    args.user_cfg_options = overrides
    config.extension_root = extension_root
    os.environ["AGENTEVOLVER_EXTENSION_ROOT"] = extension_root
    grader_repo = os.path.abspath(args.grader_repo)
    if not os.path.isfile(os.path.join(grader_repo, "swe_bench_pro_eval.py")):
        raise ValueError(f"grader repo not found: {grader_repo}")

    run_state = {
        "evaluation_protocol": EVALUATION_PROTOCOL,
        "grader_profile": getattr(args, "grader_profile", "official"),
        "grader_fingerprint": grader_fingerprint(grader_repo),
        "extension_root": extension_root,
        "config": os.path.abspath(args.config),
        "task_ids": args.task_ids,
        "start": args.start,
        "end": args.end,
        "output_owner": str(config.get("output_owner") or config.tag),
    }
    if prior_state:
        for key in ("config", "task_ids", "start", "end", "extension_root", "grader_profile", "grader_fingerprint"):
            if prior_state.get(key) != run_state.get(key):
                logger.error(
                    f"| ❌ resume selection mismatch for {key}: "
                    f"state={prior_state.get(key)!r}, requested={run_state.get(key)!r}"
                )
                return 1
    if not prior_state:
        sessions = path_manager.resolve_under(
            path_manager.get(P.OWNER, owner=run_state["output_owner"]), "sessions")
        if os.path.isdir(sessions) and os.listdir(sessions):
            raise ValueError("output_owner already contains sessions; use a new owner for a fresh experiment")
    atomic_write_json(state_path, run_state)

    instances = await load_instances()
    task_ids = [t.strip() for t in args.task_ids.split(",")] if args.task_ids else None
    selected = select_instances(instances, task_ids=task_ids, start=args.start, end=args.end)
    if not selected:
        logger.error("| ❌ no instances selected")
        return 1
    selection_size = len(selected)

    results = load_run_results(results_path) if getattr(args, "resume", False) else []
    previous_records = {record.get("instance_id"): record for record in results}
    completed_ids = scored_instance_ids(results)
    pending = [(index, row) for index, row in enumerate(selected, start=1)
               if row["instance_id"] not in completed_ids]
    logger.info(
        f"| 📋 SWE-bench Pro: {selection_size} selected, {len(completed_ids)} already "
        f"scored, {len(pending)} pending, concurrency={args.concurrency}"
    )
    owner = str(config.get("output_owner") or config.tag)
    monitor = BenchmarkMonitor(
        out_dir,
        "swebench_pro",
        selection_size,
        args.concurrency,
        results_path=results_path,
        owner_dir=str(path_manager.get(P.OWNER, owner=owner)),
        title="SWE-bench Pro",
    )
    if not args.no_monitor:
        try:
            url = await monitor.deploy(args.monitor_port)
            logger.info(f"| 📊 Benchmark monitor: {url or 'deployment failed'}")
        except Exception as error:  # monitoring must never invalidate a benchmark
            logger.warning(f"| ⚠️ Benchmark monitor unavailable: {error}")
    if not pending:
        monitor.close()
        logger.info(f"| ✅ Nothing to resume; all {selection_size} instances are scored")
        return 0

    wall_clock = int(getattr(config, "WALL_CLOCK", 7200) or 7200)
    forwarded = {k: os.environ[k] for k in _FORWARDED_ENV if os.environ.get(k)}

    # The Agent brain now stays in the known host runtime; only repository commands run
    # inside the task image. Recover the shared library from a previously interrupted
    # container-hosted run before any host worker tries to update its manifest.
    restore_ownership(str(path_manager.get(P.EXTENSION)))

    results_lock = asyncio.Lock()
    provision_sem = asyncio.Semaphore(max(1, int(args.provision_concurrency or 1)))

    def _write_results():
        order = {row["instance_id"]: index for index, row in enumerate(selected)}
        results.sort(key=lambda item: order.get(item.get("instance_id"), selection_size))
        atomic_write_json(results_path, results)

    async def _run_one_instance(index, row):
        instance_id = row["instance_id"]
        ref = image_ref(row)
        monitor.task(instance_id, "preparing", position=index)
        logger.info(f"| ▶️ [{index}/{len(selected)}] {instance_id} (image={ref})")
        started = time.time()
        previous = previous_records.get(instance_id, {})
        record: dict = {"instance_id": instance_id, "status": "failed", "error": None, **previous}
        sandbox = None
        workspace_dir = None
        attempt_completed = False
        try:
            owner = str(config.get("output_owner") or config.tag)
            workspace_dir = str(path_manager.get(
                P.SESSION_WORKSPACE, owner=owner, session_id=instance_id,
            ))
            session_path = str(path_manager.get(
                P.SESSION, owner=owner, session_id=instance_id,
            ))
            submission_path = str(path_manager.resolve_under(session_path, "submission.json"))
            receipt_path = str(path_manager.resolve_under(session_path, "submission_receipt.json"))
            submission = pending_submission(submission_path, receipt_path, row, previous)
            if submission is not None:
                async with provision_sem:
                    await ensure_image(ref)
                logger.info(f"| ⏩ [{instance_id}] Regrading the frozen submission; no Agent restart")
            else:
                resuming = bool(args.resume) and has_resumable_workspace(
                    workspace_dir, row.get("base_commit", ""))
                if resuming:
                    async with provision_sem:
                        await ensure_image(ref)
                    grant_to_container_user(workspace_dir)
                else:
                    async with provision_sem:
                        await seed_workspace_async(ref, row.get("base_commit", ""), workspace_dir)

                sandbox = await sandbox_manager.acquire(
                    "docker", reuse_key=instance_id, image=ref, network=False, user=CONTAINER_USER,
                    workdir=CONTAINER_WORKSPACE, mounts={workspace_dir: CONTAINER_WORKSPACE},
                    env={}, allow_hosts=[], deny_hosts=[],
                    timeout_minutes=(wall_clock + _LANDING_MARGIN_SECONDS) // 60,
                )
                monitor.task(instance_id, "solving", position=index)
                logger.info(f"| 🚀 [{instance_id}] {inner_command(row, args, resume=resuming)[:160]}")
                inner_env = solver_environment({
                    **os.environ, **forwarded,
                    _EXEC_CONTAINER_ENV: sandbox.container_name,
                    _EXEC_WORKDIR_ENV: CONTAINER_WORKSPACE,
                    _TASK_WORKSPACE_ENV: workspace_dir,
                    "AGENTEVOLVER_HOME": root,
                    "PYTHONPATH": root,
                    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                })
                exit_code = await run_inner_process(
                    row, args, inner_env, resume=resuming,
                    timeout=wall_clock + _LANDING_MARGIN_SECONDS,
                )
                inner_result = str(path_manager.under(session_path, P.PROJECT_RESULT))
                if os.path.isfile(inner_result):
                    with open(inner_result, encoding="utf-8") as handle:
                        record.update(json.load(handle))
                else:
                    record["error"] = f"the inner run left no result.json (process exit {exit_code})"
                record["egress_audit"] = sandbox_manager.egress_audit(sandbox)

                # Stop background repository commands before freezing the final artifact.
                await sandbox_manager.release("docker", reuse_key=instance_id)
                sandbox = None
                submission = freeze_submission(submission_path, workspace_dir, row, record)

            record = dict(submission["agent_result"])
            atomic_write_json(receipt_path, {"sha256": submission["sha256"]})
            record.update(evaluation_protocol=EVALUATION_PROTOCOL, grader_evals=0,
                          submission_sha256=submission["sha256"],
                          grader_profile=run_state["grader_profile"],
                          grader_fingerprint=run_state["grader_fingerprint"])
            if run_state["grader_profile"] != "official":
                record["leaderboard_comparable"] = False
            monitor.task(instance_id, "grading", position=index)
            logger.info(f"| ⚖️ [{instance_id}] final grade of frozen patch {submission['sha256']}")
            final = await _grade_once(workspace_dir, row, grader_repo, patch=submission["patch"],
                                      profile=run_state["grader_profile"])
            record["final_grade"] = final
            record["resolved"] = bool(final.get("resolved"))
            if final.get("leaderboard_comparable") is False:
                record["leaderboard_comparable"] = False
            if final.get("error_code"):
                record["error"] = f"final grader failed: {final['error_code']}"
            logger.info(f"| {'🟢 RESOLVED' if record['resolved'] else '🔴 unresolved'} "
                        f"[{instance_id}] {final}")
            attempt_completed = not final.get("error_code")
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{instance_id}] launcher failure: {e}", exc_info=True)
            record["error"] = str(e)
        finally:
            if sandbox is not None:
                await sandbox_manager.release("docker", reuse_key=instance_id)
            # Bounded disk on a near-full shared volume: each instance has its own ~1GB
            # image and a seeded repo workspace. Reclaim both once graded — result.json and
            # agent.patch live in the session root (not the workspace), so scores survive.
            # Cancellation is resumable: retain the Git workspace and session state.
            # A later ``--resume`` run can continue them after the sandbox is released.
            if attempt_completed and getattr(args, "reclaim_disk", True):
                await asyncio.to_thread(_reclaim_instance_disk, workspace_dir, ref)

        record.setdefault("time_seconds", round(time.time() - started, 1))
        logger.info(f"| {'✅' if record['status'] == 'done' else '❌'} [{index}/{len(selected)}] "
                    f"{instance_id} → {record['status']} in {record['time_seconds']}s")
        async with results_lock:
            results[:] = [item for item in results if item.get("instance_id") != instance_id]
            results.append(record)
            _write_results()
            monitor.finish_task(instance_id)
        return record

    sem = asyncio.Semaphore(max(1, int(args.concurrency or 1)))

    async def _bounded(index, row):
        async with sem:
            try:
                return await _run_one_instance(index, row)
            except Exception as e:  # noqa: BLE001
                logger.error(f"| ❌ [{row['instance_id']}] crashed: {e}")
                record = {"instance_id": row["instance_id"], "status": "failed", "error": str(e)}
                async with results_lock:
                    results[:] = [item for item in results if item.get("instance_id") != row["instance_id"]]
                    results.append(record)
                    _write_results()
                    monitor.finish_task(row["instance_id"])
                return record

    try:
        await asyncio.gather(*[_bounded(i, row) for i, row in pending])
    except BaseException:
        monitor.close("interrupted")
        raise

    # Two different numbers, and reporting only the first is how a run that never happened
    # reads as a run that did. `status == "done"` says the agent finished its turn;
    # `resolved` says the grader accepted the patch. An instance that never produced a score
    # — a seeding timeout, a crash — is a harness failure, not a benchmark answer, so it
    # leaves through a non-zero exit while an honestly unresolved instance does not.
    done = sum(1 for r in results if r.get("status") == "done")
    resolved = sum(1 for r in results if r.get("resolved"))
    scored = [r for r in results if isinstance(r.get("final_grade"), dict)
              and not r["final_grade"].get("error_code")]
    unscored = [r for r in results if r not in scored]
    logger.info(
        f"| ✅ SWE-bench Pro: {resolved}/{selection_size} resolved, "
        f"{len(scored)}/{selection_size} scored, {done}/{selection_size} ran. Results: {out_dir}"
    )
    for r in unscored:
        grade = r.get("final_grade") or {}
        logger.error(
            f"| ❌ {r.get('instance_id')} produced no score: "
            f"{grade.get('error_code') or r.get('error') or 'unknown'}"
        )
    monitor.close("completed" if not unscored else "completed_with_errors")
    return 1 if unscored else 0


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
    parser.add_argument("--grader-profile", choices=("official", "diagnostic"), default="official",
                        help="Official uses unchanged upstream scripts; diagnostic enables custom repairs and is non-comparable.")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--provision-concurrency",
        type=int,
        default=4,
        help="Maximum concurrent image pulls/workspace seeds, separate from Agent workers.",
    )
    parser.add_argument("--reclaim-disk", dest="reclaim_disk", action="store_true", default=True,
                        help="After each instance is graded, delete its seeded workspace and its "
                             "docker image to bound disk on a full volume (default on).")
    parser.add_argument("--no-reclaim-disk", dest="reclaim_disk", action="store_false",
                        help="Keep each instance's workspace and image (needs ~1GB per instance).")
    parser.add_argument("--out", default=None, help="Results output directory.")
    parser.add_argument("--no-monitor", action="store_true",
                        help="Do not deploy the live benchmark monitor (enabled by default).")
    parser.add_argument("--monitor-port", type=int, default=8765,
                        help="Preferred monitor port; deploy allocates another when busy.")
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume a durable run from --out: restore its output namespace, skip every "
             "instance with a recorded final score, regrade frozen submissions without "
             "restarting the Agent, or reuse a pre-submission interrupted workspace. "
             "Resolved and unresolved scores are both terminal.",
    )
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
    # as a list. It also mutates that mapping with ordinary launcher arguments, so keep a
    # separate copy: forwarding start/end/out host paths into the inner config previously
    # polluted every container invocation.
    args.cfg_options = dict(overrides)
    config.initialize(config_path=args.config, args=args)
    args.user_cfg_options = dict(overrides)
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
