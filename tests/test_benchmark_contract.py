"""The same lifecycle and results contract for every registered benchmark.

Dataset/browser/grader boundaries are replaced; no downloads, models, or containers.
"""
import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.benchmark import BenchmarkManager
from agentevolver.benchmark.types import Benchmark, EvaluationResult, Stats, Task
from agentevolver.registry import BENCHMARK

CLASSES = list(BENCHMARK._module_dict.values())


def stub_sources(monkeypatch, tmp_path):
    rows = [dict(task_id=str(i), id=str(i), instance_id=str(i), name='Problem',
                 question='Six times seven?', question_md='Six times seven?',
                 true_answer='42', answer='42', reference_answer_md='42',
                 problem_statement='Fix it', base_commit='base', repo='owner/repo',
                 repository='owner/repo', language='python', commit='base',
                 code_template={'python3': 'pass'}, image_name='image',
                 image=None, dockerhub_tag='tag',
                 patch='SECRET', test_patch='SECRET', fail_to_pass=['SECRET'])
            for i in (1, 2)]
    frame = SimpleNamespace(to_dict=lambda **kwargs: rows)
    for module, name in [('aime24', 'AIME24Dataset'), ('aime25', 'AIME25Dataset'),
                         ('GPQA', 'GPQADataset'), ('gsm8k', 'GSM8kDataset'),
                         ('leetcode', 'LeetCodeDataset')]:
        monkeypatch.setitem(sys.modules, 'agentevolver.data.' + module,
                            SimpleNamespace(**{name: lambda **kwargs: SimpleNamespace(data=frame)}))
    monkeypatch.setitem(sys.modules, 'agentevolver.data.deepweb',
                        SimpleNamespace(DeepWebDataset=lambda **kwargs: SimpleNamespace(data=rows)))
    monkeypatch.setitem(sys.modules, 'agentevolver.data.programbench',
                        SimpleNamespace(ProgramBenchDataset=lambda: SimpleNamespace(
                            data=rows, instances={r['instance_id']: r for r in rows})))
    monkeypatch.setitem(sys.modules, 'datasets', SimpleNamespace(load_dataset=lambda *a, **k: rows))
    monkeypatch.setattr('agentevolver.benchmark.utils.ensure_dataset', lambda *a, **k: str(tmp_path))
    from agentevolver.benchmark.default import leetcode
    monkeypatch.setattr(leetcode, 'CodeSubmitter', lambda **kwargs: SimpleNamespace(
        output_file=str(tmp_path / 'unused.jsonl'), initialize=AsyncMock(), close=AsyncMock(), save_result=AsyncMock()))
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize('cls', CLASSES, ids=lambda cls: cls.__name__)
async def test_every_builtin_obeys_lifecycle_and_result_contract(cls, monkeypatch, tmp_path):
    stub_sources(monkeypatch, tmp_path)
    bench = cls(base_dir=str(tmp_path / cls.__name__))
    for name in Benchmark.PUBLIC_METHODS:
        assert getattr(cls, name) is getattr(Benchmark, name)
    await bench.initialize()
    first = await bench.step()
    dataset_free = bench.name == 'exact_match'
    if dataset_free:
        assert first is None
    else:
        assert first.task_id in ('1', '0001')
        # Calling initialize twice must not consume or rewind a task.
        await bench.initialize()
        second = await bench.step()
        assert second.task_id != first.task_id
        assert await bench.step() is None
        assert (await bench.reset()).task_id == first.task_id
        assert (await bench.step()).task_id == second.task_id
        if bench.name.startswith('swebench'):
            assert first.ground_truth is None
            assert not {'patch', 'test_patch', 'fail_to_pass'} & first.extra.keys()
            assert first.extra['dockerhub_tag'] == 'tag'
    await bench.reset()
    async def grade(task):
        if task.result == 'error':
            raise RuntimeError('grader unavailable')
        task.score = float(task.result)
        return task
    monkeypatch.setattr(bench, '_eval', grade)
    result = await bench.eval(Task(task_id='a', result=1))
    assert result.evaluation.status == 'passed'
    result.score = 0  # Caller mutation cannot rewrite stored history.
    assert (await bench.stats()).correct == 1
    assert (await bench.eval(Task(task_id='a', result=0))).evaluation.status == 'failed'
    await bench.eval(Task(task_id='b', result='error'))
    await bench.eval(Task(task_id='c', result=0.5))
    stats = await bench.stats()
    assert isinstance(stats, Stats)
    assert (stats.attempted, stats.scored, stats.errors, stats.correct, stats.wrong) == (3, 2, 1, 0, 2)
    assert stats.mean_score == 0.25 and stats.accuracy == 0
    await bench.reset()
    assert (await bench.stats()).attempted == 0
    await bench.cleanup()


