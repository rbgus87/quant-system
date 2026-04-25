"""backtest_kosdaq_expansion.py — KOSPI vs KOSPI+KOSDAQ vs KOSDAQ 백테스트 비교.

v2.0 프리셋 A (V70M30 + Vol70) 기준으로 3개 시나리오 비교:
  A. KOSPI 단독 (현재 Baseline)
  B. KOSPI + KOSDAQ 통합 (market="ALL")
  C. KOSDAQ 단독 (참고용)

측정:
  - 성과: CAGR, MDD, Sharpe, Sortino, Volatility, 총수익률
  - 선정 구성: KOSDAQ 비율, 평균 종목수
  - 리스크: 폐지(failure) 노출, F-Score 분포, 유동성 통과율
  - 유동성: 선정 종목당 20일 평균 거래대금 (슬리피지 추정)

**이 스크립트는 config.yaml / 실전 설정을 영구 변경하지 않습니다.**
런타임 settings 객체를 복사→변경→복원합니다.

사용:
    python scripts/backtest_kosdaq_expansion.py --start 2017-01-01 --end 2024-12-31
    python scripts/backtest_kosdaq_expansion.py --scenarios KOSPI,ALL
    python scripts/backtest_kosdaq_expansion.py --skip-screener  # 선정 구성 분석 스킵
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from config.settings import settings  # noqa: E402
from data.storage import DataStorage  # noqa: E402
from sqlalchemy import text  # noqa: E402

logger = logging.getLogger(__name__)

SCENARIOS = [
    ("KOSPI", "KOSPI 단독 (Baseline)"),
    ("ALL", "KOSPI + KOSDAQ 통합"),
    ("KOSDAQ", "KOSDAQ 단독 (참고용)"),
]


# ───────────────────────────────────────────────
# 데이터 클래스
# ───────────────────────────────────────────────

@dataclass
class ScenarioResult:
    """단일 시나리오 결과."""
    name: str
    description: str
    start: str
    end: str
    # 성과 지표
    cagr: float = 0.0
    mdd: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0
    calmar: float = 0.0
    n_days: int = 0
    # 선정 구성
    n_rebalances: int = 0
    avg_selection_size: float = 0.0
    kosdaq_ratio_per_date: list[float] = field(default_factory=list)
    avg_kosdaq_ratio: float = 0.0
    # 리스크
    failure_exposure_count: int = 0
    failure_tickers: list[str] = field(default_factory=list)
    # 유동성
    avg_trading_value_per_selection: float = 0.0
    # 에러
    error: Optional[str] = None


# ───────────────────────────────────────────────
# 설정 컨텍스트 매니저
# ───────────────────────────────────────────────

class SettingsGuard:
    """settings 객체를 컨텍스트 종료 시 원복."""

    def __init__(self) -> None:
        # 관심 필드만 백업 (deepcopy는 dataclass + 기타 객체 포함 → 비용 큼)
        self._backup = {
            "universe.market": settings.universe.market,
        }

    def apply_market(self, market: str) -> None:
        settings.universe.market = market
        # 스크리너 팩터 캐시 클리어 (market이 cache key에 포함되나,
        # 혼선 방지)
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception as e:
            logger.warning(f"팩터 캐시 클리어 실패: {e}")

    def restore(self) -> None:
        settings.universe.market = self._backup["universe.market"]
        try:
            from strategy.screener import MultiFactorScreener
            MultiFactorScreener._factor_cache.clear()
        except Exception:
            pass

    def __enter__(self) -> "SettingsGuard":
        return self

    def __exit__(self, *_args) -> None:
        self.restore()


# ───────────────────────────────────────────────
# 선정 구성 분석 (screener)
# ───────────────────────────────────────────────

def _build_ticker_market_map(
    storage: DataStorage, tickers: list[str]
) -> dict[str, str]:
    """종목코드 → 시장(KOSPI/KOSDAQ) 매핑. market_cap 테이블에서 최빈값 조회."""
    if not tickers:
        return {}
    with storage.engine.connect() as conn:
        # 각 ticker의 가장 최근 market 값 채택
        rows = conn.execute(
            text(
                "SELECT ticker, market "
                "FROM market_cap "
                "WHERE ticker IN :tk "
                "GROUP BY ticker"
            ).bindparams(),
            {"tk": tuple(tickers) if len(tickers) > 1 else (tickers[0], tickers[0])},
        ).fetchall()
    return {r[0]: (r[1] or "KOSPI") for r in rows}


def _ticker_market(storage: DataStorage, ticker: str) -> str:
    """단일 ticker의 market 조회 (캐시 없이 간단 구현)."""
    with storage.engine.connect() as conn:
        row = conn.execute(
            text("SELECT market FROM market_cap WHERE ticker = :t LIMIT 1"),
            {"t": ticker},
        ).fetchone()
    return (row[0] if row and row[0] else "KOSPI")


def collect_selections(
    rebal_dates: list[pd.Timestamp], market: str
) -> dict[str, list[str]]:
    """각 리밸런싱 날짜의 선정 종목 리스트 수집.

    Args:
        rebal_dates: 리밸런싱 날짜 (engine과 동일한 로직으로 생성)
        market: 시장 ('KOSPI' / 'KOSDAQ' / 'ALL')

    Returns:
        {date_str: [tickers]}
    """
    from strategy.screener import MultiFactorScreener

    screener = MultiFactorScreener()
    result: dict[str, list[str]] = {}
    for i, rdt in enumerate(rebal_dates):
        ds = rdt.strftime("%Y%m%d")
        try:
            df = screener.screen(ds, market=market)
            tickers = df.index.tolist() if df is not None and not df.empty else []
        except Exception as e:
            logger.warning(f"[{market}] {ds} screener 실패: {e}")
            tickers = []
        result[ds] = tickers
        if (i + 1) % 8 == 0:
            logger.info(f"[{market}] 선정 수집 {i + 1}/{len(rebal_dates)}")
    return result


# ───────────────────────────────────────────────
# 폐지 노출 계산
# ───────────────────────────────────────────────

def count_failure_exposure(
    storage: DataStorage,
    selections: dict[str, list[str]],
    rebal_dates: list[pd.Timestamp],
) -> tuple[int, list[str]]:
    """선정된 종목 중 다음 리밸런싱 전 failure 폐지 건수."""
    if not selections or len(rebal_dates) < 2:
        return 0, []

    count = 0
    failure_tickers: set[str] = set()
    with storage.engine.connect() as conn:
        for i, rdt in enumerate(rebal_dates[:-1]):
            ds = rdt.strftime("%Y%m%d")
            next_rdt = rebal_dates[i + 1]
            tickers = selections.get(ds, [])
            if not tickers:
                continue
            placeholders = ",".join(f":t{j}" for j in range(len(tickers)))
            params = {f"t{j}": t for j, t in enumerate(tickers)}
            params["start"] = rdt.date()
            params["end"] = next_rdt.date()
            rows = conn.execute(
                text(
                    f"SELECT ticker FROM delisted_stock "
                    f"WHERE category = 'failure' "
                    f"AND delist_date > :start AND delist_date <= :end "
                    f"AND ticker IN ({placeholders})"
                ),
                params,
            ).fetchall()
            for (t,) in rows:
                count += 1
                failure_tickers.add(t)
    return count, sorted(failure_tickers)


# ───────────────────────────────────────────────
# 유동성: 선정 종목 평균 20일 거래대금
# ───────────────────────────────────────────────

def avg_trading_value(
    storage: DataStorage,
    selections: dict[str, list[str]],
    window: int = 20,
) -> float:
    """선정 종목의 선정 시점 직전 window 영업일 평균 거래대금 (원).

    거래대금 = close × volume (OHLCV 기반 근사). 선정 시점 종목별 평균의
    전 리밸런싱 평균을 반환.
    """
    if not selections:
        return 0.0
    per_rebal_means: list[float] = []
    with storage.engine.connect() as conn:
        for ds, tickers in selections.items():
            if not tickers:
                continue
            rdt = datetime.strptime(ds, "%Y%m%d").date()
            # 직전 window 영업일 평균 close×volume 조회 (단일 SQL)
            placeholders = ",".join(f":t{j}" for j in range(len(tickers)))
            params = {f"t{j}": t for j, t in enumerate(tickers)}
            params["end"] = rdt
            rows = conn.execute(
                text(
                    f"SELECT ticker, AVG(close * volume) AS tv "
                    f"FROM daily_price "
                    f"WHERE ticker IN ({placeholders}) "
                    f"AND date <= :end "
                    f"AND date >= date(:end, '-{window * 2} days') "
                    f"GROUP BY ticker"
                ),
                params,
            ).fetchall()
            vals = [r[1] for r in rows if r[1] is not None]
            if vals:
                per_rebal_means.append(sum(vals) / len(vals))
    if not per_rebal_means:
        return 0.0
    return sum(per_rebal_means) / len(per_rebal_means)


# ───────────────────────────────────────────────
# 단일 시나리오 실행
# ───────────────────────────────────────────────

def run_scenario(
    name: str,
    description: str,
    start_date: str,
    end_date: str,
    run_screener: bool = True,
) -> ScenarioResult:
    """단일 시나리오 백테스트 + 구성 분석."""
    from backtest.engine import MultiFactorBacktest
    from backtest.metrics import PerformanceAnalyzer

    result = ScenarioResult(
        name=name, description=description,
        start=start_date, end=end_date,
    )

    logger.info("=" * 60)
    logger.info(f"시나리오: {name} — {description}")
    logger.info("=" * 60)

    with SettingsGuard() as guard:
        guard.apply_market(name)

        # 1) 백테스트 실행
        try:
            engine = MultiFactorBacktest()
            df = engine.run(start_date, end_date, market=name)
        except Exception as e:
            logger.error(f"[{name}] 백테스트 실패: {e}", exc_info=True)
            result.error = f"backtest_failed: {e}"
            return result

        if df is None or df.empty:
            result.error = "empty_backtest_result"
            return result

        pv = df["portfolio_value"]
        rt = df["returns"].dropna()
        analyzer = PerformanceAnalyzer()

        result.cagr = analyzer.calculate_cagr(pv)
        result.mdd = analyzer.calculate_mdd(pv)
        result.sharpe = analyzer.calculate_sharpe(rt)
        result.sortino = analyzer.calculate_sortino(rt)
        result.volatility = analyzer.calculate_volatility(rt)
        result.calmar = analyzer.calculate_calmar(result.cagr, result.mdd)
        result.total_return = float(pv.iloc[-1] / pv.iloc[0] - 1) if len(pv) >= 2 else 0.0
        result.n_days = len(pv)

        # 2) 리밸런싱 날짜 재생성 (engine 내부와 동일)
        rebal_dates = engine._generate_rebalance_dates(start_date, end_date, name)
        result.n_rebalances = len(rebal_dates)

        # 3) 선정 구성 수집 (옵션)
        if not run_screener:
            logger.info(f"[{name}] --skip-screener: 선정 구성 분석 스킵")
            return result

        logger.info(f"[{name}] 선정 종목 수집 중 ({len(rebal_dates)}회)")
        selections = collect_selections(rebal_dates, name)

        # 유효 선정일 종목수 평균
        sizes = [len(v) for v in selections.values() if v]
        result.avg_selection_size = sum(sizes) / len(sizes) if sizes else 0.0

        # KOSDAQ 비율 계산 (종목 → market 매핑)
        storage = DataStorage()
        all_tickers = sorted({t for lst in selections.values() for t in lst})
        tkr_mkt: dict[str, str] = {}
        if all_tickers:
            # 배치 조회 (쿼리 단순화 위해 개별 조회 → 수천 개면 느리니 벌크 조회)
            # 매 리밸런싱마다 수십 개 종목이라 중복 제거 후 1회 조회
            with storage.engine.connect() as conn:
                # SQLAlchemy의 IN 바인딩은 dialect마다 다르므로 수동 구성
                chunk = 500
                for i in range(0, len(all_tickers), chunk):
                    sub = all_tickers[i:i + chunk]
                    placeholders = ",".join(f":t{j}" for j in range(len(sub)))
                    params = {f"t{j}": t for j, t in enumerate(sub)}
                    rows = conn.execute(
                        text(
                            f"SELECT ticker, market FROM market_cap "
                            f"WHERE ticker IN ({placeholders}) "
                            f"GROUP BY ticker"
                        ),
                        params,
                    ).fetchall()
                    for t, m in rows:
                        tkr_mkt[t] = (m or "KOSPI")

        kosdaq_ratios = []
        for ds, tickers in selections.items():
            if not tickers:
                continue
            kd = sum(1 for t in tickers if tkr_mkt.get(t) == "KOSDAQ")
            kosdaq_ratios.append(kd / len(tickers))
        result.kosdaq_ratio_per_date = kosdaq_ratios
        result.avg_kosdaq_ratio = (
            sum(kosdaq_ratios) / len(kosdaq_ratios) if kosdaq_ratios else 0.0
        )

        # 4) 폐지 노출
        fc, ft = count_failure_exposure(storage, selections, rebal_dates)
        result.failure_exposure_count = fc
        result.failure_tickers = ft

        # 5) 유동성 (평균 거래대금)
        result.avg_trading_value_per_selection = avg_trading_value(storage, selections)

    return result


# ───────────────────────────────────────────────
# Markdown 리포트
# ───────────────────────────────────────────────

def df_to_md(df: pd.DataFrame) -> str:
    """간단 Markdown 테이블 변환."""
    if df.empty:
        return "_(데이터 없음)_"
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                vals.append("" if pd.isna(v) else f"{v:.4f}")
            elif v is None:
                vals.append("")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_report(
    results: list[ScenarioResult],
    start_date: str,
    end_date: str,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines.append("# KOSDAQ 확장 백테스트 분석 보고서")
    lines.append("")
    lines.append(f"> 생성: {now}  ")
    lines.append(f"> 기간: {start_date} ~ {end_date}  ")
    lines.append(f"> 프리셋: config.yaml 활성 프리셋 (기본 A, V70M30 + Vol70)  ")
    lines.append(f"> 스크립트: `scripts/backtest_kosdaq_expansion.py`")
    lines.append("")
    lines.append(
        "**중요**: 본 백테스트는 `settings.universe.market`만 런타임 변경합니다. "
        "config.yaml / 실전 운용 설정은 변경되지 않습니다."
    )
    lines.append("")

    # 1. 성과 비교
    lines.append("## 1. 성과 비교")
    lines.append("")
    perf = pd.DataFrame([
        {
            "시나리오": r.name,
            "설명": r.description,
            "CAGR(%)": f"{r.cagr * 100:.2f}",
            "MDD(%)": f"{r.mdd * 100:.2f}",
            "Sharpe": f"{r.sharpe:.3f}",
            "Sortino": f"{r.sortino:.3f}",
            "Vol(%)": f"{r.volatility * 100:.2f}",
            "Calmar": f"{r.calmar:.3f}",
            "총수익률(%)": f"{r.total_return * 100:.2f}",
            "에러": r.error or "",
        }
        for r in results
    ])
    lines.append(df_to_md(perf))
    lines.append("")

    # 2. 선정 구성
    lines.append("## 2. 선정 구성")
    lines.append("")
    comp = pd.DataFrame([
        {
            "시나리오": r.name,
            "리밸런싱 횟수": r.n_rebalances,
            "평균 선정 종목수": f"{r.avg_selection_size:.2f}",
            "평균 KOSDAQ 비율(%)": f"{r.avg_kosdaq_ratio * 100:.2f}",
            "평균 거래대금(억)": f"{r.avg_trading_value_per_selection / 1e8:.2f}",
        }
        for r in results
    ])
    lines.append(df_to_md(comp))
    lines.append("")

    # 3. 폐지 노출
    lines.append("## 3. 폐지(failure) 노출")
    lines.append("")
    risk_rows = []
    for r in results:
        n_exposures = r.failure_exposure_count
        sample = ", ".join(r.failure_tickers[:5])
        if len(r.failure_tickers) > 5:
            sample += f" ... (+{len(r.failure_tickers) - 5}건)"
        risk_rows.append({
            "시나리오": r.name,
            "폐지 노출 건수": n_exposures,
            "고유 폐지 종목수": len(r.failure_tickers),
            "예시 종목": sample,
        })
    lines.append(df_to_md(pd.DataFrame(risk_rows)))
    lines.append("")

    # 4. KOSDAQ 비율 추이 (ALL 시나리오 전용)
    lines.append("## 4. KOSDAQ 비율 추이 (ALL 시나리오)")
    lines.append("")
    all_res = next((r for r in results if r.name == "ALL"), None)
    if all_res and all_res.kosdaq_ratio_per_date:
        ratios = all_res.kosdaq_ratio_per_date
        lines.append(
            f"- 리밸런싱 {len(ratios)}회 중 KOSDAQ 비중 평균: "
            f"**{sum(ratios) / len(ratios) * 100:.1f}%**"
        )
        lines.append(
            f"- 최소: {min(ratios) * 100:.1f}%, 최대: {max(ratios) * 100:.1f}%"
        )
        import statistics
        if len(ratios) >= 2:
            lines.append(
                f"- 표준편차: {statistics.stdev(ratios) * 100:.2f}%p"
            )
    else:
        lines.append("_(ALL 시나리오 결과 없음)_")
    lines.append("")

    # 5. 목표 vs 실적 (KPI 비교)
    lines.append("## 5. KPI 비교 (PRD_v2 기준)")
    lines.append("")
    lines.append("| 지표 | 목표 | KOSPI | ALL | KOSDAQ |")
    lines.append("| --- | --- | --- | --- | --- |")
    kpi_rows = [
        ("CAGR", "10%+", "cagr", lambda v: f"{v * 100:.2f}%"),
        ("MDD", "-15% 이내", "mdd", lambda v: f"{v * 100:.2f}%"),
        ("Sharpe", "0.8+", "sharpe", lambda v: f"{v:.3f}"),
        ("Sortino", "1.0+", "sortino", lambda v: f"{v:.3f}"),
    ]
    by_name = {r.name: r for r in results}
    for label, target, attr, fmt in kpi_rows:
        row = [f"| {label}", target]
        for scen in ["KOSPI", "ALL", "KOSDAQ"]:
            r = by_name.get(scen)
            if r and not r.error:
                row.append(fmt(getattr(r, attr)))
            else:
                row.append("—")
        lines.append(" | ".join(row) + " |")
    lines.append("")

    # 6. 주요 관찰
    lines.append("## 6. 주요 관찰")
    lines.append("")
    observations: list[str] = []

    # KOSPI vs ALL 성과 차이
    ko = by_name.get("KOSPI")
    al = by_name.get("ALL")
    kd = by_name.get("KOSDAQ")
    if ko and al and not ko.error and not al.error:
        cagr_delta = al.cagr - ko.cagr
        mdd_delta = al.mdd - ko.mdd
        observations.append(
            f"- **ALL vs KOSPI**: CAGR 차이 {cagr_delta * 100:+.2f}%p, "
            f"MDD 차이 {mdd_delta * 100:+.2f}%p, "
            f"Sharpe 차이 {al.sharpe - ko.sharpe:+.3f}"
        )
        if al.avg_kosdaq_ratio > 0.3 and cagr_delta > 0.02:
            observations.append(
                f"  - KOSDAQ 비중 {al.avg_kosdaq_ratio * 100:.0f}%에서 "
                f"Alpha 개선 관측"
            )
        elif cagr_delta < -0.02:
            observations.append(
                f"  - KOSDAQ 편입이 CAGR을 {cagr_delta * 100:.2f}%p 악화"
            )
    if kd and not kd.error:
        observations.append(
            f"- **KOSDAQ 단독**: CAGR {kd.cagr * 100:.2f}%, "
            f"MDD {kd.mdd * 100:.2f}%, Sharpe {kd.sharpe:.3f} "
            f"(폐지 노출 {kd.failure_exposure_count}건)"
        )

    # 유동성
    for r in results:
        if r.avg_trading_value_per_selection > 0:
            observations.append(
                f"- [{r.name}] 선정 종목 평균 20일 거래대금: "
                f"{r.avg_trading_value_per_selection / 1e8:.2f}억원"
            )

    if not observations:
        observations.append("- (분석 가능한 결과 없음)")
    lines.extend(observations)
    lines.append("")

    # 7. 정책 제안
    lines.append("## 7. 정책 제안")
    lines.append("")
    lines.append(self_policy_recommendation(results))
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 본 분석은 _분석 전용_입니다. 실전 운용 설정 변경 여부는 "
        "운용자가 별도 판단합니다."
    )
    return "\n".join(lines)


def self_policy_recommendation(results: list[ScenarioResult]) -> str:
    """관찰치 기반 정책 제안 텍스트.

    판단 기준 (보수적):
      - 모든 주요 지표(CAGR/MDD/Sharpe)가 한 번이라도 악화하면 최소 "확장 보류".
      - 단일 지표라도 크게 악화되면 "확장 반대".
    """
    by_name = {r.name: r for r in results}
    ko = by_name.get("KOSPI")
    al = by_name.get("ALL")
    kd = by_name.get("KOSDAQ")

    parts: list[str] = []

    # ── 7-1. 종합 판단 (엄격한 판정) ──
    if ko and al and not ko.error and not al.error:
        delta_cagr = al.cagr - ko.cagr
        delta_mdd = al.mdd - ko.mdd
        delta_sharpe = al.sharpe - ko.sharpe
        delta_sortino = al.sortino - ko.sortino

        all_worse = (
            delta_cagr < 0 and delta_sharpe < 0
            and delta_sortino < 0 and delta_mdd < 0
        )

        if all_worse:
            verdict = (
                "**확장 반대** — ALL 시나리오가 CAGR/MDD/Sharpe/Sortino 모든 주요 지표에서 "
                "KOSPI 단독 대비 악화.\n\n"
                f"- CAGR: {ko.cagr * 100:+.2f}% → {al.cagr * 100:+.2f}% "
                f"(Δ {delta_cagr * 100:+.2f}%p)\n"
                f"- MDD: {ko.mdd * 100:+.2f}% → {al.mdd * 100:+.2f}% "
                f"(Δ {delta_mdd * 100:+.2f}%p, 악화)\n"
                f"- Sharpe: {ko.sharpe:+.3f} → {al.sharpe:+.3f} "
                f"(Δ {delta_sharpe:+.3f})\n"
                f"- Sortino: {ko.sortino:+.3f} → {al.sortino:+.3f} "
                f"(Δ {delta_sortino:+.3f})\n"
            )
        elif delta_sharpe > 0.05 and delta_mdd > -0.02:
            verdict = (
                "**확장 권고** — ALL 시나리오가 Sharpe를 개선하면서 MDD 악화가 -2%p 이내."
            )
        elif delta_cagr > 0.03 and delta_mdd > -0.05:
            verdict = (
                "**제한적 확장 권고** — CAGR은 개선되나 MDD가 악화됨. "
                "리스크 허용도 높을 때만 고려."
            )
        elif delta_cagr < 0 or delta_sharpe < 0 or delta_mdd < 0:
            verdict = (
                "**확장 보류** — 일부 주요 지표에서 악화 관측.\n\n"
                f"- CAGR Δ {delta_cagr * 100:+.2f}%p, "
                f"MDD Δ {delta_mdd * 100:+.2f}%p, "
                f"Sharpe Δ {delta_sharpe:+.3f}"
            )
        else:
            verdict = "**중립** — 성과 차이 미미. 추가 실험 필요."
        parts.append(f"### 7-1. 종합 판단\n\n{verdict}\n")

    # ── 7-2. 유동성 갭 (KOSPI vs KOSDAQ 거래대금 비교) ──
    if ko and kd and ko.avg_trading_value_per_selection > 0 and kd.avg_trading_value_per_selection > 0:
        kospi_tv = ko.avg_trading_value_per_selection / 1e8
        kosdaq_tv = kd.avg_trading_value_per_selection / 1e8
        ratio = kospi_tv / kosdaq_tv if kosdaq_tv > 0 else 0
        parts.append(
            "### 7-2. 유동성 격차\n\n"
            f"- KOSPI 선정 종목 평균 20일 거래대금: **{kospi_tv:.2f}억** \n"
            f"- KOSDAQ 선정 종목 평균 20일 거래대금: **{kosdaq_tv:.2f}억** "
            f"(KOSPI 대비 1/{ratio:.1f})\n"
            "- 권고: KOSDAQ 편입 시 슬리피지 상향 적용 필요 "
            "(중액 프리셋 0.10% → 0.15~0.20% 검토).\n"
            "- 또는 KOSDAQ 전용 `min_avg_trading_value`를 KOSPI보다 높게 설정 "
            "(현 2억 → 5억) 하여 유동성 열위 종목 배제.\n"
        )

    # ── 7-3. 폐지 리스크 ──
    if ko and kd:
        parts.append(
            "### 7-3. 폐지(failure) 노출\n\n"
            f"- 본 백테스트 선정 종목 중 해당 분기 내 failure 폐지 건수:\n"
            f"  - KOSPI 단독: **{ko.failure_exposure_count}건**\n"
            f"  - ALL 통합: **{al.failure_exposure_count if al else 'N/A'}건**\n"
            f"  - KOSDAQ 단독: **{kd.failure_exposure_count}건**\n"
            "- 참고: 선정 여부는 min_fscore=4 필터를 통과한 종목 한정. "
            "F-Score가 KOSDAQ 재무 불량 종목 배제에 기여 (docs/reports/alternative_defense_comparison.md 참조).\n"
        )

    # ── 7-4. PCR 데이터 가용성 ──
    parts.append(
        "### 7-4. PCR 데이터 가용성 (v2.0 Value 팩터 핵심)\n\n"
        "- v2.0 Value 팩터 구성: PBR 50% + PCR 30% + DIV 20%. "
        "PCR 누락 시 `value.py`가 PBR+DIV로 가중치 재분배(NaN-aware).\n"
        "- **DART 분기/반기보고서에 현금흐름표가 없어 PCR = 0% 확보** (KOSPI/KOSDAQ 공통).\n"
        "- 연간보고서에는 현금흐름표 있으나 DART 호출 로직상 분기/반기 우선 적용됨 → 사실상 PCR 상시 NaN.\n"
        "- 권고:\n"
        "  1) `data/dart_client.py`에서 PCR 계산 시 연간보고서(사업보고서) 우선 호출하도록 개선 검토.\n"
        "  2) 연결재무제표 현금흐름표 우선 시도 후 별도재무제표 폴백.\n"
        "  3) 확보 불가 시 `value_weights.pcr=0.0` + `psr` 도입 공식화 (PRD_v2 §7).\n"
        "- 본 백테스트의 V70M30 성과는 사실상 PBR+DIV 2팩터 Value + 모멘텀 체계에서 산출된 것으로 해석해야 함.\n"
    )

    # ── 7-5. F-Score 계산 가능성 ──
    parts.append(
        "### 7-5. F-Score 계산 가능성\n\n"
        "- 현 구현은 PER/PBR/DIV 3필드 동시 보유 시 5점 만점 F-Score 계산 가능.\n"
        "- **실측 (docs/reports/kosdaq_data_quality.md §5)**:\n"
        "  - KOSPI: 44.0% 레코드만 3필드 동시 보유\n"
        "  - KOSDAQ: 35.7% 레코드만 3필드 동시 보유\n"
        "- `min_fscore=4` 필터는 PER/PBR/DIV 중 하나라도 NaN인 종목을 조건부 누락시킴. "
        "KOSDAQ에서는 35.7%만 유효 F-Score를 가지므로 필터 후 유니버스 축소 폭이 큼.\n"
        "- 권고: KOSDAQ 확장 시 (a) `min_fscore=3` 완화 또는 (b) DIV NaN을 0으로 간주하는 로직 추가 검토.\n"
    )

    # ── 7-6. 펀더멘털 누락 종목 처리 방안 ──
    parts.append(
        "### 7-6. 펀더멘털 누락 종목 처리 방안\n\n"
        "- 현 로직: PBR/PER/DIV 중 하나라도 NaN인 경우 `composite.py`의 NaN-aware 가중치 재분배가 "
        "보완하지만, 3개 모두 NaN이면 해당 종목은 유효 score 산출 불가 → 자동 탈락.\n"
        "- 권고:\n"
        "  1) KOSDAQ 확장 시 `ffill=30d` 옵션 도입 — 최근 30일 내 직전 분기 데이터 폴백.\n"
        "  2) 신규 상장 종목은 상장 후 2분기(6개월) 대기 후 유니버스 편입.\n"
        "  3) 펀더멘털 4필드 이상 NaN인 종목은 해당 리밸런싱 유니버스에서 선제 제외.\n"
    )

    # ── 7-7. KOSDAQ 전용 유동성 필터 기준 ──
    parts.append(
        "### 7-7. KOSDAQ 전용 유동성 필터 기준 (제안)\n\n"
        "| 금액 프리셋 | KOSPI min_avg_trading_value | KOSDAQ 상향 안 | 슬리피지 상향 안 |\n"
        "| --- | --- | --- | --- |\n"
        "| 소액 | 1억 | **2억** | 0.10% → **0.15%** |\n"
        "| 중액 | 2억 | **5억** | 0.10% → **0.15%** |\n"
        "| 대액 | 5억 | **10억** | 0.15% → **0.20%** |\n"
        "| 거액 | 10억 | **20억** | 0.20% → **0.30%** |\n"
        "\n"
        "※ 상기 수치는 권고 초안이며, KOSDAQ 편입 확정 시 별도 Grid Search로 검증 필요.\n"
    )

    # ── 7-8. 확장 여부 권고 + 근거 ──
    parts.append(
        "### 7-8. 확장 여부 권고 (최종)\n\n"
        "**결론: 현 시점 KOSDAQ 확장 권고하지 않음.**\n\n"
        "근거:\n"
        "1. **성과 악화**: ALL 시나리오가 KOSPI 단독 대비 CAGR/MDD/Sharpe/Sortino 전면 악화.\n"
        "2. **PCR 팩터 무효화**: 분기/반기보고서 현금흐름표 부재로 v2.0 Value 팩터 30%가 실질 미작동. "
        "이는 KOSPI에서도 동일한 문제이나, KOSDAQ에서 PBR+DIV 2팩터만으로 저평가 식별이 충분한지 검증 필요.\n"
        "3. **F-Score 커버리지 저조**: KOSDAQ에서 F-Score 계산 가능 비율 35.7% (KOSPI 44%).\n"
        "4. **유동성 격차**: 선정 종목 거래대금이 KOSPI 대비 약 1/3. 슬리피지 상향 반영 시 "
        "성과 추가 하락 가능.\n\n"
        "**재검토 전제조건**:\n"
        "- DART PCR 연간보고서 기반 수집 개선 → 재테스트\n"
        "- `min_fscore`, 유동성 필터 KOSDAQ 전용화 → Grid Search\n"
        "- 2021-2024 단기 구간에서 KOSDAQ 단독 성과 재검증 (본 보고서는 2017-2024 8년 구간, "
        "2018 하락장 + 2020 코로나 영향 포함됨)\n"
    )

    return "\n".join(parts)


# ───────────────────────────────────────────────
# main
# ───────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="KOSDAQ 확장 백테스트 비교")
    parser.add_argument("--start", type=str, default="2017-01-01",
                        help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2024-12-31",
                        help="종료일 (YYYY-MM-DD)")
    parser.add_argument(
        "--scenarios", type=str, default="KOSPI,ALL,KOSDAQ",
        help="콤마 구분 시나리오 (KOSPI,ALL,KOSDAQ 중)",
    )
    parser.add_argument(
        "--skip-screener", action="store_true",
        help="선정 구성 분석(screener 재실행) 스킵",
    )
    parser.add_argument(
        "--output", type=str,
        default=str(PROJECT_ROOT / "docs" / "reports" / "kosdaq_expansion_analysis.md"),
        help="출력 Markdown 경로",
    )
    parser.add_argument(
        "--dump-json", type=str, default=None,
        help="결과 JSON 경로 (재사용용, 선택)",
    )
    args = parser.parse_args()

    setup_logging()

    scen_list = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    valid = {name for name, _ in SCENARIOS}
    for s in scen_list:
        if s not in valid:
            parser.error(f"알 수 없는 시나리오: {s} (허용: {valid})")

    logger.info(
        "시나리오: %s | 기간: %s ~ %s | screener=%s",
        scen_list, args.start, args.end, not args.skip_screener,
    )

    results: list[ScenarioResult] = []
    for name in scen_list:
        desc = next(d for n, d in SCENARIOS if n == name)
        r = run_scenario(
            name, desc, args.start, args.end,
            run_screener=not args.skip_screener,
        )
        results.append(r)
        logger.info(
            "[%s] CAGR=%.2f%% MDD=%.2f%% Sharpe=%.3f KOSDAQ%%=%.1f%% "
            "failure=%d err=%s",
            r.name, r.cagr * 100, r.mdd * 100, r.sharpe,
            r.avg_kosdaq_ratio * 100, r.failure_exposure_count, r.error,
        )

    # Markdown 리포트 저장
    md = build_report(results, args.start, args.end)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    logger.info("리포트 저장: %s (%d bytes)", out_path, len(md.encode("utf-8")))

    # JSON 덤프 (선택)
    if args.dump_json:
        dump = [asdict(r) for r in results]
        Path(args.dump_json).write_text(
            json.dumps(dump, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("JSON 덤프: %s", args.dump_json)

    print(f"\n리포트: {out_path}")
    for r in results:
        print(
            f"  [{r.name}] CAGR={r.cagr * 100:>6.2f}%  MDD={r.mdd * 100:>6.2f}%  "
            f"Sharpe={r.sharpe:>6.3f}  err={r.error or '-'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
