"""scripts/analyze_deflated_sharpe_v2.py — DSR + 통계적 유의성 검정 V2.

현재 전략(V70M30, Sharpe 0.245)의 통계적 유의성을 검정.
"이 Sharpe가 운인가 실력인가"에 대한 공식 답.

측정 지표:
1. PSR (Probabilistic Sharpe Ratio, Bailey & López de Prado 2014)
   — 수익률의 skew/kurtosis 보정 후 "SR > SR*" 확률
2. DSR (Deflated Sharpe Ratio)
   — N회 시행 중 최대값 기대치(선택 편향) 추가 보정
3. t-statistic (Opdyke 2007 보정식)
4. MinTRL (Minimum Track Record Length)
   — 현재 Sharpe가 α=0.95에서 유의해지려면 최소 몇 개월 필요한지

사용:
    python scripts/analyze_deflated_sharpe_v2.py
"""

from __future__ import annotations

import logging
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kurtosis as scipy_kurt
from scipy.stats import norm
from scipy.stats import skew as scipy_skew

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────

RANDOM_SEED    = 42
BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "deflated_sharpe_v2_analysis.md"

# 다중 시행 파라미터 (보수적 추정)
N_TRIALS = 20          # 그리드 탐색에서 시도한 전략 변형 수

# 시행들의 연율화 Sharpe 표준편차 (그리드 탐색 결과에서 추정)
# 실험 Sharpe 분포: [0.205, 0.212, 0.240, 0.245, 0.259, 0.263, 0.273]
SR_STD_ANNUAL = 0.025

# 무위험수익률 (연율화)
RF_ANNUAL = 0.03

# 신뢰수준
CONFIDENCE = 0.95

# V4 결과 참조 (KOSPI B&H, ~20bp baseline)
KOSPI_SHARPE_ANNUAL = 0.017
KOSPI_CAGR = 0.0214

# V3 IC/IR 결과 참조 (보고서 연계용)
V3_VALUE_IR      = 0.572
V3_MOMENTUM_IR   = -0.057
V3_QUALITY_IR    = -0.221
V3_COMPOSITE_IR  = 0.533

# 오일러-마스케로니 상수
EULER_GAMMA = 0.5772156649015328


# ── 핵심 통계 함수 ─────────────────────────────────────────────────────────────

def _se_sr(sr_m: float, skewness: float, excess_kurt: float) -> float:
    """월간 Sharpe Ratio 표준오차 (분자, √T 제외).

    Bailey & López de Prado (2014), Eq. 7:
      SE(SR̂)² ≈ (1 + 0.5·SR̂² - γ₃·SR̂ + (γ₄/4)·SR̂²) / (T-1)

    이 함수는 분자 √(...)만 반환 (T는 PSR/MinTRL에서 직접 사용).

    Args:
        sr_m: 월간 Sharpe ratio
        skewness: 월간 수익률 skewness (γ₃)
        excess_kurt: 월간 수익률 excess kurtosis (γ₄, 정규분포=0)

    Returns:
        √(1 + 0.5·SR² - skew·SR + (excess_kurt/4)·SR²)
    """
    inner = 1.0 + 0.5 * sr_m**2 - skewness * sr_m + (excess_kurt / 4.0) * sr_m**2
    # 수치 안정성: inner < 0 방지
    return math.sqrt(max(inner, 1e-9))


def psr(
    sr_m: float,
    sr_ref_m: float,
    T: int,
    skewness: float,
    excess_kurt: float,
) -> float:
    """Probabilistic Sharpe Ratio.

    P(SR > SR*) = Φ((SR̂ - SR*) · √(T-1) / SE_SR)

    Args:
        sr_m: 관측된 월간 Sharpe ratio
        sr_ref_m: 기준 월간 Sharpe ratio (SR*)
        T: 관측 수 (월)
        skewness: 월간 수익률 skewness
        excess_kurt: 월간 수익률 excess kurtosis

    Returns:
        PSR ∈ (0, 1)
    """
    se = _se_sr(sr_m, skewness, excess_kurt)
    z = (sr_m - sr_ref_m) * math.sqrt(T - 1) / se
    return float(norm.cdf(z))


