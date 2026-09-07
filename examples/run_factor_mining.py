"""Prepare market data, check the pipeline without a model, or run factor research.

    python -m examples.run_factor_mining prepare --synthetic --dataset /tmp/market
    python -m examples.run_factor_mining check --dataset /tmp/market --out /tmp/check
    python -m examples.run_factor_mining run --dataset /path/to/market --out /path/to/study

Only the explicit ``run`` command starts agents. Agent initialization and the
outer factor/strategy iteration live here; all grading uses BenchmarkManager.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv=None):
    from mmengine import DictAction

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="Import OHLCV through data; never starts an agent")
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", help="CSV, Parquet file, or symbol-partitioned Parquet directory")
    source.add_argument("--hf-repo", help="HuggingFace OHLCV dataset; loaded through DataManager")
    source.add_argument("--synthetic", action="store_true", help="Create toy data, not real-market evidence")
    prepare.add_argument("--dataset", required=True, help="New output directory for train/valid/test bundle")
    prepare.add_argument("--frequency", default="1h")
    prepare.add_argument("--symbols", nargs="+")
    prepare.add_argument("--hf-split", default="train")
    prepare.add_argument("--train-fraction", type=float, default=.6)
    prepare.add_argument("--valid-fraction", type=float, default=.2)
    prepare.add_argument("--gap", type=int, default=5)
    prepare.add_argument("--seed", type=int, default=7)
    for name in ("check", "run"):
        item = sub.add_parser(name, help="Deterministic no-model integration check" if name == "check" else "Start isolated research agents")
        item.add_argument("--dataset", required=True)
        item.add_argument("--out", required=True)
        item.add_argument("--config", default=str(ROOT / "configs/factor_mining.py"))
        item.add_argument("--goal", choices=["factors", "strategy"], default="strategy")
        item.add_argument("--resume", action="store_true")
        item.add_argument("--cfg-options", nargs="+", action=DictAction)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--job", required=True)
    return parser.parse_args(argv)


def _save(path, value):
    from agentevolver.utils.file_utils import atomic_write_text

    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


async def prepare_data(args):
    from agentevolver.data import FactorMarketDataset

    if args.synthetic:
        panel = FactorMarketDataset.synthetic(seed=args.seed)
        source = {"kind": "synthetic", "seed": args.seed, "performance_claim": False}
    elif args.hf_repo:
        panel = await FactorMarketDataset.from_dataset(args.hf_repo, split=args.hf_split,
                    frequency=args.frequency, symbols=args.symbols)
        source = {"kind": "huggingface", "repo": args.hf_repo, "split": args.hf_split}
    else:
        panel = FactorMarketDataset.load(args.source, frequency=args.frequency, symbols=args.symbols)
        source = {"kind": "local", "path": str(Path(args.source).resolve())}
    manifest = FactorMarketDataset.bundle(panel, args.dataset, train=args.train_fraction,
                                          valid=args.valid_fraction, gap=args.gap)
    manifest["source"] = source
    _save(Path(args.dataset) / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


def choose_strategy(state):
    candidates = [(entry["passed"], entry["quality"], name)
                  for name, entry in state["strategies"].items() if entry["quality"] is not None]
    return max(candidates)[2] if candidates else None


def stopping_reason(state, *, goal, round_number, max_rounds, stagnant, no_progress_rounds):
    if goal == "factors" and state["factors"]:
        return "factor_target_met"
    if any(entry["passed"] for entry in state["strategies"].values()):
        return "strategy_target_met"
    if state["remaining_validations"] <= 0:
        return "validation_budget_exhausted"
    if stagnant >= no_progress_rounds:
        return "no_validation_improvement"
    if round_number >= max_rounds:
        return "round_budget_exhausted"
    return None


def worker_command(job_file, workspace, runtime, settings):
    """Expose only static runtime code, train workspace and worker logs to the process."""
    from agentevolver.sandbox.filesystem import isolated_command

    readonly = [str(ROOT / "agentevolver"), str(ROOT / "configs"), str(Path(__file__).resolve()),
                str(Path(sys.prefix).resolve())]
    private = Path(settings["dataset"]).resolve()
    authority = Path(settings["authority"]).resolve()
    for root in [*map(Path, readonly), Path(workspace), Path(runtime)]:
        if any(secret == root or root in secret.parents for secret in (private, authority)):
            raise ValueError("worker mount would expose private market data or grading state")
    # Credentials stay in the process environment; never serialized to jobs/logs/prompts.
    inherited = {key: value for key, value in os.environ.items()
                 if (key.startswith(("VAULT_", "LLM_", "OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_", "OPENROUTER_"))
                     or key in {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "TZ"})}
    inherited.update(AGENTEVOLVER_HOME=str(runtime), AGENTEVOLVER_EXTENSION_ROOT=str(Path(runtime) / "extension"),
                     HOME=str(Path(runtime) / "home"), PYTHONDONTWRITEBYTECODE="1",
                     PYTHONPATH=str(ROOT), TOKENIZERS_PARALLELISM="false")
    return isolated_command([sys.executable, "-u", str(Path(__file__).resolve()), "_worker", "--job", str(job_file)],
                             readonly=readonly, writable=[str(workspace), str(runtime)],
                             cwd=str(runtime), environment=inherited)


async def dispatch_agent(role, round_number, workspace, out, settings, state):
    runtime = out / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "home").mkdir(exist_ok=True)
    folder = runtime / f"round-{round_number}-{role}"
    folder.mkdir(exist_ok=True)
    snapshot = runtime / "config.json"
    _save(snapshot, settings["configuration"])
    job = {"role": role, "round": round_number, "workspace": str(workspace),
           "config": str(snapshot), "cfg_options": {},
           "output": str(folder / "response.json"), "state": state,
           "validation_allowance": min(state["remaining_validations"], 2 if role == "factor_mining_agent" else 1)}
    path = folder / "job.json"
    _save(path, job)
    command = worker_command(path, workspace, runtime, settings)
    with (folder / "worker.log").open("a") as log:
        process = await asyncio.create_subprocess_exec(*command, stdout=log, stderr=log, start_new_session=True)
        _save(folder / "process.json", {"pid": process.pid, "role": role, "round": round_number})
        try:
            code = await asyncio.wait_for(process.wait(), timeout=settings["worker_timeout"])
            if code:
                raise RuntimeError(f"{role} exited {code}; see {folder / 'worker.log'}")
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
    response = json.loads((folder / "response.json").read_text())
    if not response.get("success"):
        raise RuntimeError(f"{role} failed: {response.get('message', '')}")
    return response


async def worker(args):
    # This is the only function in the script that can initialize/call a model.
    from agentevolver.agent import AgentContext, agent_manager
    from agentevolver.config import config
    from agentevolver.connector import connector_manager
    from agentevolver.environment import environment_manager
    from agentevolver.hook import hook_manager
    from agentevolver.logger import logger
    from agentevolver.memory import memory_manager
    from agentevolver.model import model_manager
    from agentevolver.prompt import prompt_manager
    from agentevolver.session.context import bind_session_roots, ensure_session_sandbox
    from agentevolver.skill import skill_manager
    from agentevolver.tool import tool_manager
    from agentevolver.trace import trace_manager
    from agentevolver.trajectory import trajectory_manager
    from agentevolver.version import version_manager

    job = json.loads(Path(args.job).read_text())
    config.initialize(job["config"], argparse.Namespace(cfg_options=job["cfg_options"]), verbose=False)
    config.factor_mining_environment["workspace"] = job["workspace"]
    ctx = AgentContext(id=f"round-{job['round']}-{job['role']}", name=job["role"])
    sandbox = ensure_session_sandbox(ctx, shared_extension_root=config.extension_root)
    bind_session_roots(config, sandbox)
    logger.initialize(config=config)
    await version_manager.initialize()
    await trace_manager.initialize(log_root=str(Path(config.log_root) / "trace"))
    await trace_manager.start()
    await trajectory_manager.initialize()
    try:
        await hook_manager.initialize()
        await model_manager.initialize()
        await prompt_manager.initialize()
        await memory_manager.initialize(memory_names=[])
        await tool_manager.initialize(tool_names=["done_tool"])
        await skill_manager.initialize(skill_names=[])
        await connector_manager.initialize(connector_names=[])
        await environment_manager.initialize(env_names=["factor_mining"])
        await agent_manager.initialize(agent_names=[job["role"]])
        task = (f"Research round {job['round']}. You may spend at most {job['validation_allowance']} "
                "independent validation calls in this round. Use train freely. "
                "Inspect the authoritative library and prior diagnoses; discover factors and admit "
                "promising ones." if job["role"] == "factor_mining_agent" else
                f"Strategy round {job['round']}. Use at most {job['validation_allowance']} validation calls. "
                "Build from admitted factors, test on train, validate the strongest candidate, "
                "and record a diagnosis if the target remains unmet.")
        response = await agent_manager(job["role"], input={"task": task}, ctx=ctx)
        _save(job["output"], response.model_dump(mode="json"))
    finally:
        await environment_manager.cleanup()
        await agent_manager.cleanup()
        await trace_manager.stop()


async def no_model_check(env, goal):
    """Fixed candidate probes only, explicitly not an agent performance experiment."""
    factors = [{"name": "momentum", "expression": "delta(close, 1) / delay(close, 1)"},
               {"name": "reversal", "expression": "delta(close, 1) / delay(close, 1)", "direction": -1}]
    await env.run_factor_backtest(factors)
    await env.validate("factors", factors)
    state = await env.get_factor_library()
    if goal == "strategy" and state["factors"]:
        name = next(iter(state["factors"]))
        strategy = {"name": "check_strategy", "expression": name, "position_rule": "threshold"}
        await env.run_strategy_backtest(strategy)
        await env.validate("strategy", [strategy])
    return await env.get_factor_library()


async def research(args):
    from mmengine import Config

    from agentevolver.benchmark import BenchmarkManager
    from agentevolver.environment.default.factor_mining.environment import FactorMiningEnvironment
    from agentevolver.utils.file_utils import file_lock

    config_path = str(Path(args.config).resolve())
    configuration = Config.fromfile(config_path)
    configuration.merge_from_dict(args.cfg_options or {})
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "run.json").exists() and not args.resume:
        raise ValueError("output already contains a run; choose another directory or --resume")
    manager = BenchmarkManager()
    settings = {"configuration": configuration.to_dict(),
                "dataset": str(Path(args.dataset).resolve()), "authority": str(out / "benchmark/study"),
                "worker_timeout": configuration.get("worker_timeout", 1800)}
    if settings["worker_timeout"] <= 0:
        raise ValueError("worker_timeout must be positive")
    max_rounds = int(configuration.get("max_rounds", 3))
    no_progress = int(configuration.get("no_progress_rounds", 2))
    improvement = float(configuration.get("minimum_improvement", .05))
    if max_rounds < 1 or no_progress < 1 or improvement < 0:
        raise ValueError("invalid iteration budget")
    run = {"status": "running", "mode": args.command, "goal": args.goal, "rounds": [],
           "agents_enabled": args.command == "run"}
    async with file_lock(str(out / "run.lock")):
        try:
            await manager.configure("factor_mining", path=settings["dataset"],
                                    base_dir=str(out / "benchmark"), goal=args.goal,
                                    protocol=dict(configuration.research_protocol), resume=args.resume)
            task = await manager.reset("factor_mining", resume=args.resume)
            stats = await manager.stats("factor_mining")
            if stats.scored:
                print(json.dumps({"status": "already_finished", "stats": stats.model_dump()}, indent=2))
                return
            prepared = await manager.prepare("factor_mining", task)
            if prepared.submission is not None:
                await manager.submit("factor_mining", task)
                result = await manager.eval("factor_mining", task)
                _save(out / "result.json", result.model_dump())
                return
            env = FactorMiningEnvironment(workspace=prepared.workspace_dir)
            await env.initialize()
            if args.resume and (out / "run.json").exists():
                previous = json.loads((out / "run.json").read_text())
                if previous["mode"] != args.command or previous["goal"] != args.goal:
                    raise ValueError("cannot mix no-model checks and agent experiments in one run")
                run["rounds"] = previous["rounds"]
            _save(out / "run.json", run)
            if args.command == "check":
                state = await no_model_check(env, args.goal)
                run["stop_reason"] = "no_model_pipeline_check"
            else:
                from dotenv import load_dotenv
                load_dotenv()
                best = max((r["quality"] for r in run["rounds"] if r.get("quality") is not None), default=None)
                stagnant = run["rounds"][-1].get("stagnant", 0) if run["rounds"] else 0
                state = await env.get_factor_library()
                for round_number in range(len(run["rounds"]) + 1, max_rounds + 1):
                    print(f"Round {round_number}: factor research", flush=True)
                    await dispatch_agent("factor_mining_agent", round_number, prepared.workspace_dir,
                                         out, settings, state)
                    state = await env.get_factor_library()
                    if args.goal == "strategy" and state["factors"] and state["remaining_validations"]:
                        print(f"Round {round_number}: strategy research", flush=True)
                        await dispatch_agent("strategy_mining_agent", round_number, prepared.workspace_dir,
                                             out, settings, state)
                        state = await env.get_factor_library()
                    chosen = choose_strategy(state)
                    quality = state["strategies"][chosen]["quality"] if chosen else None
                    if quality is not None and (best is None or quality > best + improvement):
                        best, stagnant = quality, 0
                    else:
                        stagnant += 1
                    run["rounds"].append({"round": round_number, "quality": quality,
                                           "factors": list(state["factors"]), "stagnant": stagnant,
                                           "diagnoses": list(state["diagnoses"])})
                    reason = stopping_reason(state, goal=args.goal, round_number=round_number,
                        max_rounds=max_rounds, stagnant=stagnant, no_progress_rounds=no_progress)
                    _save(out / "run.json", run)
                    if reason:
                        run["stop_reason"] = reason
                        break
            output = ({"kind": "factors", "names": sorted(state["factors"])} if args.goal == "factors"
                      else {"kind": "strategy", "name": choose_strategy(state) or "no_qualifying_strategy"})
            await manager.submit("factor_mining", task, output=output)
            result = await manager.eval("factor_mining", task)
            _save(out / "result.json", result.model_dump())
            stats = await manager.stats("factor_mining")
            run.update(status="finished" if result.score is not None else "evaluation_error",
                       score=result.score, stats=stats.model_dump())
            _save(out / "run.json", run)
            print(json.dumps({"mode": args.command, "status": run["status"], "score": result.score,
                              "result": str(out / "result.json")}, indent=2))
        except BaseException as exc:
            run.update(status="interrupted" if isinstance(exc, asyncio.CancelledError) else "error", error=str(exc))
            _save(out / "run.json", run)
            raise
        finally:
            await manager.cleanup()


async def main(argv=None):
    args = parse_args(argv)
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    stopping = False

    def stop():
        nonlocal stopping
        if not stopping:
            stopping = True
            task.cancel()

    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop)
    try:
        if args.command == "prepare":
            await prepare_data(args)
        elif args.command == "_worker":
            await worker(args)
        else:
            await research(args)
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    asyncio.run(main())
