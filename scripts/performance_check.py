# scripts/performance_check.py
"""성능 진단 스크립트 — DB / 객체 생성 / 메모리 / 코드 효율성 / 스케줄러 5개 영역 측정.

실행:
  python scripts/performance_check.py                  # 콘솔 출력 + 리포트 저장
  python scripts/performance_check.py --no-save        # 콘솔 출력만
  python scripts/performance_check.py --label after    # Before/After 측정 시 라벨

결과 저장 경로: docs/reports/performance_check.md
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# 프로젝트 루트 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logger = logging.getLogger(__name__)

# 출력 인코딩 (Windows cp949 회피)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────
# 결과 컨테이너
# ─────────────────────────────────────────────


@dataclass
class Finding:
    item: str
    current: str
    severity: str  # "RED", "YELLOW", "GREEN"
    suggestion: str


@dataclass
class CheckReport:
    label: str = "before"
    db_findings: list[Finding] = field(default_factory=list)
    object_findings: list[Finding] = field(default_factory=list)
    memory_findings: list[Finding] = field(default_factory=list)
    code_findings: list[Finding] = field(default_factory=list)
    scheduler_findings: list[Finding] = field(default_factory=list)
    raw_metrics: dict = field(default_factory=dict)


SEVERITY_ICON = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}


# ─────────────────────────────────────────────
# Part 1: DB 진단
# ─────────────────────────────────────────────


def check_db_performance(report: CheckReport, db_path: Path) -> None:
    """DB 성능 진단 — 테이블 크기 / 인덱스 / 쿼리 실행 시간."""
    if not db_path.exists():
        report.db_findings.append(Finding(
            item="DB 파일", current=f"{db_path} 없음", severity="RED",
            suggestion="먼저 데이터 수집 후 실행",
        ))
        return

    db_size_mb = db_path.stat().st_size / (1024 * 1024)
    report.raw_metrics["db_size_mb"] = round(db_size_mb, 1)
    report.db_findings.append(Finding(
        item="DB 디스크 크기", current=f"{db_size_mb:,.1f} MB",
        severity="GREEN" if db_size_mb < 5000 else "YELLOW",
        suggestion="-",
    ))

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1. 테이블 행수
    tables = [
        "daily_price", "fundamental", "market_cap", "factor_score",
        "portfolio", "trade", "delisted_stock",
    ]
    table_rows: dict[str, int] = {}
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            n = cur.fetchone()[0]
            table_rows[t] = n
        except sqlite3.Error:
            table_rows[t] = -1
    report.raw_metrics["table_rows"] = table_rows

    summary = ", ".join(f"{k}={v:,}" for k, v in table_rows.items() if v >= 0)
    report.db_findings.append(Finding(
        item="테이블 행수", current=summary, severity="GREEN", suggestion="-",
    ))

    # 2. 인덱스 목록
    cur.execute(
        "SELECT name, tbl_name FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY tbl_name, name"
    )
    indexes = cur.fetchall()
    report.raw_metrics["indexes"] = [(n, t) for n, t in indexes]
    by_table: dict[str, list[str]] = {}
    for name, tbl in indexes:
        by_table.setdefault(tbl, []).append(name)

    missing_compound = []
    # 복합 인덱스 권장 대상
    compound_targets = {
        "fundamental": ("date", "market"),
        "market_cap": ("date", "market"),
    }
    for tbl, cols in compound_targets.items():
        idx_names = by_table.get(tbl, [])
        # 정확한 컬럼 조합을 가진 인덱스가 있는지 확인 (PRAGMA index_info 검증)
        has_compound = False
        for idx in idx_names:
            try:
                cur.execute(f"PRAGMA index_info({idx})")
                idx_cols = tuple(r[2] for r in cur.fetchall())
                if idx_cols == cols:
                    has_compound = True
                    break
            except sqlite3.Error:
                pass
        if not has_compound:
            missing_compound.append(f"{tbl}({', '.join(cols)})")

    severity = "RED" if missing_compound else "GREEN"
    report.db_findings.append(Finding(
        item="복합 인덱스 누락",
        current=", ".join(missing_compound) if missing_compound else "없음",
        severity=severity,
        suggestion="data/storage.py에 _migrate_compound_indexes 추가" if missing_compound else "-",
    ))
    total_idx = sum(len(v) for v in by_table.values())
    report.db_findings.append(Finding(
        item="총 인덱스 수", current=f"{total_idx}개 (테이블 {len(by_table)}개)",
        severity="GREEN", suggestion="-",
    ))

    # 3. 쿼리 실행 시간 측정 (대표 쿼리 4종)
    # 최근 데이터 날짜 자동 탐지
    try:
        cur.execute("SELECT MAX(date) FROM fundamental")
        ref_date = cur.fetchone()[0]
    except sqlite3.Error:
        ref_date = None

    if ref_date is None:
        report.db_findings.append(Finding(
            item="쿼리 측정", current="ref_date 탐지 실패 (fundamental 비어있음)",
            severity="YELLOW", suggestion="데이터 수집 후 재실행",
        ))
    else:
        # 시가총액 기준 대표 종목 (예: 삼성전자 005930)
        sample_ticker = "005930"
        # daily_price 시작일 — ref_date 1년 전
        try:
            ref_dt = datetime.strptime(ref_date, "%Y-%m-%d").date() if "-" in str(ref_date) else datetime.strptime(str(ref_date), "%Y%m%d").date()
        except ValueError:
            ref_dt = date.today()
        start_dt = (ref_dt - timedelta(days=365)).isoformat()

        queries = {
            "fundamental WHERE date+market": (
                "SELECT * FROM fundamental WHERE date = ? AND market = ?",
                (str(ref_dt), "KOSPI"),
            ),
            "market_cap WHERE date+market": (
                "SELECT * FROM market_cap WHERE date = ? AND market = ?",
                (str(ref_dt), "KOSPI"),
            ),
            "daily_price WHERE ticker+date>=": (
                "SELECT * FROM daily_price WHERE ticker = ? AND date >= ?",
                (sample_ticker, start_dt),
            ),
            "daily_price WHERE date (전종목)": (
                "SELECT * FROM daily_price WHERE date = ?",
                (str(ref_dt),),
            ),
        }

        query_times: dict[str, dict] = {}
        for name, (sql, params) in queries.items():
            # 평균을 위해 3회 측정
            elapsed_list = []
            row_count = 0
            for _ in range(3):
                t0 = time.perf_counter()
                rows = cur.execute(sql, params).fetchall()
                elapsed_list.append((time.perf_counter() - t0) * 1000)
                row_count = len(rows)
            avg_ms = sum(elapsed_list) / len(elapsed_list)

            # EXPLAIN QUERY PLAN
            plan_rows = cur.execute(
                f"EXPLAIN QUERY PLAN {sql}", params
            ).fetchall()
            plan_text = " | ".join(str(r[3]) for r in plan_rows)
            uses_scan = "SCAN " in plan_text and "USING INDEX" not in plan_text
            uses_index = "USING INDEX" in plan_text or "USING COVERING INDEX" in plan_text

            query_times[name] = {
                "avg_ms": round(avg_ms, 2),
                "row_count": row_count,
                "plan": plan_text,
                "full_scan": uses_scan,
                "uses_index": uses_index,
            }

            severity = "GREEN"
            if avg_ms > 500:
                severity = "RED"
            elif avg_ms > 100:
                severity = "YELLOW"

            sug = []
            if uses_scan and not uses_index:
                sug.append("Full table scan — 인덱스 추가 필요")
            if not sug:
                sug.append("-")

            report.db_findings.append(Finding(
                item=f"쿼리 [{name}]",
                current=f"{avg_ms:.1f}ms (행 {row_count:,}, plan: {plan_text[:80]})",
                severity=severity,
                suggestion="; ".join(sug),
            ))

        report.raw_metrics["query_times"] = query_times
        report.raw_metrics["ref_date"] = str(ref_dt)

    conn.close()


# ─────────────────────────────────────────────
# Part 2: 객체 생성 패턴 분석
# ─────────────────────────────────────────────


def check_object_creation(report: CheckReport) -> None:
    """객체 생성 패턴 분석 — scheduler/main.py와 코드 전반의 반복 생성."""
    scheduler_path = ROOT / "scheduler" / "main.py"
    order_path = ROOT / "trading" / "order.py"

    counts = {
        "DataStorage()": 0,
        "KiwoomRestClient()": 0,
        "KRXDataCollector()": 0,
    }

    for path in [scheduler_path, order_path]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for cls in counts:
            # 정확한 호출 카운트 (DataStorage()만, db_path= 인자 있으면 별도)
            base = cls.replace("()", "")
            counts[f"{base}()"] = counts.get(f"{base}()", 0)
            # 함수 본문 내 호출만 (import 제외)
            pattern = rf"\b{base}\("
            matches = re.findall(pattern, text)
            counts[f"{base}()"] += len(matches)

    report.raw_metrics["object_creation_counts"] = counts

    # 런타임 생성 시간 측정 (각 클래스 단일 인스턴스)
    init_times: dict[str, float] = {}
    try:
        # DataStorage
        from data.storage import DataStorage  # noqa: WPS433
        # WAL/PRAGMA 캐시 워밍 후 측정
        DataStorage()
        t0 = time.perf_counter()
        DataStorage()
        init_times["DataStorage"] = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as e:
        init_times["DataStorage"] = -1
        logger.debug(f"DataStorage 초기화 측정 실패: {e}")

    try:
        from trading.kiwoom_api import KiwoomRestClient  # noqa: WPS433
        t0 = time.perf_counter()
        KiwoomRestClient()
        init_times["KiwoomRestClient"] = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as e:
        init_times["KiwoomRestClient"] = -1
        logger.debug(f"KiwoomRestClient 초기화 측정 실패: {e}")

    try:
        from data.collector import KRXDataCollector  # noqa: WPS433
        t0 = time.perf_counter()
        KRXDataCollector()
        init_times["KRXDataCollector"] = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as e:
        init_times["KRXDataCollector"] = -1
        logger.debug(f"KRXDataCollector 초기화 측정 실패: {e}")

    report.raw_metrics["init_times_ms"] = init_times

    # 점검 결과
    sched_storage = counts.get("DataStorage()", 0)
    sched_api = counts.get("KiwoomRestClient()", 0)
    sched_collector = counts.get("KRXDataCollector()", 0)

    is_singleton = sched_storage <= 2  # get_storage 함수 1개 + 1번 fallback
    severity = "GREEN" if is_singleton else "RED"
    report.object_findings.append(Finding(
        item="DataStorage 생성 호출 수 (scheduler+order)",
        current=f"{sched_storage}회",
        severity=severity,
        suggestion="모듈 레벨 싱글턴화 권장 (get_storage 함수)" if not is_singleton else "-",
    ))

    is_singleton = sched_api <= 2
    severity = "GREEN" if is_singleton else "RED"
    report.object_findings.append(Finding(
        item="KiwoomRestClient 생성 호출 수",
        current=f"{sched_api}회",
        severity=severity,
        suggestion="모듈 레벨 싱글턴화 권장 (get_api 함수)" if not is_singleton else "-",
    ))

    is_singleton = sched_collector <= 2
    severity = "GREEN" if is_singleton else "RED"
    report.object_findings.append(Finding(
        item="KRXDataCollector 생성 호출 수",
        current=f"{sched_collector}회",
        severity=severity,
        suggestion="모듈 레벨 싱글턴화 권장 (get_collector 함수)" if not is_singleton else "-",
    ))

    init_summary = ", ".join(
        f"{k}={v:.1f}ms" for k, v in init_times.items() if v >= 0
    )
    total_init = sum(v for v in init_times.values() if v > 0)
    severity = "GREEN" if total_init < 100 else "YELLOW"
    report.object_findings.append(Finding(
        item="단일 인스턴스 생성 시간",
        current=init_summary,
        severity=severity,
        suggestion=(
            f"리밸런싱 1회당 누적 약 {total_init * sched_storage / max(sched_storage, 1):.0f}ms 절감 가능"
            if not is_singleton else "-"
        ),
    ))

    # 각 __init__ 부수효과 식별
    side_effects = {
        "DataStorage": "create_engine + 7개 마이그레이션 + Base.metadata.create_all + WAL pragma",
        "KiwoomRestClient": "토큰 미리 발급 안 함 (lazy) — 가벼움",
        "KRXDataCollector": "DataStorage() 생성 (체인) + 캐시 dict 초기화",
    }
    for cls, effect in side_effects.items():
        report.object_findings.append(Finding(
            item=f"{cls}.__init__ 부수효과",
            current=effect,
            severity="YELLOW" if "마이그레이션" in effect else "GREEN",
            suggestion="-",
        ))


# ─────────────────────────────────────────────
# Part 3: 메모리 사용 패턴 분석
# ─────────────────────────────────────────────


def check_memory_patterns(report: CheckReport, db_path: Path) -> None:
    """메모리 사용 패턴 분석 — 캐시 구조, 대규모 DataFrame 로딩."""
    # 1. screener._factor_cache 구조
    screener_text = (ROOT / "strategy" / "screener.py").read_text(encoding="utf-8")
    cache_max = re.search(r"_CACHE_MAX_SIZE.*?=\s*(\d+)", screener_text)
    max_size = int(cache_max.group(1)) if cache_max else -1

    severity = "GREEN" if 0 < max_size <= 50 else ("YELLOW" if max_size > 0 else "RED")
    report.memory_findings.append(Finding(
        item="screener._factor_cache 최대 크기",
        current=f"{max_size}개" if max_size > 0 else "제한 없음",
        severity=severity,
        suggestion="현재 적정" if 0 < max_size <= 50 else "maxsize=24 권장",
    ))

    # 캐시 키에 settings 포함 여부 (오염 방지)
    has_factor_weights_in_key = "fw.value" in screener_text and "fw.momentum" in screener_text
    has_quality_in_key = "min_fscore" in screener_text and "cache_key" in screener_text
    severity = "GREEN" if has_factor_weights_in_key and has_quality_in_key else "RED"
    report.memory_findings.append(Finding(
        item="캐시 키 settings 포함 (오염 방지)",
        current=f"factor_weights={has_factor_weights_in_key}, quality 옵션={has_quality_in_key}",
        severity=severity,
        suggestion="-" if severity == "GREEN" else "프리셋 변경 시 캐시 오염 위험",
    ))

    # 2. load_daily_prices_bulk 패턴 (전종목 로딩 메모리 추정)
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            n_tickers_row = conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM daily_price"
            ).fetchone()
            n_tickers = n_tickers_row[0] if n_tickers_row else 0
            n_days_row = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM daily_price"
            ).fetchone()
            n_days = n_days_row[0] if n_days_row else 0
        except sqlite3.Error:
            n_tickers = n_days = 0
        finally:
            conn.close()

        # 전종목 × 1년치 일별 가격 메모리 (대략 한 행 60바이트)
        est_mb_full = (n_tickers * n_days * 60) / (1024 * 1024)
        est_mb_1y = (n_tickers * 252 * 60) / (1024 * 1024)
        report.memory_findings.append(Finding(
            item="DB 전종목·전기간 로드 시 추정 메모리",
            current=f"전체 {est_mb_full:,.0f}MB / 1년치 {est_mb_1y:,.0f}MB",
            severity="YELLOW" if est_mb_full > 500 else "GREEN",
            suggestion="벌크 조회 시 chunk_size + 청크 단위 처리 검토",
        ))
        report.raw_metrics["distinct_tickers"] = n_tickers
        report.raw_metrics["distinct_days"] = n_days

    # 3. 변동성 필터 — pivot_table 메모리 (전종목 × lookback)
    has_pivot = "pivot_table" in screener_text
    report.memory_findings.append(Finding(
        item="변동성 필터 pivot_table 사용",
        current="사용 중 (벡터화)" if has_pivot else "groupby 루프",
        severity="GREEN" if has_pivot else "YELLOW",
        suggestion="-",
    ))


# ─────────────────────────────────────────────
# Part 4: 코드 효율성 분석
# ─────────────────────────────────────────────


def check_code_efficiency(report: CheckReport) -> None:
    """코드 효율성 — pd.concat 루프 / N+1 / 불필요한 데이터 로드."""
    # 1. pd.concat 루프 내 반복 호출 (O(n²) 패턴 탐지)
    concat_files = ["data/collector.py", "data/storage.py", "strategy/screener.py"]
    bad_concat: list[str] = []
    for rel_path in concat_files:
        path = ROOT / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # for 루프 안에서 pd.concat([a, b]) 패턴 (결과를 자기 자신에 누적)
        # 단순 휴리스틱: "for " 다음에 가까운 곳에서 "pd.concat([" + 같은 변수 재할당
        lines = text.splitlines()
        in_for_block = False
        for_indent = 0
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("for "):
                in_for_block = True
                for_indent = len(line) - len(stripped)
                continue
            if in_for_block:
                cur_indent = len(line) - len(stripped) if stripped else for_indent + 1
                if stripped and cur_indent <= for_indent:
                    in_for_block = False
                    continue
                # for 블록 안
                m = re.search(r"(\w+)\s*=\s*pd\.concat\(\[(\w+),", line)
                if m and m.group(1) == m.group(2):
                    bad_concat.append(f"{rel_path}:{i + 1}")

    severity = "RED" if bad_concat else "GREEN"
    report.code_findings.append(Finding(
        item="pd.concat 루프 내 누적 (O(n²))",
        current=f"{len(bad_concat)}건 " + (f"({', '.join(bad_concat[:3])})" if bad_concat else ""),
        severity=severity,
        suggestion="frames 리스트로 모은 후 1회 concat" if bad_concat else "-",
    ))

    # 2. N+1 쿼리 패턴 — 개별 종목 폴백 루프
    collector_text = (ROOT / "data" / "collector.py").read_text(encoding="utf-8")
    # for ticker in ... 안에서 stock.get_market_ohlcv 또는 storage.load_daily_prices 등
    n_plus_1_patterns = []
    for line_no, line in enumerate(collector_text.splitlines(), 1):
        if re.search(r"for\s+ticker\s+in\s+", line):
            # 이후 30줄 검사
            block = "\n".join(collector_text.splitlines()[line_no:line_no + 30])
            if (
                "stock.get_market_ohlcv" in block
                or "self.storage.load_daily_prices(" in block
            ):
                n_plus_1_patterns.append(f"data/collector.py:{line_no}")

    severity = "YELLOW" if n_plus_1_patterns else "GREEN"
    report.code_findings.append(Finding(
        item="N+1 쿼리 (개별 종목 루프 폴백)",
        current=f"{len(n_plus_1_patterns)}건 (벌크 실패 시 폴백 — 의도적)",
        severity=severity,
        suggestion="벌크 우선 + 미스만 개별 폴백 — 현 패턴 OK",
    ))

    # 3. 불필요한 전체 로드 후 필터 패턴
    # SELECT * 다음 메모리에서 필터링하는 패턴 검출 — 현재 storage.py는 모두 WHERE 사용
    storage_text = (ROOT / "data" / "storage.py").read_text(encoding="utf-8")
    select_star_count = len(re.findall(r"SELECT \*\s+FROM", storage_text, re.IGNORECASE))
    where_count = len(re.findall(r"WHERE\s+", storage_text, re.IGNORECASE))
    report.code_findings.append(Finding(
        item="WHERE 절 사용 (전체 로드 회피)",
        current=f"WHERE 사용 {where_count}회 / SELECT * {select_star_count}회",
        severity="GREEN",
        suggestion="-",
    ))

    # 4. GUI 자동 갱신 주기 (장 마감 후에도 30초?)
    gui_path = ROOT / "gui" / "main_window.py"
    if gui_path.exists():
        gui_text = gui_path.read_text(encoding="utf-8")
        market_aware = (
            "market_close" in gui_text
            or "장 마감" in gui_text
            or "is_market_open" in gui_text
        )
        timer_30s = "30000" in gui_text
        severity = "YELLOW" if not market_aware and timer_30s else "GREEN"
        report.code_findings.append(Finding(
            item="GUI 자동 갱신 주기 (장 마감 후 동작)",
            current=(
                "장 마감 후에도 30초 간격" if not market_aware and timer_30s
                else ("시간대 인지 갱신" if market_aware else "타이머 미사용")
            ),
            severity=severity,
            suggestion="장외 5분 간격으로 확대" if severity == "YELLOW" else "-",
        ))


# ─────────────────────────────────────────────
# Part 5: 스케줄러 효율성 분석
# ─────────────────────────────────────────────


def check_scheduler_efficiency(report: CheckReport) -> None:
    """스케줄러 효율성 — 함수 수, time.sleep, 직렬 실행."""
    sched_path = ROOT / "scheduler" / "main.py"
    if not sched_path.exists():
        report.scheduler_findings.append(Finding(
            item="scheduler/main.py", current="없음", severity="RED", suggestion="-",
        ))
        return

    text = sched_path.read_text(encoding="utf-8")
    n_lines = len(text.splitlines())
    n_funcs = len(re.findall(r"^def\s+\w+", text, re.MULTILINE))
    n_jobs = len(re.findall(r"scheduler\.add_job\(", text))

    report.scheduler_findings.append(Finding(
        item="scheduler/main.py 규모",
        current=f"{n_lines}줄 / {n_funcs}함수 / {n_jobs} Job",
        severity="GREEN" if n_lines < 1500 else "YELLOW",
        suggestion="-",
    ))

    # time.sleep 사용 — 스레드 블로킹
    sleep_calls = re.findall(r"_time\.sleep\(|time\.sleep\(", text)
    severity = "YELLOW" if len(sleep_calls) > 0 else "GREEN"
    report.scheduler_findings.append(Finding(
        item="time.sleep() 사용 (블로킹)",
        current=f"{len(sleep_calls)}회",
        severity=severity,
        suggestion="장기 대기는 APScheduler date trigger 권장" if sleep_calls else "-",
    ))

    # Job 직렬 실행 가능성 — BlockingScheduler 단일 워커
    is_blocking = "BlockingScheduler" in text
    is_background = "BackgroundScheduler" in text
    severity = "YELLOW" if is_blocking and not is_background else "GREEN"
    report.scheduler_findings.append(Finding(
        item="스케줄러 타입",
        current="BlockingScheduler (단일 스레드)" if is_blocking else (
            "BackgroundScheduler" if is_background else "기타"
        ),
        severity=severity,
        suggestion="대부분 IO bound라서 OK; 동시성 필요 시 max_workers 조정",
    ))

    # engine.dispose() 호출 여부 — 종료 시 정리
    has_dispose = "engine.dispose" in text or "dispose()" in text
    severity = "YELLOW" if not has_dispose else "GREEN"
    report.scheduler_findings.append(Finding(
        item="DB engine.dispose() 종료 정리",
        current="호출 안 함" if not has_dispose else "호출",
        severity=severity,
        suggestion="종료 시 engine.dispose() 추가 권장" if not has_dispose else "-",
    ))


# ─────────────────────────────────────────────
# 출력 / 저장
# ─────────────────────────────────────────────


def _format_table(title: str, findings: list[Finding]) -> str:
    """결과를 마크다운 표로 포맷."""
    out = [f"\n## {title}\n"]
    out.append("| 항목 | 현재 상태 | 심각도 | 개선 방안 |")
    out.append("|------|-----------|:------:|-----------|")
    for f in findings:
        icon = SEVERITY_ICON.get(f.severity, "-")
        # 마크다운 파이프 이스케이프
        cur = f.current.replace("|", "\\|")
        sug = f.suggestion.replace("|", "\\|")
        out.append(f"| {f.item} | {cur} | {icon} | {sug} |")
    return "\n".join(out)


def render_report(report: CheckReport) -> str:
    """전체 리포트를 마크다운 문자열로 생성."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        f"# 성능 진단 리포트 ({report.label.upper()})",
        f"> 측정 시각: {now}",
        f"> DB: data/quant.db ({report.raw_metrics.get('db_size_mb', '?')} MB)",
    ]
    parts.append(_format_table("1. DB 성능 진단", report.db_findings))
    parts.append(_format_table("2. 객체 생성 패턴", report.object_findings))
    parts.append(_format_table("3. 메모리 사용 패턴", report.memory_findings))
    parts.append(_format_table("4. 코드 효율성", report.code_findings))
    parts.append(_format_table("5. 스케줄러 효율성", report.scheduler_findings))

    # 핵심 측정값 요약 (Before/After 비교에 사용)
    parts.append("\n## 핵심 측정값 (Raw Metrics)\n")
    parts.append("```")
    qt = report.raw_metrics.get("query_times", {})
    for k, v in qt.items():
        parts.append(f"  {k}: {v.get('avg_ms')}ms (rows={v.get('row_count')})")
    counts = report.raw_metrics.get("object_creation_counts", {})
    for k, v in counts.items():
        parts.append(f"  {k} 호출 수: {v}")
    init_t = report.raw_metrics.get("init_times_ms", {})
    for k, v in init_t.items():
        parts.append(f"  {k} __init__: {v}ms")
    parts.append("```\n")

    return "\n".join(parts)


