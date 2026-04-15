"""005620 Reporting Lag 미작동 버그 재현 스크립트.

Context:
- strategy/screener.py에 `_get_effective_fundamental_date()` 함수가 정의되어 있으나
  실제 screen() 경로에서는 호출되지 않음.
- DART의 `_determine_report_period()`는 6월부터 Q1 데이터 사용을 허용하므로
  2017-06-30 리밸런싱에서 005620의 "흑자 전환된" Q1 재무 데이터가 F-Score에 반영됨.

본 스크립트는 두 모드로 F-Score를 계산하여 차이를 검증:
  Mode A (현재): date=20170630 그대로 펀더멘털 조회 (버그 상태)
  Mode B (수정): _get_effective_fundamental_date(20170630)=20161229 사용 (lag 적용)

사용:
    python scripts/reproduce_005620_lag_bug.py
    python scripts/reproduce_005620_lag_bug.py --ticker 005620 --date 20170630
"""

import argparse
import logging
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import setup_logging
from config.settings import settings
from factors.quality import QualityFactor
from strategy.screener import MultiFactorScreener

logger = logging.getLogger(__name__)


def load_fundamentals(db_path: str, date_iso: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT ticker, per, pbr, eps, bps, div FROM fundamental "
            "WHERE date = ?",
            conn,
            params=[date_iso],
        )
    df.columns = ["ticker", "PER", "PBR", "EPS", "BPS", "DIV"]
    return df.set_index("ticker")


def yyyymmdd_to_iso(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def mode_summary(
    label: str,
    universe_df: pd.DataFrame,
    ticker: str,
    min_fscore: int,
) -> dict:
    """지정 모드의 펀더멘털로 F-Score 계산 + 필터 결과."""
    result = {
        "label": label,
        "universe_size": len(universe_df),
        "has_ticker": ticker in universe_df.index,
    }
    if not result["has_ticker"]:
        result["fscore"] = None
        result["filter_pass"] = False
        return result

    row = universe_df.loc[ticker]
    result["per"] = row["PER"]
    result["pbr"] = row["PBR"]
    result["eps"] = row["EPS"]
    result["bps"] = row["BPS"]
    result["div"] = row["DIV"]
    result["roe"] = (
        row["EPS"] / row["BPS"]
        if pd.notna(row["EPS"]) and pd.notna(row["BPS"]) and row["BPS"] > 0
        else None
    )

    fscore_all = QualityFactor.calc_fscore(universe_df)
    if ticker in fscore_all.index:
        fscore = int(fscore_all.loc[ticker])
    else:
        fscore = 0
    result["fscore"] = fscore
    result["filter_pass"] = fscore >= min_fscore
    return result


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="005620 Lag 버그 재현")
    parser.add_argument("--ticker", default="005620")
    parser.add_argument("--date", default="20170630", help="리밸런싱 YYYYMMDD")
    args = parser.parse_args()

    rebalance = args.date
    effective = MultiFactorScreener._get_effective_fundamental_date(rebalance)

    rebalance_iso = yyyymmdd_to_iso(rebalance)
    effective_iso = yyyymmdd_to_iso(effective)

    min_fscore = settings.quality.min_fscore
    db_path = settings.db_path

    print("=" * 70)
    print(f"005620 Reporting Lag 재현 분석")
    print("=" * 70)
    print(f"리밸런싱 날짜: {rebalance} ({rebalance_iso})")
    print(f"효과적 날짜 (lag 적용): {effective} ({effective_iso})")
    print(f"min_fscore 기준: {min_fscore}점")
    print()

    univ_a = load_fundamentals(db_path, rebalance_iso)
    univ_b = load_fundamentals(db_path, effective_iso)

    mode_a = mode_summary("A: 현재 코드 (lag 미적용)", univ_a, args.ticker, min_fscore)
    mode_b = mode_summary("B: lag 강제 적용", univ_b, args.ticker, min_fscore)

    for mode in (mode_a, mode_b):
        print("-" * 70)
        print(f"[{mode['label']}]")
        print(f"  유니버스 크기: {mode['universe_size']}")
        if not mode["has_ticker"]:
            print(f"  {args.ticker}: 해당 날짜 펀더멘털 없음 → 선정 불가")
            print(f"  → 필터 통과: ✗ (데이터 없음으로 자동 배제)")
            continue
        per_s = f"{mode['per']:.4f}" if pd.notna(mode["per"]) else "None"
        eps_s = f"{mode['eps']:,.0f}" if pd.notna(mode["eps"]) else "None"
        bps_s = f"{mode['bps']:,.0f}" if pd.notna(mode["bps"]) else "None"
        roe_s = f"{mode['roe']:.4f}" if mode["roe"] is not None else "None"
        print(f"  PER: {per_s}")
        print(f"  PBR: {mode['pbr']:.4f}")
        print(f"  EPS: {eps_s}")
        print(f"  BPS: {bps_s}")
        print(f"  ROE: {roe_s}")
        print(f"  F-Score: {mode['fscore']}/5")
        mark = "✅ 통과" if mode["filter_pass"] else "❌ 배제"
        print(f"  → 필터 결과 (min={min_fscore}): {mark}")
        print()

    print("=" * 70)
    print("진단")
    print("=" * 70)
    a_pass = mode_a["filter_pass"]
    b_pass = mode_b["filter_pass"]
    if a_pass and not b_pass:
        print("  ✅ **Lag 버그 확정**")
        print("  - 현재 코드는 6/30 당일 기준으로 Q1 보고서 반영된 데이터를 사용 → 필터 통과")
        print("  - lag 적용 시 2016년 연간 보고서 데이터(적자)를 사용 → 필터 배제")
        print("  - 수정 방향: screener.screen()에서 "
              "_get_effective_fundamental_date() 호출하여 연간 데이터 기반 F-Score 계산")
    elif not a_pass and not b_pass:
        print("  ⚠️ 두 모드 모두 필터 배제 — lag과 무관한 별도 이슈 가능성")
    elif a_pass and b_pass:
        print("  ⚠️ 두 모드 모두 통과 — lag만으로 회피 불가")
        print("  - 연간 보고서 데이터로도 F-Score 4점 이상 → 추가 방어 필요")
        print("  - F-Score 시계열 일관성 / EPS 급변 감지 / 거래정지 감지 등 검토")
    else:
        print("  ℹ️ 현재 코드는 배제, lag 코드는 통과 — 비상식적 결과, 재확인 필요")
    return 0


if __name__ == "__main__":
    sys.exit(main())
