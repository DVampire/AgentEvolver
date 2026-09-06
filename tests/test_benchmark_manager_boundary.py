"""Runtime consumers use the manager facade, not concrete benchmark implementations."""
import ast
from pathlib import Path

import pytest

from agentevolver.benchmark import BenchmarkInfo, BenchmarkManager, Task


@pytest.mark.asyncio
async def test_manager_owns_creation_and_returns_detached_metadata(tmp_path):
    manager = BenchmarkManager()
    info = await manager.configure('exact_match', base_dir=str(tmp_path))
    assert isinstance(info, BenchmarkInfo)
    assert not hasattr(manager, 'get')
    assert not hasattr(info, 'instance') and not hasattr(info, 'cls')
    info.config['base_dir'] = 'modified externally'
    assert (await manager.get_info('exact_match')).config['base_dir'] == str(tmp_path)
    assert not await manager.is_loaded('exact_match')
    result = await manager.eval('exact_match', Task(task_id='a', result='42', ground_truth='42'))
    assert result.score == 1
    assert await manager.is_loaded('exact_match')
    await manager.configure('swebench_pro')  # Lazy: no dataset/container/model work.
    assert (await manager.stats('exact_match')).correct == 1
    await manager.initialize(['exact_match'])
    assert (await manager.stats('exact_match')).correct == 1
    assert await manager.get_info('swebench_pro') is not None
    await manager.cleanup()


@pytest.mark.asyncio
async def test_manager_validates_grader_identity_without_loading_data(tmp_path):
    manager = BenchmarkManager()
    (tmp_path / 'swe_bench_pro_eval.py').write_text('# evaluator\n')
    (tmp_path / 'run_script.sh').write_text('go test ./...\n')
    options = {'grader_repo': str(tmp_path), 'grader_profile': 'official'}
    info = await manager.get_info('swebench_pro', evaluation_options=options)
    assert not info.initialized
    assert len(info.evaluation['grader_fingerprint']) == 64
    await manager.get_info('swebench_pro', evaluation_options=options,
                           expected_evaluation=info.evaluation)
    (tmp_path / 'run_script.sh').write_text('go test ./different\n')
    with pytest.raises(ValueError, match='grader_fingerprint'):
        await manager.get_info('swebench_pro', evaluation_options=options,
                               expected_evaluation=info.evaluation)
    with pytest.raises(ValueError, match='grader_profile'):
        await manager.get_info('swebench_pro', evaluation_options={**options, 'grader_profile': 'diagnostic'},
                               expected_evaluation=info.evaluation)
    assert not await manager.is_loaded('swebench_pro')


def test_manager_projects_solver_fields_without_oracle_or_runtime_objects():
    manager = BenchmarkManager()
    row = {'instance_id': 'x', 'problem_statement': 'fix me', 'repo': 'owner/repo',
           'patch': 'SECRET', 'test_patch': 'SECRET', 'fail_to_pass': ['SECRET']}
    payload = manager.task_payload('swebench_pro', row)
    assert payload['problem_statement'] == 'fix me'
    assert 'SECRET' not in str(payload)
    assert 'grader_fingerprint' not in payload
    catalog = manager.catalog()
    assert len(catalog) == 11
    assert all(isinstance(info, BenchmarkInfo) and not info.initialized for info in catalog)


def test_production_consumers_do_not_import_implementations_or_access_raw_instances():
    root = Path(__file__).resolve().parents[1]
    paths = [*root.joinpath('examples').rglob('*.py'), root / 'datasets/load.py',
             root / 'others/swe_grader_audit.py', *root.joinpath('agentevolver').rglob('*.py')]
    violations = []
    for path in paths:
        relative = path.relative_to(root)
        if relative.parts[:2] == ('agentevolver', 'benchmark'):
            continue  # Implementation unit tests and internals may inspect private hooks.
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('agentevolver.benchmark'):
                if node.module.startswith('agentevolver.benchmark.default'):
                    violations.append(f'{relative}:{node.lineno}: implementation import')
                if any(alias.name.endswith('Benchmark') and alias.name != 'Benchmark' for alias in node.names):
                    violations.append(f'{relative}:{node.lineno}: concrete class import')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'benchmark_manager':
                    if node.func.attr == 'get' or node.func.attr.startswith('_'):
                        violations.append(f'{relative}:{node.lineno}: manager bypass')
    assert not violations, '\n'.join(violations)