def run_all_checks(label: str = "before") -> CheckReport:
    """전체 진단 실행."""
    db_path = ROOT / "data" / "quant.db"
    report = CheckReport(label=label)

    print(f"\n{'=' * 60}")
    print(f"성능 진단 시작 — 라벨: {label.upper()}")
    print(f"{'=' * 60}")

    print("\n[1/5] DB 성능 진단...")
    check_db_performance(report, db_path)

    print("[2/5] 객체 생성 패턴 분석...")
    check_object_creation(report)

    print("[3/5] 메모리 사용 패턴 분석...")
    check_memory_patterns(report, db_path)

    print("[4/5] 코드 효율성 분석...")
    check_code_efficiency(report)

    print("[5/5] 스케줄러 효율성 분석...")
    check_scheduler_efficiency(report)

    return report


def save_report(report: CheckReport, before_report: Optional[CheckReport] = None) -> Path:
    """리포트를 docs/reports/performance_check.md로 저장.

    before_report가 있으면 Before/After 비교 테이블을 추가한다.
    """
    out_dir = ROOT / "docs" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "performance_check.md"

    parts = [render_report(report)]

    if before_report is not None:
        parts.append("\n---\n")
        parts.append("\n## Before vs After 비교\n")
        parts.append(_render_diff_table(before_report, report))

    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def append_after_to_report(after_report: CheckReport, before_report: CheckReport) -> Path:
    """기존 Before 리포트에 After 결과 + Diff 추가."""
    out_dir = ROOT / "docs" / "reports"
    out_path = out_dir / "performance_check.md"

    existing = out_path.read_text(encoding="utf-8") if out_path.exists() else ""

    parts = [existing.rstrip()]
    parts.append("\n\n---\n")
    parts.append(render_report(after_report))
    parts.append("\n---\n")
    parts.append("\n## Before vs After 비교\n")
    parts.append(_render_diff_table(before_report, after_report))

    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def _render_diff_table(before: CheckReport, after: CheckReport) -> str:
    """Before/After 비교 표 생성."""
    out = [
        "| 항목 | Before | After | 개선 |",
        "|------|--------|-------|------|",
    ]

    b_qt = before.raw_metrics.get("query_times", {})
    a_qt = after.raw_metrics.get("query_times", {})
    for name in b_qt:
        b_ms = b_qt[name]["avg_ms"]
        a_ms = a_qt.get(name, {}).get("avg_ms", -1)
        if a_ms > 0 and b_ms > 0:
            ratio = b_ms / a_ms if a_ms > 0 else float("inf")
            improvement = f"{ratio:.1f}배 빠름" if ratio > 1.05 else (
                f"동등 ({ratio:.2f}x)" if 0.95 <= ratio <= 1.05 else f"{1 / ratio:.1f}배 느림"
            )
            out.append(f"| 쿼리 [{name}] | {b_ms:.1f}ms | {a_ms:.1f}ms | {improvement} |")
        else:
            out.append(f"| 쿼리 [{name}] | {b_ms:.1f}ms | (측정 실패) | - |")

    b_cnt = before.raw_metrics.get("object_creation_counts", {})
    a_cnt = after.raw_metrics.get("object_creation_counts", {})
    for name in b_cnt:
        b_n = b_cnt[name]
        a_n = a_cnt.get(name, -1)
        delta = b_n - a_n if a_n >= 0 else 0
        out.append(
            f"| {name} 호출 수 | {b_n}회 | {a_n}회 | {'-' + str(delta) + '회' if delta > 0 else '동등'} |"
        )

    b_idx = before.raw_metrics.get("indexes", [])
    a_idx = after.raw_metrics.get("indexes", [])
    out.append(
        f"| DB 인덱스 총 수 | {len(b_idx)}개 | {len(a_idx)}개 | "
        f"{'+' + str(len(a_idx) - len(b_idx)) + '개' if len(a_idx) > len(b_idx) else '동등'} |"
    )

    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="성능 진단 스크립트")
    parser.add_argument(
        "--label", default="before", choices=["before", "after"],
        help="측정 라벨 (Before/After 비교용)",
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="콘솔 출력만 (파일 저장 안 함)",
    )
    parser.add_argument(
        "--compare-with",
        type=str,
        default=None,
        help="비교용 Before 리포트 JSON 경로 (선택)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    report = run_all_checks(label=args.label)

    # 콘솔 출력
    rendered = render_report(report)
    print("\n" + rendered)

    # JSON 캐시 저장 (Before/After 비교용)
    cache_dir = ROOT / "docs" / "reports"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"performance_check_{args.label}.json"
    if not args.no_save:
        import json
        cache_data = {
            "label": report.label,
            "raw_metrics": report.raw_metrics,
            "findings": {
                "db": [f.__dict__ for f in report.db_findings],
                "object": [f.__dict__ for f in report.object_findings],
                "memory": [f.__dict__ for f in report.memory_findings],
                "code": [f.__dict__ for f in report.code_findings],
                "scheduler": [f.__dict__ for f in report.scheduler_findings],
            },
        }
        cache_path.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[저장] JSON 캐시: {cache_path}")

        # 마크다운 리포트
        if args.label == "before":
            out_path = save_report(report)
            print(f"[저장] 리포트: {out_path}")
        else:
            # After: before 캐시 로드 후 비교 추가
            before_cache = cache_dir / "performance_check_before.json"
            if before_cache.exists():
                import json
                before_data = json.loads(before_cache.read_text(encoding="utf-8"))
                # CheckReport 재구성 (raw_metrics만 비교에 필요)
                before_rep = CheckReport(label="before")
                before_rep.raw_metrics = before_data["raw_metrics"]
                out_path = append_after_to_report(report, before_rep)
                print(f"[저장] 리포트(Before/After 비교 포함): {out_path}")
            else:
                out_path = save_report(report)
                print(f"[저장] 리포트(Before 캐시 없음, 단독 저장): {out_path}")


if __name__ == "__main__":
    main()
