# tests/test_parallel.py
"""backtest.parallel 유틸리티 테스트."""
import pytest

from backtest.parallel import run_parallel_backtests


def _double(task: dict) -> dict:
    """테스트용 worker: task["x"]를 2배로 반환."""
    return {"result": task["x"] * 2}


class TestRunParallelBacktests:
    def test_sequential_fallback(self) -> None:
        tasks = [{"x": 1}, {"x": 2}, {"x": 3}]
        results = run_parallel_backtests(tasks, max_workers=1, runner=_double)
        assert [r["result"] for r in results] == [2, 4, 6]

    def test_preserves_order(self) -> None:
        tasks = [{"x": i} for i in range(5)]
        results = run_parallel_backtests(tasks, max_workers=1, runner=_double)
        assert [r["result"] for r in results] == [0, 2, 4, 6, 8]

    def test_single_task_sequential(self) -> None:
        results = run_parallel_backtests([{"x": 7}], max_workers=4, runner=_double)
        assert results == [{"result": 14}]

    def test_empty_tasks(self) -> None:
        results = run_parallel_backtests([], max_workers=2, runner=_double)
        assert results == []