def test_custom_public_methods_and_sync_hooks_are_rejected():
    with pytest.raises(TypeError, match='private hooks'):
        class ExtraEndpoint(Benchmark):
            def instances(self):
                return []
    with pytest.raises(TypeError, match='private hooks'):
        class Bypass(Benchmark):
            async def eval(self, task):
                return task
    with pytest.raises(TypeError, match='must be async'):
        class SyncHook(Benchmark):
            def _step(self):
                return None


@pytest.mark.asyncio
@pytest.mark.parametrize('cls', CLASSES, ids=lambda cls: cls.__name__)
async def test_every_builtin_evaluates_and_restores_through_manager_without_agents(
    cls, monkeypatch, tmp_path
):
    stub_sources(monkeypatch, tmp_path)
    name = cls.model_fields['name'].default

    async def grade(self, task):
        if task.result == 'broken grader':
            raise RuntimeError('controlled evaluator failure')
        task.score = float(task.result)
        return task

    # Replace the external scorer boundary, keeping manager dispatch, base wrappers,
    # persistence, failure recovery and stats real. No agent may be initialized.
    monkeypatch.setattr(cls, '_eval', grade)
    forbidden = AsyncMock(side_effect=AssertionError('evaluation must never run an Agent'))
    monkeypatch.setattr('agentevolver.agent.agent_manager.initialize', forbidden)
    monkeypatch.setattr('agentevolver.task.task_manager.submit', forbidden)
    manager = BenchmarkManager()
    await manager.configure(name, base_dir=str(tmp_path / 'state'))
    result = await manager.eval(name, Task(task_id='same', result='broken grader'))
    assert result.evaluation.status == 'error' and result.score is None
    assert (await manager.stats(name)).errors == 1
    result = await manager.eval(name, Task(task_id='same', result=0))
    assert result.evaluation.status == 'failed' and result.score == 0
    assert (await manager.stats(name)).errors == 0
    await manager.eval(name, Task(task_id='passed', result=1))
    await manager.cleanup(name)
    restored = BenchmarkManager()
    await restored.configure(name, base_dir=str(tmp_path / 'state'), resume=True)
    stats = await restored.stats(name)
    assert (stats.attempted, stats.scored, stats.correct, stats.wrong, stats.errors) == (2, 2, 1, 1, 0)
    forbidden.assert_not_awaited()
    await restored.cleanup(name)


@pytest.mark.asyncio
async def test_manager_forwards_reset_split_judge_and_stats(tmp_path):
    from agentevolver.benchmark.default.exact_match import ExactMatchBenchmark
    bench = ExactMatchBenchmark(base_dir=str(tmp_path))
    manager = BenchmarkManager()
    await manager.register(bench)
    assert await manager.reset('exact_match', split='validation') is None
    assert bench.split == 'validation'
    task = Task(task_id='a', result='42', ground_truth='42')
    assert await manager.llm_judge('exact_match', task) == 1
    assert (await manager.stats('exact_match')).attempted == 0
    assert (await manager.eval('exact_match', task)).evaluation.status == 'passed'
    assert (await manager.stats('exact_match')).correct == 1
    await manager.cleanup()


