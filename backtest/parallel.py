"""백테스트 병렬 실행 유틸리티.

각 프로세스가 독립된 settings + DB 연결을 사용하므로 SQLite WAL 모드와 함께 안전.
_backtest_worker는 모듈 레벨 함수로 ProcessPoolExecutor가 pickle 가능.

사용 예시:
    from backtest.parallel import run_parallel_backtests

    tasks = [
        {"cfg": {"name": "A", "weighting_method": "equal", "max_position_pct": 0.15},
         "start": "2017-01-01", "end": "2024-12-31", "cash": 10_000_000},
        ...
    ]
    results = run_parallel_backtests(tasks, max_workers=4)
    for r in results:
        print(r["name"], r["df"].shape)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _backtest_worker(task: dict) -> dict:
    """단일 백테스트 태스크 실행 (subprocess worker).

    모듈 레벨 정의로 ProcessPoolExecutor가 pickle 가능.
    spawn된 각 프로세스에서 독립적으로 settings를 초기화한 후 cfg를 적용.

    Args:
        task: {
            "cfg": {"name": str, "weighting_method": str, "max_position_pct": float, ...},
            "start": str (YYYY-MM-DD),
            "end": str (YYYY-MM-DD),
            "cash": int,
        }

    Returns:
        {"name": str, "df": pd.DataFrame}
    """
    from config.settings import settings
    from strategy.screener import MultiFactorScreener
    from backtest.engine import MultiFactorBacktest

    cfg = task["cfg"]
    # 프로세스 독립 settings 수정 (다른 프로세스와 격리)
    if "weighting_method" in cfg:
        settings.portfolio.weighting_method = cfg["weighting_method"]
    if "max_position_pct" in cfg:
        settings.portfolio.max_position_pct = cfg["max_position_pct"]

    MultiFactorScreener._factor_cache.clear()
    engine = MultiFactorBacktest(initial_cash=task["cash"])
    df = engine.run(task["start"], task["end"])

    return {"name": cfg["name"], "df": df}


def run_parallel_backtests(
    tasks: list[dict],
    max_workers: int = 4,
    runner: Optional[Callable[[dict], Any]] = None,
) -> list[Any]:
    """여러 백테스트를 병렬 실행.

    Args:
        tasks: 각 백테스트 설정 딕셔너리 리스트 (_backtest_worker 참조)
        max_workers: 최대 프로세스 수 (기본 4)
        runner: 커스텀 실행 함수. None이면 _backtest_worker 사용.
                모듈 레벨 함수여야 pickle 가능 (subprocess 전송).

    Returns:
        tasks 순서와 동일한 결과 리스트
    """
    if runner is None:
        runner = _backtest_worker

    n = min(max_workers, len(tasks))

    if n <= 1:
        logger.info(f"순차 실행: {len(tasks)}개 백테스트")
        return [runner(t) for t in tasks]

    logger.info(f"병렬 실행: {len(tasks)}개 백테스트, {n}개 워커 (ProcessPoolExecutor)")

    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=n) as executor:
        results = list(executor.map(runner, tasks))

    return results