def expected_max_sr(N: int, sr_std_m: float) -> float:
    """E[max(SR_N)]: N회 시행 중 최대 Sharpe의 기댓값.

    Bailey & López de Prado (2014), Eq. 13:
      E[max(SR_N)] ≈ SR_std · [(1-γ)·Z_{1-1/N} + γ·Z_{1-1/(N·e)}]

    where γ = Euler-Mascheroni constant ≈ 0.5772
    """
    if N <= 1:
        return 0.0
    z1 = float(norm.ppf(1.0 - 1.0 / N))
    z2 = float(norm.ppf(1.0 - 1.0 / (N * math.e)))
    return sr_std_m * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def dsr(
    sr_m: float,
    T: int,
    skewness: float,
    excess_kurt: float,
    N_trials: int,
    sr_std_m: float,
) -> float:
    """Deflated Sharpe Ratio.

    다중 시행의 선택 편향을 보정한 PSR.
    DSR = PSR(SR̂, E[max(SR_N)], T, skew, kurt)

    Args:
        sr_m: 관측된 월간 Sharpe (시행 중 최고값)
        T: 관측 수 (월)
        skewness: 월간 수익률 skewness
        excess_kurt: 월간 수익률 excess kurtosis
        N_trials: 백테스트 시도 횟수
        sr_std_m: 월간 Sharpe 표준편차 (시행들 간 분산)

    Returns:
        DSR ∈ (0, 1)
    """
    e_max = expected_max_sr(N_trials, sr_std_m)
    return psr(sr_m, e_max, T, skewness, excess_kurt)


def t_statistic(sr_m: float, T: int, skewness: float, excess_kurt: float) -> float:
    """Sharpe Ratio t-통계량 (비정규 보정, Opdyke 2007).

    t = SR̂_monthly · √T / √(1 + 0.5·SR̂² - skew·SR̂ + (kurt/4)·SR̂²)

    H₀: SR = 0

    Returns:
        t-statistic (float)
    """
    se = _se_sr(sr_m, skewness, excess_kurt)
    return sr_m * math.sqrt(T) / se


def min_trl(
    sr_m: float,
    sr_ref_m: float,
    skewness: float,
    excess_kurt: float,
    confidence: float = 0.95,
) -> float:
    """Minimum Track Record Length (월 단위).

    PSR ≥ α 조건 → T 하한:
      T_min = 1 + SE² · (Z_α / (SR - SR*))²

    Args:
        sr_m: 관측된 월간 Sharpe
        sr_ref_m: 기준 월간 Sharpe (SR*)
        skewness, excess_kurt: 수익률 분포 모수
        confidence: 목표 신뢰수준 (기본 0.95)

    Returns:
        최소 관측 수 (월). SR ≤ SR*이면 inf.
    """
    if sr_m <= sr_ref_m:
        return float("inf")
    se2 = _se_sr(sr_m, skewness, excess_kurt) ** 2
    z_alpha = float(norm.ppf(confidence))
    return 1.0 + se2 * (z_alpha / (sr_m - sr_ref_m)) ** 2


# ── 백테스트 실행 ─────────────────────────────────────────────────────────────

