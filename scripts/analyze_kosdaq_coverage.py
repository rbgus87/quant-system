"""analyze_kosdaq_coverage.py — KOSDAQ 데이터 품질/커버리지 분석 리포트.

v2.0 수술 후 KOSDAQ 확장 검토용. 현 DB의 KOSDAQ 데이터를 KOSPI와 비교하여
펀더멘털 누락률, 연도별 커버리지, F-Score 계산 가능 비율, 이상치 통계를 산출.

결과는 `docs/reports/kosdaq_data_quality.md` 로 저장.

사용:
    python scripts/analyze_kosdaq_coverage.py
    python scripts/analyze_kosdaq_coverage.py --start 20140101 --end 20260416
    python scripts/analyze_kosdaq_coverage.py --output docs/reports/kosdaq_data_quality.md

이 스크립트는 **읽기 전용**입니다. DB/설정 변경 없음.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging  # noqa: E402
from data.storage import DataStorage  # noqa: E402
from sqlalchemy import text  # noqa: E402

logger = logging.getLogger(__name__)

# 이상치 기준 (v2.0 processor.py와 일관)
OUTLIER_RULES: dict[str, tuple[str, float]] = {
    "pbr_lt_005": ("pbr < 0.05", 0.05),
    "pbr_gt_20": ("pbr > 20", 20.0),
    "per_gt_1000": ("per > 1000", 1000.0),
    "per_lt_neg": ("per < 0 (적자)", 0.0),
    "pcr_gt_500": ("pcr > 500", 500.0),
    "pcr_lt_neg": ("pcr < 0 (영업현금흐름 음수)", 0.0),
    "div_gt_20": ("div > 20% (이례적)", 20.0),
}


# ───────────────────────────────────────────────
# 데이터 로딩
# ───────────────────────────────────────────────

@dataclass
class MarketSlice:
    """단일 시장(KOSPI/KOSDAQ)의 분석 스냅샷."""

    market: str
    fundamental: pd.DataFrame
    daily_price: pd.DataFrame
    market_cap: pd.DataFrame


def _parse_date_arg(s: str) -> date:
    s = s.strip().replace("-", "")
    return datetime.strptime(s, "%Y%m%d").date()


def load_market_slice(
    storage: DataStorage,
    market: str,
    start: date,
    end: date,
) -> MarketSlice:
    """단일 시장의 fundamental/daily_price/market_cap 집계용 원본 로드."""
    with storage.engine.connect() as conn:
        fund_rows = conn.execute(
            text(
                "SELECT ticker, date, pbr, per, pcr, eps, div, "
                "revenue, total_assets, operating_income, opa, data_source "
                "FROM fundamental "
                "WHERE market = :m AND date BETWEEN :s AND :e"
            ),
            {"m": market, "s": str(start), "e": str(end)},
        ).fetchall()
        dp_rows = conn.execute(
            text(
                "SELECT date, COUNT(DISTINCT ticker) AS n "
                "FROM daily_price WHERE market = :m "
                "AND date BETWEEN :s AND :e GROUP BY date"
            ),
            {"m": market, "s": str(start), "e": str(end)},
        ).fetchall()
        mc_rows = conn.execute(
            text(
                "SELECT ticker, date, market_cap "
                "FROM market_cap WHERE market = :m "
                "AND date BETWEEN :s AND :e"
            ),
            {"m": market, "s": str(start), "e": str(end)},
        ).fetchall()

    fund = pd.DataFrame(
        fund_rows,
        columns=[
            "ticker", "date", "pbr", "per", "pcr", "eps", "div",
            "revenue", "total_assets", "operating_income", "opa", "data_source",
        ],
    )
    dp = pd.DataFrame(dp_rows, columns=["date", "n_tickers"])
    mc = pd.DataFrame(mc_rows, columns=["ticker", "date", "market_cap"])

    for df in (fund, dp, mc):
        if not df.empty and "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])

    return MarketSlice(market=market, fundamental=fund, daily_price=dp, market_cap=mc)


# ───────────────────────────────────────────────
# 분석 섹션
# ───────────────────────────────────────────────

def summarize_basic(ms: MarketSlice) -> dict[str, object]:
    """기본 지표: 총 종목 수, 기간, 레코드 수."""
    f = ms.fundamental
    if f.empty:
        return {
            "market": ms.market,
            "tickers": 0,
            "rows": 0,
            "date_min": None,
            "date_max": None,
            "dp_days": 0,
            "dp_avg_tickers": 0.0,
            "mc_tickers": 0,
        }
    return {
        "market": ms.market,
        "tickers": f["ticker"].nunique(),
        "rows": len(f),
        "date_min": f["date"].min().date() if not f.empty else None,
        "date_max": f["date"].max().date() if not f.empty else None,
        "dp_days": ms.daily_price["date"].nunique() if not ms.daily_price.empty else 0,
        "dp_avg_tickers": (
            ms.daily_price["n_tickers"].mean() if not ms.daily_price.empty else 0.0
        ),
        "mc_tickers": ms.market_cap["ticker"].nunique() if not ms.market_cap.empty else 0,
    }


def summarize_null_rates(ms: MarketSlice) -> pd.DataFrame:
    """필드별 결측률."""
    f = ms.fundamental
    if f.empty:
        return pd.DataFrame(
            columns=["field", "total", "null", "null_pct", "valid", "valid_pct"]
        )
    out = []
    for col in ["pbr", "per", "pcr", "eps", "div", "revenue",
                "total_assets", "operating_income", "opa"]:
        if col not in f.columns:
            continue
        total = len(f)
        null = f[col].isna().sum()
        valid = total - null
        out.append({
            "field": col,
            "total": total,
            "null": int(null),
            "null_pct": null / total * 100 if total else 0.0,
            "valid": int(valid),
            "valid_pct": valid / total * 100 if total else 0.0,
        })
    return pd.DataFrame(out)


def summarize_yearly(ms: MarketSlice) -> pd.DataFrame:
    """연도별 종목 수, PBR 유효 비율, PCR 유효 비율."""
    f = ms.fundamental
    if f.empty:
        return pd.DataFrame(columns=[
            "year", "rows", "unique_tickers", "pbr_valid_pct",
            "pcr_valid_pct", "per_valid_pct", "div_valid_pct",
        ])
    f = f.copy()
    f["year"] = f["date"].dt.year
    rows = []
    for yr, grp in f.groupby("year"):
        n = len(grp)
        rows.append({
            "year": int(yr),
            "rows": n,
            "unique_tickers": grp["ticker"].nunique(),
            "pbr_valid_pct": grp["pbr"].notna().mean() * 100 if n else 0.0,
            "pcr_valid_pct": grp["pcr"].notna().mean() * 100 if n else 0.0,
            "per_valid_pct": grp["per"].notna().mean() * 100 if n else 0.0,
            "div_valid_pct": grp["div"].notna().mean() * 100 if n else 0.0,
        })
    return pd.DataFrame(rows).sort_values("year")


def summarize_fscore_eligibility(ms: MarketSlice) -> dict[str, float]:
    """F-Score 계산에 필요한 필드(per, pbr, div) 동시 보유 비율.

    현 구현(quality.py calc_fscore)은 PER/PBR/DIV 3개 필드로 5점 만점 계산.
    3개 모두 있어야 의미 있는 점수를 산출 (min_fscore=4 필터 통과 가능).
    """
    f = ms.fundamental
    if f.empty:
        return {
            "eligible_pct": 0.0,
            "with_per_pbr_div": 0,
            "with_per_pbr": 0,
            "total": 0,
        }
    has_all3 = f[["per", "pbr", "div"]].notna().all(axis=1)
    has_per_pbr = f[["per", "pbr"]].notna().all(axis=1)
    return {
        "total": int(len(f)),
        "with_per_pbr_div": int(has_all3.sum()),
        "with_per_pbr": int(has_per_pbr.sum()),
        "eligible_pct": has_all3.mean() * 100,
    }


def summarize_outliers(ms: MarketSlice) -> pd.DataFrame:
    """이상치 건수 표."""
    f = ms.fundamental
    if f.empty:
        return pd.DataFrame(columns=["rule", "count", "pct"])
    rows = []
    total = len(f)
    checks = [
        ("pbr < 0.05", (f["pbr"] < 0.05) & f["pbr"].notna()),
        ("pbr > 20", (f["pbr"] > 20) & f["pbr"].notna()),
        ("per > 1000", (f["per"] > 1000) & f["per"].notna()),
        ("per < 0 (적자)", (f["per"] < 0) & f["per"].notna()),
        ("pcr > 500", (f["pcr"] > 500) & f["pcr"].notna()),
        ("pcr < 0 (영업CF 음수)", (f["pcr"] < 0) & f["pcr"].notna()),
        ("div > 20 (이례적)", (f["div"] > 20) & f["div"].notna()),
    ]
    for rule, mask in checks:
        c = int(mask.sum())
        rows.append({
            "rule": rule,
            "count": c,
            "pct": c / total * 100 if total else 0.0,
        })
    return pd.DataFrame(rows)


def summarize_data_source(ms: MarketSlice) -> pd.DataFrame:
    """데이터 출처(KRX/DART) 분포."""
    f = ms.fundamental
    if f.empty or "data_source" not in f.columns:
        return pd.DataFrame(columns=["source", "rows", "pct"])
    vc = f["data_source"].fillna("UNKNOWN").value_counts()
    total = vc.sum()
    return pd.DataFrame({
        "source": vc.index,
        "rows": vc.values,
        "pct": (vc.values / total * 100) if total else 0.0,
    })


# ───────────────────────────────────────────────
# Markdown 리포트 작성
# ───────────────────────────────────────────────

def df_to_md(df: pd.DataFrame, float_fmt: str = "{:.2f}") -> str:
    """pandas DataFrame → GitHub Flavored Markdown 테이블 (의존성 없이)."""
    if df.empty:
        return "_(데이터 없음)_"
    cols = list(df.columns)
    head = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [head, sep]
    for _, row in df.iterrows():
        vals = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                if pd.isna(v):
                    vals.append("")
                else:
                    vals.append(float_fmt.format(v))
            elif v is None:
                vals.append("")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build_report(
    kospi: MarketSlice,
    kosdaq: MarketSlice,
    start: date,
    end: date,
) -> str:
    """Markdown 리포트 본문 생성."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines.append(f"# KOSDAQ 데이터 품질 보고서")
    lines.append("")
    lines.append(f"> 생성 시각: {now}  ")
    lines.append(f"> 분석 범위: {start} ~ {end}  ")
    lines.append(f"> 대상: `fundamental`, `daily_price`, `market_cap` 테이블  ")
    lines.append(f"> 스크립트: `scripts/analyze_kosdaq_coverage.py` (읽기 전용)")
    lines.append("")

    # ── 1. 총괄 요약
    lines.append("## 1. 총괄 요약")
    lines.append("")
    kospi_b = summarize_basic(kospi)
    kosdaq_b = summarize_basic(kosdaq)
    sum_df = pd.DataFrame([
        {
            "시장": "KOSPI",
            "고유 종목수(fund)": kospi_b["tickers"],
            "fund 레코드수": kospi_b["rows"],
            "fund 기간": f"{kospi_b['date_min']} ~ {kospi_b['date_max']}",
            "daily_price 일수": kospi_b["dp_days"],
            "dp 일평균 종목수": round(kospi_b["dp_avg_tickers"], 0),
            "market_cap 고유 종목수": kospi_b["mc_tickers"],
        },
        {
            "시장": "KOSDAQ",
            "고유 종목수(fund)": kosdaq_b["tickers"],
            "fund 레코드수": kosdaq_b["rows"],
            "fund 기간": f"{kosdaq_b['date_min']} ~ {kosdaq_b['date_max']}",
            "daily_price 일수": kosdaq_b["dp_days"],
            "dp 일평균 종목수": round(kosdaq_b["dp_avg_tickers"], 0),
            "market_cap 고유 종목수": kosdaq_b["mc_tickers"],
        },
    ])
    lines.append(df_to_md(sum_df, float_fmt="{:,.0f}"))
    lines.append("")

    # ── 2. 펀더멘털 결측률
    lines.append("## 2. 펀더멘털 누락률 (시장별)")
    lines.append("")
    for name, ms in [("KOSPI", kospi), ("KOSDAQ", kosdaq)]:
        lines.append(f"### 2.{1 if name == 'KOSPI' else 2}. {name}")
        lines.append("")
        nr = summarize_null_rates(ms)
        if not nr.empty:
            nr_fmt = nr.copy()
            nr_fmt["null_pct"] = nr_fmt["null_pct"].round(2)
            nr_fmt["valid_pct"] = nr_fmt["valid_pct"].round(2)
            lines.append(df_to_md(nr_fmt, float_fmt="{:.2f}"))
        else:
            lines.append("_(데이터 없음)_")
        lines.append("")

    # ── 3. 연도별 커버리지
    lines.append("## 3. 연도별 커버리지 추이")
    lines.append("")
    for name, ms in [("KOSPI", kospi), ("KOSDAQ", kosdaq)]:
        lines.append(f"### 3.{1 if name == 'KOSPI' else 2}. {name}")
        lines.append("")
        yr = summarize_yearly(ms)
        if not yr.empty:
            for c in ["pbr_valid_pct", "pcr_valid_pct", "per_valid_pct", "div_valid_pct"]:
                yr[c] = yr[c].round(1)
            lines.append(df_to_md(yr, float_fmt="{:.1f}"))
        else:
            lines.append("_(데이터 없음)_")
        lines.append("")

    # ── 4. KOSPI vs KOSDAQ 품질 비교 표
    lines.append("## 4. KOSPI vs KOSDAQ 데이터 품질 비교")
    lines.append("")
    kospi_null = summarize_null_rates(kospi).set_index("field") \
        if not summarize_null_rates(kospi).empty else pd.DataFrame()
    kosdaq_null = summarize_null_rates(kosdaq).set_index("field") \
        if not summarize_null_rates(kosdaq).empty else pd.DataFrame()
    fields = sorted(set(kospi_null.index) | set(kosdaq_null.index))
    comp_rows = []
    for fld in fields:
        kp = kospi_null.loc[fld, "valid_pct"] if fld in kospi_null.index else float("nan")
        kd = kosdaq_null.loc[fld, "valid_pct"] if fld in kosdaq_null.index else float("nan")
        delta = kd - kp if (pd.notna(kp) and pd.notna(kd)) else float("nan")
        comp_rows.append({
            "필드": fld,
            "KOSPI 유효%": round(kp, 2) if pd.notna(kp) else "",
            "KOSDAQ 유효%": round(kd, 2) if pd.notna(kd) else "",
            "차이(KOSDAQ-KOSPI)": round(delta, 2) if pd.notna(delta) else "",
        })
    lines.append(df_to_md(pd.DataFrame(comp_rows)))
    lines.append("")

    # ── 5. F-Score 계산 가능 비율
    lines.append("## 5. F-Score 계산 가능 종목 비율")
    lines.append("")
    lines.append(
        "현 구현(`factors/quality.py: calc_fscore`)은 PER/PBR/DIV 3필드로 5점 계산. "
        "3개 필드가 모두 있어야 `min_fscore=4` 필터 통과 여부 판정이 유효함."
    )
    lines.append("")
    kospi_fs = summarize_fscore_eligibility(kospi)
    kosdaq_fs = summarize_fscore_eligibility(kosdaq)
    fs_df = pd.DataFrame([
        {
            "시장": "KOSPI",
            "총 레코드": kospi_fs["total"],
            "PER+PBR 보유": kospi_fs["with_per_pbr"],
            "PER+PBR+DIV 보유": kospi_fs["with_per_pbr_div"],
            "F-Score 가능%": round(kospi_fs["eligible_pct"], 2),
        },
        {
            "시장": "KOSDAQ",
            "총 레코드": kosdaq_fs["total"],
            "PER+PBR 보유": kosdaq_fs["with_per_pbr"],
            "PER+PBR+DIV 보유": kosdaq_fs["with_per_pbr_div"],
            "F-Score 가능%": round(kosdaq_fs["eligible_pct"], 2),
        },
    ])
    lines.append(df_to_md(fs_df, float_fmt="{:,.0f}"))
    lines.append("")

    # ── 6. 이상치 통계
    lines.append("## 6. 이상치 통계")
    lines.append("")
    for name, ms in [("KOSPI", kospi), ("KOSDAQ", kosdaq)]:
        lines.append(f"### 6.{1 if name == 'KOSPI' else 2}. {name}")
        lines.append("")
        out = summarize_outliers(ms)
        if not out.empty:
            out["pct"] = out["pct"].round(3)
            lines.append(df_to_md(out, float_fmt="{:.3f}"))
        else:
            lines.append("_(데이터 없음)_")
        lines.append("")

    # ── 7. 데이터 출처 분포
    lines.append("## 7. 데이터 출처 분포 (KRX vs DART)")
    lines.append("")
    for name, ms in [("KOSPI", kospi), ("KOSDAQ", kosdaq)]:
        lines.append(f"### 7.{1 if name == 'KOSPI' else 2}. {name}")
        lines.append("")
        ds = summarize_data_source(ms)
        if not ds.empty:
            ds["pct"] = ds["pct"].round(2)
            lines.append(df_to_md(ds, float_fmt="{:,.0f}"))
        else:
            lines.append("_(데이터 없음)_")
        lines.append("")

    # ── 8. 주요 관찰
    lines.append("## 8. 주요 관찰")
    lines.append("")
    notes: list[str] = []
    # KOSDAQ fund 존재 여부
    if kosdaq_b["rows"] == 0:
        notes.append(
            "- **KOSDAQ fundamental 데이터가 DB에 전무**. backfill 선행 필수."
        )
    else:
        # 기간 격차
        if kosdaq_b["date_min"] and kospi_b["date_min"]:
            if kosdaq_b["date_min"] > kospi_b["date_min"]:
                notes.append(
                    f"- KOSDAQ fund 데이터 시작({kosdaq_b['date_min']})이 "
                    f"KOSPI({kospi_b['date_min']})보다 늦음 → 초기 기간 갭 존재."
                )
        # 유효율 갭
        if not kospi_null.empty and not kosdaq_null.empty:
            for fld in ["pbr", "pcr", "per", "div"]:
                if fld in kospi_null.index and fld in kosdaq_null.index:
                    kp = kospi_null.loc[fld, "valid_pct"]
                    kd = kosdaq_null.loc[fld, "valid_pct"]
                    if kp - kd > 10:
                        notes.append(
                            f"- `{fld}` 유효률: KOSPI {kp:.1f}% vs KOSDAQ {kd:.1f}% "
                            f"→ 격차 {kp - kd:.1f}%p (KOSDAQ 품질 저조)."
                        )
        # F-Score 가능 비율
        if kosdaq_fs["eligible_pct"] < 60:
            notes.append(
                f"- KOSDAQ F-Score 가능 레코드 {kosdaq_fs['eligible_pct']:.1f}% — "
                f"필터 적용 시 유니버스 급감 우려."
            )
    # daily_price 일평균 종목수
    if kosdaq_b["dp_days"] > 0 and kosdaq_b["dp_avg_tickers"] > 0:
        notes.append(
            f"- KOSDAQ daily_price 일평균 {kosdaq_b['dp_avg_tickers']:.0f} 종목 "
            f"(총 {kosdaq_b['dp_days']}영업일 커버)."
        )
    if not notes:
        notes.append("- (자동 관찰 규칙에 해당하는 이상 없음)")
    lines.extend(notes)
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 본 보고서는 자동 생성됩니다. 수치는 DB 현재 스냅샷 기준이며 "
        "backfill 진행 중이면 실시간으로 변합니다."
    )
    lines.append("")
    return "\n".join(lines)


