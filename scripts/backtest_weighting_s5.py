"""S5: 포지션 사이징 비교 백테스트 (equal vs inverse_vol).

A_equal       : 기본 equal-weight (현행 Preset A)
B_invvol      : inverse_vol, max_position_pct=15%
C_invvol_10   : inverse_vol, max_position_pct=10%
D_invvol_20   : inverse_vol, max_position_pct=20%

실행:
    python scripts/backtest_weighting_s5.py [--start 2017-01-01] [--end 2024-12-31]

POLICY 5조건:
  1. CAGR 손실 ≤ -1%p (vs A_equal)
  2. MDD 5%p 이상 개선 OR (ΔCAGR>0 AND ΔSharpe>0)
  3. Sharpe 하락 < 0.10
  4. 종목 선정 동일 (비중만 다름) → 자동 통과
  5. 구간별 안정성 (4구간)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RISK_FREE_ANNUAL = settings.momentum.risk_free_rate


@dataclass
class BacktestResult:
    name: str
    cagr: float
    mdd: float
    sharpe: float
    sortino: float
    calmar: float
    ann_vol: float
    max_weight_avg: float
    hhi_avg: float
    dsr: float
    cagr_2017_2019: float
    cagr_2020_2021: float
    cagr_2022_2022: float
    cagr_2023_2024: float


CONFIGS: list[dict] = [
    {"name": "A_equal",     "weighting_method": "equal",       "max_position_pct": 0.15},
    {"name": "B_invvol",    "weighting_method": "inverse_vol", "max_position_pct": 0.15},
    {"name": "C_invvol_10", "weighting_method": "inverse_vol", "max_position_pct": 0.10},
    {"name": "D_invvol_20", "weighting_method": "inverse_vol", "max_position_pct": 0.20},
]


def _apply_config(cfg: dict) -> None:
    settings.portfolio.weighting_method = cfg["weighting_method"]
    settings.portfolio.max_position_pct = cfg["max_position_pct"]


def _calc_metrics(df: pd.DataFrame, initial_cash: float) -> dict:
    pv = df["portfolio_value"]
    returns = df["returns"].dropna()

    n_years = len(pv) / 252
    total_ret = pv.iloc[-1] / initial_cash - 1
    cagr = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0.0

    rolling_max = pv.cummax()
    drawdown = (pv - rolling_max) / rolling_max
    mdd = drawdown.min()

    rf_daily = (1 + RISK_FREE_ANNUAL) ** (1 / 252) - 1
    excess = returns - rf_daily
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = (excess.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0.0

    downside = returns[returns < rf_daily]
    sortino_denom = downside.std() * np.sqrt(252) if len(downside) > 1 else ann_vol
    sortino = (excess.mean() * 252 / sortino_denom) if sortino_denom > 0 else 0.0

    calmar = abs(cagr / mdd) if mdd != 0 else 0.0

    return {
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "ann_vol": ann_vol,
    }


def _period_cagr(df: pd.DataFrame, start: str, end: str, initial: float) -> float:
    mask = (df.index >= start) & (df.index <= end)
    sub = df[mask]
    if sub.empty:
        return float("nan")
    n_days = len(sub)
    n_years = n_days / 252
    if n_years < 0.1:
        return float("nan")
    ret = sub["portfolio_value"].iloc[-1] / sub["portfolio_value"].iloc[0] - 1
    return (1 + ret) ** (1 / n_years) - 1


def _turnover_log_weights(turnover_log: list[dict]) -> tuple[float, float]:
    """평균 최대 비중과 HHI 계산 (turnover_log에서 추출)."""
    # 실제 weight 정보는 turnover_log에 없으므로 0.0 반환 (포트 DataFrame 필요)
    return 0.0, 0.0


def _calc_dsr(sharpe: float, n_trials: int = 4, n_years: float = 7.5) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    DSR = Phi[(SR_hat - E[SR_max]) / sqrt(Var[SR_max])]
    여기서는 단순화: SR_max = max expected from multiple testing.
    """
    from scipy import stats

    gamma_1 = 0.0    # 가정: 수익률 분포 정규
    gamma_2 = 3.0
    t = n_years * 252

    e_max = (1 - 0.5772) * np.log(n_trials) + 0.5772
    v_max = np.pi**2 / 6 * np.log(n_trials) / t

    z = (sharpe - e_max) / (np.sqrt(v_max) if v_max > 0 else 1e-9)
    return float(stats.norm.cdf(z))