def run_backtest() -> pd.DataFrame:
    """Preset A (V70M30) 설정으로 백테스트 실행 → 일별 NAV DataFrame 반환."""
    from backtest.engine import MultiFactorBacktest

    np.random.seed(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    logger.info("백테스트 실행 (Preset A, %s ~ %s)...", BACKTEST_START, BACKTEST_END)
    engine = MultiFactorBacktest()
    df = engine.run(BACKTEST_START, BACKTEST_END, market=MARKET)

    if df is None or df.empty:
        raise RuntimeError("백테스트 결과 없음")

    logger.info("백테스트 완료: %d일 NAV", len(df))
    return df


def get_monthly_returns(df: pd.DataFrame) -> pd.Series:
    """일별 NAV DataFrame → 월말 리샘플 → 월간 수익률 Series.

    Args:
        df: engine.run() 반환값 (index=date, portfolio_value 컬럼)

    Returns:
        월간 수익률 Series (index=month_end_date, values=return)
    """
    pv = df["portfolio_value"].copy()
    pv.index = pd.to_datetime(pv.index)

    # pandas 2.2+ : 'ME' (month-end), 구버전 호환 위해 try-except
    try:
        monthly_pv = pv.resample("ME").last()
    except ValueError:
        monthly_pv = pv.resample("M").last()

    monthly_ret = monthly_pv.pct_change().dropna()
    logger.info("월간 수익률 추출: %d개월", len(monthly_ret))
    return monthly_ret


def fetch_kospi_monthly() -> pd.Series:
    """KOSPI 월간 수익률 (DB 캐시 → 폴백).

    Returns:
        월간 수익률 Series. 실패 시 빈 Series.
    """
    from datetime import datetime as _dt

    s_date = _dt.strptime(BACKTEST_START, "%Y-%m-%d").date()
    e_date = _dt.strptime(BACKTEST_END, "%Y-%m-%d").date()

    try:
        from data.storage import DataStorage
        storage = DataStorage()
        cached = storage.load_daily_prices("KOSPI", s_date, e_date)
        if not cached.empty and len(cached) >= 1500:
            closes = cached["close"].sort_index().dropna()
            closes.index = pd.to_datetime(closes.index)
            try:
                monthly = closes.resample("ME").last()
            except ValueError:
                monthly = closes.resample("M").last()
            ret = monthly.pct_change().dropna()
            if len(ret) >= 60:
                logger.info("KOSPI 월간 수익률: DB 캐시 %d개월", len(ret))
                return ret
    except Exception as e:
        logger.warning("KOSPI DB 조회 실패: %s", e)

    logger.warning("KOSPI 월간 수익률 확보 실패 — 연율 Sharpe 0.017 사용")
    return pd.Series(dtype=float)


# ── 분석 ─────────────────────────────────────────────────────────────────────

def annualize_sr(sr_m: float) -> float:
    return sr_m * math.sqrt(12.0)


def monthly_sr_from_annual(sr_a: float) -> float:
    return sr_a / math.sqrt(12.0)


def run_analysis(monthly_ret: pd.Series, kospi_monthly_ret: pd.Series) -> dict:
    """DSR/PSR/t-stat/MinTRL 전체 계산.

    Returns:
        결과 dict
    """
    T = len(monthly_ret)
    sr_m       = float(monthly_ret.mean() / monthly_ret.std(ddof=1))
    sr_annual  = annualize_sr(sr_m)
    skewness   = float(scipy_skew(monthly_ret))
    excess_k   = float(scipy_kurt(monthly_ret, fisher=True))  # excess kurtosis
    vol_m      = float(monthly_ret.std(ddof=1))
    vol_annual = vol_m * math.sqrt(12.0)

    # RF (월간)
    rf_m = (1 + RF_ANNUAL) ** (1 / 12) - 1
    # Excess SR (vs RF)
    sr_m_excess = (monthly_ret - rf_m).mean() / monthly_ret.std(ddof=1)

    # KOSPI 월간 Sharpe
    if not kospi_monthly_ret.empty and len(kospi_monthly_ret) >= 30:
        kospi_sr_m = float(
            (kospi_monthly_ret.mean() - rf_m) / kospi_monthly_ret.std(ddof=1)
        )
    else:
        kospi_sr_m = monthly_sr_from_annual(KOSPI_SHARPE_ANNUAL)

    kospi_sr_annual = annualize_sr(kospi_sr_m)

    # SR_std (월간 단위로 변환)
    sr_std_m = SR_STD_ANNUAL / math.sqrt(12.0)

    # ── PSR ──────────────────────────────────────────────────────────────────
    psr_vs_zero  = psr(sr_m_excess, 0.0,         T, skewness, excess_k)
    psr_vs_kospi = psr(sr_m_excess, kospi_sr_m,  T, skewness, excess_k)

    # ── DSR ──────────────────────────────────────────────────────────────────
    dsr_val = dsr(sr_m_excess, T, skewness, excess_k, N_TRIALS, sr_std_m)

    e_max_sr_m = expected_max_sr(N_TRIALS, sr_std_m)
    e_max_sr_a = annualize_sr(e_max_sr_m)

    # ── t-statistic ──────────────────────────────────────────────────────────
    t_stat = t_statistic(sr_m_excess, T, skewness, excess_k)
    p_val  = float(1.0 - norm.cdf(t_stat))  # 단측 (H₁: SR > 0)

    # ── MinTRL ───────────────────────────────────────────────────────────────
    min_trl_zero_m  = min_trl(sr_m_excess, 0.0,        skewness, excess_k, CONFIDENCE)
    min_trl_kospi_m = min_trl(sr_m_excess, kospi_sr_m, skewness, excess_k, CONFIDENCE)
    min_trl_zero_yr  = min_trl_zero_m / 12.0
    min_trl_kospi_yr = min_trl_kospi_m / 12.0

    # MinTRL: SR 개선 시나리오 (Sharpe 0.30)
    sr_hypothetical_a = 0.30
    sr_hyp_m = monthly_sr_from_annual(sr_hypothetical_a)
    min_trl_hyp_zero_m = min_trl(sr_hyp_m, 0.0, skewness, excess_k, CONFIDENCE)
    min_trl_hyp_zero_yr = min_trl_hyp_zero_m / 12.0

    return {
        "T": T,
        "sr_monthly": sr_m,
        "sr_monthly_excess": sr_m_excess,
        "sr_annual": sr_annual,
        "skewness": skewness,
        "excess_kurtosis": excess_k,
        "vol_monthly": vol_m,
        "vol_annual": vol_annual,
        "rf_monthly": rf_m,
        "kospi_sr_monthly": kospi_sr_m,
        "kospi_sr_annual": kospi_sr_annual,
        "sr_std_monthly": sr_std_m,
        "e_max_sr_monthly": e_max_sr_m,
        "e_max_sr_annual": e_max_sr_a,
        "psr_vs_zero": psr_vs_zero,
        "psr_vs_kospi": psr_vs_kospi,
        "dsr": dsr_val,
        "t_stat": t_stat,
        "p_value": p_val,
        "min_trl_zero_months": min_trl_zero_m,
        "min_trl_zero_years": min_trl_zero_yr,
        "min_trl_kospi_months": min_trl_kospi_m,
        "min_trl_kospi_years": min_trl_kospi_yr,
        "min_trl_hyp_months": min_trl_hyp_zero_m,
        "min_trl_hyp_years": min_trl_hyp_zero_yr,
        "sr_hypothetical_annual": sr_hypothetical_a,
    }


# ── 판정 ─────────────────────────────────────────────────────────────────────

def verdict(r: dict) -> str:
    dsr_val = r["dsr"]
    if dsr_val > 0.95:
        return "✅ 통계적으로 유의한 alpha — DSR > 0.95"
    if dsr_val > 0.50:
        return "⚠️ 유의하지 않지만 양의 신호 — DSR 0.50~0.95"
    return "❌ 운과 구분 불가 — DSR < 0.50"


# ── 보고서 ────────────────────────────────────────────────────────────────────

def build_report(r: dict, monthly_ret: pd.Series) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _f(v: float, d: int = 3) -> str:
        return f"{v:.{d}f}" if not (v != v) else "—"  # NaN check

    def _pct(v: float, d: int = 2) -> str:
        return f"{v * 100:.{d}f}%" if not (v != v) else "—"

    def _inf(v: float, d: int = 1) -> str:
        if v == float("inf"):
            return "∞"
        return f"{v:.{d}f}"

    lines = [
        "# Deflated Sharpe Ratio + 통계적 유의성 검정 V2",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END}  ",
        f"시장: {MARKET} | 전략: V70M30 (Preset A)",
        "",
        "## 기본 통계",
        "",
        "| 항목 | 값 |",
        "|------|---|",
        f"| 연율화 Sharpe (excess vs RF) | {_f(r['sr_annual'])} |",
        f"| 월간 Sharpe | {_f(r['sr_monthly_excess'])} |",
        f"| 연율화 변동성 | {_pct(r['vol_annual'])} |",
        f"| 월간 관측치 (T) | {r['T']}개월 |",
        f"| 월간 수익률 Skewness | {_f(r['skewness'])} |",
        f"| 월간 수익률 Excess Kurtosis | {_f(r['excess_kurtosis'])} |",
        f"| N_trials (시행 횟수) | {N_TRIALS} (보수적) |",
        f"| SR_std 연율화 (추정) | {_f(SR_STD_ANNUAL)} |",
        f"| E[max(SR_N)] 연율화 | {_f(r['e_max_sr_annual'])} |",
        f"| KOSPI 연율화 Sharpe | {_f(r['kospi_sr_annual'])} |",
        "",
        "## PSR / DSR",
        "",
        "| 지표 | 값 | 해석 |",
        "|------|---|------|",
        f"| **DSR** | **{_f(r['dsr'])}** | "
        f"{'> 0.95: 유의' if r['dsr'] > 0.95 else ('> 0.50: 양의 신호' if r['dsr'] > 0.50 else '< 0.50: 운과 구분 불가')} |",
        f"| PSR (vs SR*=0) | {_f(r['psr_vs_zero'])} | > 0.95면 유의 |",
        f"| PSR (vs KOSPI) | {_f(r['psr_vs_kospi'])} | > 0.95면 KOSPI 초과 유의 |",
        "",
        "## t-statistic (비정규 보정)",
        "",
        "| 항목 | 값 | 해석 |",
        "|------|---|------|",
        f"| t-statistic | {_f(r['t_stat'])} | > 1.645면 p < 0.05 (단측) |",
        f"| p-value (단측) | {_f(r['p_value'], 4)} | |",
        "",
        "## Minimum Track Record Length (MinTRL)",
        "",
        "| 벤치마크 | MinTRL (월) | MinTRL (년) | 해석 |",
        "|----------|-----------|-----------|------|",
        f"| SR* = 0 | {_inf(r['min_trl_zero_months'])} | "
        f"{_inf(r['min_trl_zero_years'])} | Sharpe > 0 증명에 필요한 최소 기간 |",
        f"| SR* = KOSPI ({_f(r['kospi_sr_annual'])}) | {_inf(r['min_trl_kospi_months'])} | "
        f"{_inf(r['min_trl_kospi_years'])} | KOSPI 초과 증명에 필요한 최소 기간 |",
        "",
        "**현재 관측 기간**: 8년 (96개월)  ",
        f"**MinTRL vs SR*=0**: {_inf(r['min_trl_zero_years'])}년 → 현재 기간 대비 부족  ",
        "",
        "## 종합 판정",
        "",
        f"> **{verdict(r)}**",
        "",
        "## MinTRL 해석",
        "",
        "MinTRL이 크다는 것은 전략이 나쁘다는 의미가 아님. ",
        "Sharpe 0.245 수준의 전략은 정의상 통계적 증명에 긴 기간이 필요함.",
        "",
        f"- Sharpe 0.245 → MinTRL ≈ {_inf(r['min_trl_zero_years'])}년 (SR*=0 기준)",
        f"- Sharpe {r['sr_hypothetical_annual']:.2f} (개선 가정) → MinTRL ≈ {_inf(r['min_trl_hyp_years'])}년",
        f"- Sharpe 0.50 (강한 전략) → MinTRL ≈ {_inf(1 + (1.645 / monthly_sr_from_annual(0.50))**2 / 12)}년",
        f"- Sharpe 1.00 (헤지펀드급) → MinTRL ≈ {_inf(1 + (1.645 / monthly_sr_from_annual(1.00))**2 / 12)}년",
        "",
        "## V3 연계: 팩터 구성 변경 시 DSR 개선 가능성",
        "",
        "V3 IC/IR 분석 결과:",
        "",
        "| 팩터 | IR | 해석 |",
        "|------|---|------|",
        f"| Value 합산 | +{V3_VALUE_IR:.3f} | ★★★ 강한 예측력 |",
        f"| Momentum 합산 | {V3_MOMENTUM_IR:+.3f} | ✗ 예측력 없음 |",
        f"| Quality 합산 | {V3_QUALITY_IR:+.3f} | ✗ 예측력 없음 |",
        f"| Composite V70M30 | +{V3_COMPOSITE_IR:.3f} | ★★★ |",
        "",
        "**팩터 구성 개선 → DSR 개선 경로**:",
        "",
        f"1. **Momentum 음수 IC 영향**: 현재 V70M30에서 Momentum(IR={V3_MOMENTUM_IR:+.3f})이 "
        f"Sharpe를 하방 압력. Value 단독(IR={V3_VALUE_IR:+.3f}) > Composite(IR={V3_COMPOSITE_IR:+.3f}).",
        "",
        f"2. **Value 단독 시나리오 (Preset C, V=1.00)**: Momentum 제거 시 Sharpe가 "
        f"{r['sr_annual']:.3f} → {r['sr_hypothetical_annual']:.2f} 수준으로 개선될 경우  ",
        f"   MinTRL이 {_inf(r['min_trl_zero_years'])}년 → {_inf(r['min_trl_hyp_years'])}년으로 단축.  ",
        f"   (현재 8년 관측치 기준 PSR 유의수준: {_f(psr(monthly_sr_from_annual(r['sr_hypothetical_annual']), 0.0, r['T'], r['skewness'], r['excess_kurtosis']))} → 여전히 < 0.95지만 접근)",
        "",
        "3. **근본 한계**: 8년 데이터는 Sharpe < 1.0인 전략을 통계적으로 '증명'하기에 구조적으로 부족.  ",
        "   DSR은 데이터 부족 경고지 전략 무효 판정이 아님.  ",
        "   실전 운용 성과(out-of-sample) 축적이 가장 강력한 검정.",
        "",
        "## 참고 문헌",
        "",
        "- Bailey, D.H. & López de Prado, M. (2014). "
        "*The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, "
        "and Non-Normality*. Journal of Portfolio Management.",
        "- Opdyke, J.D. (2007). *Comparing Sharpe Ratios: So Where Are the p-values?* "
        "Journal of Asset Management.",
    ]

    return "\n".join(lines) + "\n"