# ───────────────────────────────────────────────
# main
# ───────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="KOSDAQ 데이터 커버리지 분석")
    parser.add_argument("--start", type=str, default="20131217",
                        help="분석 시작일 (YYYYMMDD, 기본: 20131217)")
    parser.add_argument("--end", type=str, default=None,
                        help="분석 종료일 (YYYYMMDD, 기본: 오늘)")
    parser.add_argument(
        "--output", type=str,
        default=str(PROJECT_ROOT / "docs" / "reports" / "kosdaq_data_quality.md"),
        help="출력 Markdown 경로",
    )
    args = parser.parse_args()

    setup_logging()

    start = _parse_date_arg(args.start)
    end = _parse_date_arg(args.end) if args.end else date.today()
    if start > end:
        parser.error(f"--start({start})가 --end({end})보다 이후입니다")

    logger.info("분석 기간: %s ~ %s", start, end)
    storage = DataStorage()

    logger.info("KOSPI 슬라이스 로딩 중...")
    kospi = load_market_slice(storage, "KOSPI", start, end)
    logger.info(
        "  KOSPI fund=%d rows, dp=%d days, mc=%d rows",
        len(kospi.fundamental), len(kospi.daily_price), len(kospi.market_cap),
    )

    logger.info("KOSDAQ 슬라이스 로딩 중...")
    kosdaq = load_market_slice(storage, "KOSDAQ", start, end)
    logger.info(
        "  KOSDAQ fund=%d rows, dp=%d days, mc=%d rows",
        len(kosdaq.fundamental), len(kosdaq.daily_price), len(kosdaq.market_cap),
    )

    logger.info("리포트 생성 중...")
    md = build_report(kospi, kosdaq, start, end)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    logger.info("리포트 저장 완료: %s (%d bytes)", out_path, len(md.encode("utf-8")))

    print(f"\n리포트 저장: {out_path}")
    print(f"  KOSPI  fund rows: {len(kospi.fundamental):>10,}")
    print(f"  KOSDAQ fund rows: {len(kosdaq.fundamental):>10,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