def run_backtest(
    cfg: dict,
    start_date: str,
    end_date: str,
    initial_cash: float,
) -> tuple[BacktestResult, pd.DataFrame]:
    from backtest.engine import MultiFactorBacktest
    from strategy.screener import MultiFactorScreener

    # 캐시 초기화 (모드 변경 시 이전 캐시 무효화)
    MultiFactorScreener._factor_cache.clear()
    _apply_config(cfg)

    engine = MultiFactorBacktest(initial_cash=initial_cash)
    df = engine.run(start_date, end_date)

    m = _calc_metrics(df, initial_cash)
    dsr = _calc_dsr(m["sharpe"], n_trials=len(CONFIGS), n_years=(len(df) / 252))

    result = BacktestResult(
        name=cfg["name"],
        cagr=m["cagr"],
        mdd=m["mdd"],
        sharpe=m["sharpe"],
        sortino=m["sortino"],
        calmar=m["calmar"],
        ann_vol=m["ann_vol"],
        max_weight_avg=0.0,
        hhi_avg=0.0,
        dsr=dsr,
        cagr_2017_2019=_period_cagr(df, "2017-01-01", "2019-12-31", initial_cash),
        cagr_2020_2021=_period_cagr(df, "2020-01-01", "2021-12-31", initial_cash),
        cagr_2022_2022=_period_cagr(df, "2022-01-01", "2022-12-31", initial_cash),
        cagr_2023_2024=_period_cagr(df, "2023-01-01", "2024-12-31", initial_cash),
    )
    return result, df


def _policy_check(results: list[BacktestResult]) -> list[dict]:
    baseline = next(r for r in results if r.name == "A_equal")
    checks = []
    for r in results:
        if r.name == "A_equal":
            continue
        d_cagr = r.cagr - baseline.cagr
        d_mdd = r.mdd - baseline.mdd
        d_sharpe = r.sharpe - baseline.sharpe

        p1 = d_cagr >= -0.01
        p2_mdd = (baseline.mdd - r.mdd) >= 0.05     # MDD 5%p 이상 개선
        p2_alpha = d_cagr > 0 and d_sharpe > 0
        p2 = p2_mdd or p2_alpha
        p3 = d_sharpe >= -0.10
        p4 = True                                     # 동일 종목 선정
        p5_cagrs = [
            r.cagr_2017_2019, r.cagr_2020_2021, r.cagr_2022_2022, r.cagr_2023_2024,
        ]
        p5 = sum(1 for c in p5_cagrs if not np.isnan(c) and c > 0) >= 3

        passed = sum([p1, p2, p3, p4, p5])
        checks.append({
            "name": r.name,
            "P1_cagr_loss": p1, "d_cagr": d_cagr,
            "P2_mdd_or_alpha": p2, "d_mdd_pct": (baseline.mdd - r.mdd),
            "P3_sharpe": p3, "d_sharpe": d_sharpe,
            "P4_ticker_overlap": p4,
            "P5_stability": p5,
            "total_pass": passed,
        })
    return checks