# ── ASCII 요약 ────────────────────────────────────────────────────────────────

def print_summary(r: dict) -> None:
    def _f(v: float, d: int = 3) -> str:
        return f"{v:.{d}f}"

    def _inf_yr(v: float) -> str:
        return "∞" if v == float("inf") else f"{v:.1f}년"

    print()
    print("=" * 65)
    print("Deflated Sharpe Ratio + 통계적 유의성 검정 V2  (2017-2024)")
    print("=" * 65)
    print()
    print("── 기본 통계 ──────────────────────────────────────────────────")
    print(f"  월간 관측치:          {r['T']}개월 (8년)")
    print(f"  연율화 Sharpe:        {_f(r['sr_annual'])}")
    print(f"  연율화 변동성:        {r['vol_annual']*100:.2f}%")
    print(f"  Skewness:             {_f(r['skewness'])}")
    print(f"  Excess Kurtosis:      {_f(r['excess_kurtosis'])}")
    print(f"  N_trials:             {N_TRIALS}")
    print(f"  E[max SR] 연율화:     {_f(r['e_max_sr_annual'])}")
    print()
    print("── PSR / DSR ──────────────────────────────────────────────────")
    print(f"  DSR:                  {_f(r['dsr'])}  {'> 0.95 ✅' if r['dsr']>0.95 else ('> 0.50 ⚠️' if r['dsr']>0.50 else '< 0.50 ❌')}")
    print(f"  PSR (vs SR*=0):       {_f(r['psr_vs_zero'])}")
    print(f"  PSR (vs KOSPI):       {_f(r['psr_vs_kospi'])}")
    print()
    print("── t-statistic ────────────────────────────────────────────────")
    print(f"  t-stat:               {_f(r['t_stat'])}  ({'> 1.645 ✅' if r['t_stat']>1.645 else '< 1.645 ❌'})")
    print(f"  p-value (단측):       {r['p_value']:.4f}")
    print()
    print("── Minimum Track Record Length ────────────────────────────────")
    print(f"  SR*=0:                {_inf_yr(r['min_trl_zero_years'])}  ({r['min_trl_zero_months']:.0f}개월)")
    print(f"  SR*=KOSPI:            {_inf_yr(r['min_trl_kospi_years'])}  ({r['min_trl_kospi_months']:.0f}개월)")
    print(f"  SR={r['sr_hypothetical_annual']:.2f} 개선 가정 MinTRL: {_inf_yr(r['min_trl_hyp_years'])}  ({r['min_trl_hyp_months']:.0f}개월)")
    print()
    print("── 종합 판정 ──────────────────────────────────────────────────")
    print(f"  {verdict(r)}")
    print()
    print("── V3 연계 ────────────────────────────────────────────────────")
    print(f"  Value IR={V3_VALUE_IR:+.3f} (★★★), Momentum IR={V3_MOMENTUM_IR:+.3f} (✗)")
    print("  Momentum 음수 IC → Sharpe 하방 압력. Value 단독 시나리오 검토 권장.")
    print()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    logger.info("Deflated Sharpe Ratio 분석 V2 시작")

    # 1. 백테스트 실행
    df = run_backtest()

    # 2. 월간 수익률 추출
    monthly_ret = get_monthly_returns(df)
    if len(monthly_ret) < 24:
        logger.error("월간 수익률 부족 (%d개월) — 종료", len(monthly_ret))
        return

    # 3. KOSPI 월간 수익률
    kospi_monthly = fetch_kospi_monthly()

    # 4. 분석
    r = run_analysis(monthly_ret, kospi_monthly)

    # 5. 터미널 출력
    print_summary(r)

    # 6. 보고서 저장
    report_text = build_report(r, monthly_ret)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)
    print(f"  보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