@pytest.mark.asyncio
async def test_leetcode_partial_batch_finishes_and_concurrent_calls_are_batched(monkeypatch, tmp_path):
    from agentevolver.benchmark.default.leetcode import LeetCodeBenchmark
    stub_sources(monkeypatch, tmp_path)
    bench = LeetCodeBenchmark(base_dir=str(tmp_path), batch_size=5)
    await bench.initialize()
    batches = []
    async def flush():
        batch = bench._pending_queue[:bench.batch_size]
        del bench._pending_queue[:len(batch)]
        batches.append(len(batch))
        await asyncio.sleep(0)
        for task in batch:
            task.score = 1
    monkeypatch.setattr(bench, '_flush_eval_queue', flush)
    result = await asyncio.wait_for(bench.eval(Task(task_id='one', result='code')), 1)
    assert result.evaluation.status == 'passed' and batches == [1]
    results = await asyncio.wait_for(asyncio.gather(*(
        bench.eval(Task(task_id=str(i), result='code')) for i in range(7))), 1)
    assert all(task.evaluation.status == 'passed' for task in results)
    assert batches == [1, 5, 2]
    assert (await bench.stats()).scored == 8
    await bench.cleanup()


@pytest.mark.asyncio
async def test_invalid_or_pending_grader_results_are_errors_and_cancellation_propagates(monkeypatch, tmp_path):
    from agentevolver.benchmark.default.exact_match import ExactMatchBenchmark
    bench = ExactMatchBenchmark(base_dir=str(tmp_path))
    for value in (None, float('nan'), 2):
        async def invalid(task):
            task.score = value
            return task
        monkeypatch.setattr(bench, '_eval', invalid)
        task = await bench.eval(Task(task_id='bad', result='anything'))
        assert task.evaluation.status == 'error' and task.score is None
    async def cancelled(task):
        raise asyncio.CancelledError
    monkeypatch.setattr(bench, '_eval', cancelled)
    with pytest.raises(asyncio.CancelledError):
        await bench.eval(Task(task_id='cancelled'))
    assert (await bench.stats()).attempted == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('module_name', ['swebench_pro', 'swebench_verified'])
async def test_swe_launchers_iterate_public_tasks_without_exposing_oracle(module_name, monkeypatch, tmp_path):
    import importlib
    stub_sources(monkeypatch, tmp_path)
    module = importlib.import_module('examples.run_' + module_name)
    monkeypatch.setattr(module, 'benchmark_manager', BenchmarkManager())
    rows = await module.load_instances()
    assert [row['instance_id'] for row in rows] == ['1', '2']
    assert all(not {'patch', 'test_patch', 'fail_to_pass'} & row.keys() for row in rows)
    assert rows[0]['dockerhub_tag'] == 'tag'


@pytest.mark.asyncio
async def test_leetcode_error_is_unscored_in_return_value_stats_and_saved_record(monkeypatch, tmp_path):
    from agentevolver.benchmark.default.leetcode import LeetCodeBenchmark
    stub_sources(monkeypatch, tmp_path)
    bench = LeetCodeBenchmark(base_dir=str(tmp_path))
    await bench.initialize()
    async def flush():
        for task in bench._pending_queue:
            task.score = 0
            task.extra['prediction'] = 'push_failed'
        bench._pending_queue.clear()
    monkeypatch.setattr(bench, '_flush_eval_queue', flush)
    task = await bench.eval(Task(task_id='broken', result='code'))
    assert task.evaluation.status == 'error' and task.score is None
    saved = bench._submitter.save_result.call_args.args[0]
    assert saved.evaluation.status == 'error' and saved.score is None
    assert (await bench.stats()).errors == 1
    assert (await bench.stats()).wrong == 0