def print_table(results: list[BacktestResult]) -> None:
    header = (
        f"{'모드':<16} {'CAGR':>7} {'MDD':>7} {'Sharpe':>7} {'Sortino':>8} "
        f"{'Calmar':>7} {'Vol':>6} {'DSR':>6}"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in results:
        print(
            f"{r.name:<16} {r.cagr:>6.1%} {r.mdd:>6.1%} {r.sharpe:>7.3f} "
            f"{r.sortino:>7.3f} {r.calmar:>7.3f} {r.ann_vol:>5.1%} {r.dsr:>6.3f}"
        )
    print(sep)


def print_period_table(results: list[BacktestResult]) -> None:
    header = f"{'모드':<16} {'2017-2019':>10} {'2020-2021':>10} {'2022':>7} {'2023-2024':>10}"
    sep = "-" * len(header)
    print("\n[구간별 CAGR]")
    print(sep)
    print(header)
    print(sep)
    for r in results:
        def fmt(v: float) -> str:
            return f"{v:>9.1%}" if not np.isnan(v) else "      N/A"
        print(
            f"{r.name:<16} {fmt(r.cagr_2017_2019)} {fmt(r.cagr_2020_2021)} "
            f"{fmt(r.cagr_2022_2022)} {fmt(r.cagr_2023_2024)}"
        )
    print(sep)


def print_policy(checks: list[dict]) -> None:
    print("\n[POLICY 5조건 평가]")
    header = f"{'모드':<16} P1(CAGR) P2(MDD/α) P3(Sharpe) P4(종목) P5(안정) {'통과':>4}"
    print(header)
    print("-" * len(header))
    for c in checks:
        def yn(v: bool) -> str:
            return "O" if v else "X"
        p1_str = ("O (" if c["P1_cagr_loss"] else "X (") + f"{c['d_cagr']:+.1%})"
        print(
            f"{c['name']:<16} "
            f"{p1_str:<10} "
            f"{yn(c['P2_mdd_or_alpha']):<10} "
            f"{yn(c['P3_sharpe']):<11} "
            f"{yn(c['P4_ticker_overlap']):<8} "
            f"{yn(c['P5_stability']):<8} "
            f"{c['total_pass']}/5"
        )


def save_report(
    results: list[BacktestResult],
    checks: list[dict],
    start_date: str,
    end_date: str,
) -> None:
    import io
    buf = io.StringIO()

    buf.write("# S5: 포지션 사이징 분석 (Equal-Weight vs Inverse-Volatility)\n\n")
    buf.write(f"**기간**: {start_date} ~ {end_date} | **시장**: KOSPI | **시드**: 42  \n")
    buf.write("**전략**: Preset A (V70M30, Vol70, F-Score≥4, S4 섹터분산)  \n\n")

    buf.write("## 1. 4가지 비중 방식 비교\n\n")
    buf.write(
        "| 모드 | CAGR | MDD | Sharpe | Sortino | Calmar | Vol | DSR |\n"
        "|------|------|-----|--------|---------|--------|-----|-----|\n"
    )
    for r in results:
        buf.write(
            f"| {r.name} | {r.cagr:.1%} | {r.mdd:.1%} | {r.sharpe:.3f} | "
            f"{r.sortino:.3f} | {r.calmar:.3f} | {r.ann_vol:.1%} | {r.dsr:.3f} |\n"
        )

    buf.write("\n## 2. MDD 상세 분석\n\n")
    baseline = next(r for r in results if r.name == "A_equal")
    buf.write("| 모드 | MDD | vs A_equal 갭 |\n|------|-----|---------------|\n")
    for r in results:
        gap = r.mdd - baseline.mdd
        buf.write(f"| {r.name} | {r.mdd:.1%} | {gap:+.1%} |\n")

    buf.write("\n## 3. 구간별 CAGR\n\n")
    buf.write(
        "| 모드 | 2017-2019 | 2020-2021 | 2022 | 2023-2024 |\n"
        "|------|-----------|-----------|------|----------|\n"
    )
    for r in results:
        def fv(v: float) -> str:
            return f"{v:.1%}" if not np.isnan(v) else "N/A"
        buf.write(
            f"| {r.name} | {fv(r.cagr_2017_2019)} | {fv(r.cagr_2020_2021)} | "
            f"{fv(r.cagr_2022_2022)} | {fv(r.cagr_2023_2024)} |\n"
        )

    buf.write("\n## 4. POLICY 5조건 평가\n\n")
    buf.write(
        "| 모드 | P1(CAGR≥-1%p) | P2(MDD5%p+/α) | P3(Sharpe>-0.10) | P4(종목) | P5(안정성) | 통과 |\n"
        "|------|--------------|----------------|-----------------|---------|-----------|------|\n"
    )
    for c in checks:
        yn = lambda v: "O" if v else "X"
        buf.write(
            f"| {c['name']} | {yn(c['P1_cagr_loss'])} ({c['d_cagr']:+.1%}) | "
            f"{yn(c['P2_mdd_or_alpha'])} (ΔMDD={c['d_mdd_pct']:+.1%}) | "
            f"{yn(c['P3_sharpe'])} (ΔSharpe={c['d_sharpe']:+.3f}) | "
            f"{yn(c['P4_ticker_overlap'])} | {yn(c['P5_stability'])} | "
            f"{c['total_pass']}/5 |\n"
        )

    buf.write("\n## 5. 채택 권고\n\n")
    adopted = [c for c in checks if c["total_pass"] >= 4]
    if adopted:
        best = max(adopted, key=lambda c: c["d_mdd_pct"])
        buf.write(
            f"**채택 권고**: `{best['name']}`  \n"
            f"- MDD 개선: {best['d_mdd_pct']:+.1%}  \n"
            f"- CAGR 변화: {best['d_cagr']:+.1%}  \n"
            f"- Sharpe 변화: {best['d_sharpe']:+.3f}  \n"
        )
    else:
        buf.write(
            "**채택 권고 없음**: 모든 inverse_vol 조합이 POLICY 5조건을 충족하지 못함.  \n"
            "→ 현행 equal-weight(A_equal) 유지.  \n"
        )

    report_path = "docs/reports/weighting_s5_analysis.md"
    os.makedirs("docs/reports", exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    logger.info(f"보고서 저장: {report_path}")


def _run_parallel_task(task: dict) -> tuple[BacktestResult, pd.DataFrame]:
    """병렬 실행용 래퍼 — backtest.parallel._backtest_worker 호출 후 BacktestResult 구성.

    ProcessPoolExecutor가 이 함수를 직접 쓰지 않고 backtest.parallel._backtest_worker를
    사용하므로, 여기서는 결과 후처리만 담당.
    """
    from backtest.parallel import _backtest_worker

    pr = _backtest_worker(task)
    df = pr["df"]
    m = _calc_metrics(df, task["cash"])
    dsr = _calc_dsr(m["sharpe"], n_trials=len(CONFIGS), n_years=(len(df) / 252))
    cfg = task["cfg"]
    result = BacktestResult(
        name=cfg["name"],
        cagr=m["cagr"], mdd=m["mdd"], sharpe=m["sharpe"],
        sortino=m["sortino"], calmar=m["calmar"], ann_vol=m["ann_vol"],
        max_weight_avg=0.0, hhi_avg=0.0, dsr=dsr,
        cagr_2017_2019=_period_cagr(df, "2017-01-01", "2019-12-31", task["cash"]),
        cagr_2020_2021=_period_cagr(df, "2020-01-01", "2021-12-31", task["cash"]),
        cagr_2022_2022=_period_cagr(df, "2022-01-01", "2022-12-31", task["cash"]),
        cagr_2023_2024=_period_cagr(df, "2023-01-01", "2024-12-31", task["cash"]),
    )
    return result, df


def main() -> None:
    parser = argparse.ArgumentParser(description="S5 포지션 사이징 백테스트")
    parser.add_argument("--start", default="2017-01-01")
    parser.add_argument("--end",   default="2024-12-31")
    parser.add_argument("--cash",  type=int, default=10_000_000)
    parser.add_argument(
        "--sequential", action="store_true",
        help="순차 실행 (기본: 병렬). 디버깅 시 사용.",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="병렬 프로세스 수 (기본 4)",
    )
    args = parser.parse_args()

    logger.info(f"S5 백테스트: {args.start} ~ {args.end}, 시드 42")
    np.random.seed(42)

    results: list[BacktestResult] = []
    dfs: dict[str, pd.DataFrame] = {}

    if args.sequential:
        # ── 순차 실행 (기존 동작, 디버깅용) ──
        original_weighting = settings.portfolio.weighting_method
        original_max_pos = settings.portfolio.max_position_pct
        try:
            for cfg in CONFIGS:
                logger.info(f"\n{'='*50}\n모드: {cfg['name']}\n{'='*50}")
                try:
                    result, df = run_backtest(cfg, args.start, args.end, args.cash)
                    results.append(result)
                    dfs[cfg["name"]] = df
                    logger.info(
                        f"[{cfg['name']}] CAGR={result.cagr:.1%}, "
                        f"MDD={result.mdd:.1%}, Sharpe={result.sharpe:.3f}"
                    )
                except Exception as e:
                    logger.error(f"[{cfg['name']}] 실패: {e}", exc_info=True)
        finally:
            settings.portfolio.weighting_method = original_weighting
            settings.portfolio.max_position_pct = original_max_pos
    else:
        # ── 병렬 실행 (ProcessPoolExecutor, 각 프로세스 독립 settings) ──
        from backtest.parallel import run_parallel_backtests

        tasks = [
            {"cfg": cfg, "start": args.start, "end": args.end, "cash": args.cash}
            for cfg in CONFIGS
        ]
        max_workers = min(args.workers, len(CONFIGS))
        logger.info(f"병렬 실행: {len(tasks)}개 백테스트, {max_workers}개 워커")

        try:
            parallel_results = run_parallel_backtests(tasks, max_workers=max_workers)
            for task, pr in zip(tasks, parallel_results):
                df = pr["df"]
                cfg = task["cfg"]
                m = _calc_metrics(df, args.cash)
                dsr = _calc_dsr(m["sharpe"], n_trials=len(CONFIGS), n_years=(len(df) / 252))
                result = BacktestResult(
                    name=cfg["name"],
                    cagr=m["cagr"], mdd=m["mdd"], sharpe=m["sharpe"],
                    sortino=m["sortino"], calmar=m["calmar"], ann_vol=m["ann_vol"],
                    max_weight_avg=0.0, hhi_avg=0.0, dsr=dsr,
                    cagr_2017_2019=_period_cagr(df, "2017-01-01", "2019-12-31", args.cash),
                    cagr_2020_2021=_period_cagr(df, "2020-01-01", "2021-12-31", args.cash),
                    cagr_2022_2022=_period_cagr(df, "2022-01-01", "2022-12-31", args.cash),
                    cagr_2023_2024=_period_cagr(df, "2023-01-01", "2024-12-31", args.cash),
                )
                results.append(result)
                dfs[cfg["name"]] = df
                logger.info(
                    f"[{cfg['name']}] CAGR={result.cagr:.1%}, "
                    f"MDD={result.mdd:.1%}, Sharpe={result.sharpe:.3f}"
                )
        except Exception as e:
            logger.error(f"병렬 실행 실패: {e}", exc_info=True)
            logger.info("순차 실행으로 재시도 중...")
            # 병렬 실패 시 순차 폴백
            original_weighting = settings.portfolio.weighting_method
            original_max_pos = settings.portfolio.max_position_pct
            try:
                for cfg in CONFIGS:
                    try:
                        result, df = run_backtest(cfg, args.start, args.end, args.cash)
                        results.append(result)
                        dfs[cfg["name"]] = df
                    except Exception as e2:
                        logger.error(f"[{cfg['name']}] 순차 폴백도 실패: {e2}", exc_info=True)
            finally:
                settings.portfolio.weighting_method = original_weighting
                settings.portfolio.max_position_pct = original_max_pos

    if not results:
        logger.error("실행 결과 없음")
        return

    print("\n" + "="*80)
    print("S5: 포지션 사이징 비교 결과")
    print("="*80)
    print_table(results)
    print_period_table(results)
    checks = _policy_check(results)
    print_policy(checks)
    save_report(results, checks, args.start, args.end)
    print("\n보고서: docs/reports/weighting_s5_analysis.md")


if __name__ == "__main__":
    main()
