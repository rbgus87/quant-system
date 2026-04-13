"""selftest.py — 분기 리밸런싱 전야 배포 무결성 자가 검증.

quant-system 은 매일 돌리는 시스템이 아니라 분기 리밸런싱 시점에만
실전 호출이 발생한다. 이 스크립트는 리밸런싱 D-1 에 수동 실행해
"exe 빌드 + 외부 API + DB + 팩터 파이프라인" 이 지금 이 순간
문제없이 돌 수 있는지를 5~10분 안에 확인한다.

4단계 구성:
  1. 정적 분석  — ruff / hidden-import / .env 필수 키
  2. 단위 테스트 — pytest tests/
  3. 통합 스모크 — DART · Kiwoom · Telegram · SQLite · 스크리너 1회
  4. exe 번들  — build/KoreanQuant 의 PYZ TOC 에서 필수 모듈 확인

사용:
    python scripts/selftest.py                  # 전체 실행
    python scripts/selftest.py --notify         # 결과를 텔레그램으로도 전송
    python scripts/selftest.py --skip-tests     # 2단계 건너뜀 (단위테스트 오래 걸릴 때)
    python scripts/selftest.py --skip-exe-check # 4단계 건너뜀 (빌드 전)

exit code:
    0 = 전 단계 통과
    1 = FAIL 가 하나라도 있음
"""

from __future__ import annotations

import argparse
import ast
import logging
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 텔레그램/네트워크 서브 단계에서 로거 소음 억제
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ──────────────────────────────────────────────────────────────────
# 터미널 출력
# ──────────────────────────────────────────────────────────────────
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_GRAY = "\x1b[90m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


class Status:
    OK = "OK"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


_STATUS_COLOR = {
    Status.OK: _GREEN,
    Status.FAIL: _RED,
    Status.WARN: _YELLOW,
    Status.SKIP: _GRAY,
}


def _print_step(phase: int, idx: int, name: str, status: str, detail: str = "") -> None:
    color = _STATUS_COLOR.get(status, "")
    tag = f"[{status}]"
    line = f"  {color}{tag:<7}{_RESET} {phase}.{idx} {name:<32}"
    if detail:
        line += f" {_GRAY}{detail}{_RESET}"
    print(line)


def _print_phase(phase: int, title: str) -> None:
    print(f"\n{_BOLD}▌Phase {phase}. {title}{_RESET}")


# ──────────────────────────────────────────────────────────────────
# 결과 누적
# ──────────────────────────────────────────────────────────────────
Result = tuple[int, int, str, str, str]  # (phase, idx, name, status, detail)
_results: list[Result] = []


def _record(phase: int, idx: int, name: str, status: str, detail: str = "") -> str:
    _results.append((phase, idx, name, status, detail))
    _print_step(phase, idx, name, status, detail)
    return status


def _safe(
    phase: int, idx: int, name: str, fn: Callable[[], tuple[str, str]],
) -> str:
    try:
        status, detail = fn()
    except Exception as e:  # 미처 잡지 못한 예외는 전부 FAIL
        detail = f"{type(e).__name__}: {str(e)[:120]}"
        if os.getenv("SELFTEST_DEBUG") == "1":
            traceback.print_exc()
        status = Status.FAIL
    return _record(phase, idx, name, status, detail)


# ──────────────────────────────────────────────────────────────────
# Phase 1. 정적 분석
# ──────────────────────────────────────────────────────────────────
def _run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True,
            text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        out = (r.stdout + r.stderr).strip()
        return r.returncode, out
    except FileNotFoundError:
        return 127, f"명령 없음: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout {timeout}s"


def step_ruff_check() -> tuple[str, str]:
    code, out = _run_cmd([sys.executable, "-m", "ruff", "check", "."], timeout=60)
    if code == 0:
        return Status.OK, "lint clean"
    tail = out.splitlines()[-1] if out else ""
    return Status.FAIL, f"exit {code} / {tail[:80]}"


def step_ruff_format() -> tuple[str, str]:
    code, out = _run_cmd(
        [sys.executable, "-m", "ruff", "format", "--check", "."], timeout=60,
    )
    if code == 0:
        return Status.OK, "format clean"
    last = out.splitlines()[-1] if out else ""
    return Status.WARN, f"미포맷 존재 / {last[:80]}"


def step_scan_imports() -> tuple[str, str]:
    script = PROJECT_ROOT / "scripts" / "scan_imports.py"
    code, out = _run_cmd([sys.executable, str(script)], timeout=30)
    if code == 0:
        first = out.splitlines()[0] if out else ""
        return Status.OK, first[:80]
    # FAIL 시 누락 모듈 첫 줄만 노출
    missing = [ln for ln in out.splitlines() if ln.startswith("MISSING")]
    head = missing[0] if missing else out.splitlines()[0] if out else ""
    return Status.FAIL, f"{len(missing)}개 누락 ({head[:60]})"


def step_env_keys() -> tuple[str, str]:
    required = [
        "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "KIWOOM_ACCOUNT_NO",
        "DART_API_KEY",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "KRX_OPENAPI_KEY",
    ]
    # settings 가 .env 를 이미 load_dotenv() 로 읽었는지 확인
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        return Status.FAIL, f"누락: {', '.join(missing)}"
    return Status.OK, f"{len(required)}개 키 확인"


def run_phase_1() -> int:
    _print_phase(1, "정적 분석")
    _safe(1, 1, "ruff check", step_ruff_check)
    _safe(1, 2, "ruff format --check", step_ruff_format)
    _safe(1, 3, "hidden-import 대조", step_scan_imports)
    _safe(1, 4, ".env 필수 키", step_env_keys)
    return sum(1 for r in _results if r[0] == 1 and r[3] == Status.FAIL)


# ──────────────────────────────────────────────────────────────────
# Phase 2. 단위 테스트
# ──────────────────────────────────────────────────────────────────
def run_phase_2(skip: bool = False) -> int:
    _print_phase(2, "단위 테스트")
    if skip:
        _record(2, 1, "pytest tests/", Status.SKIP, "--skip-tests")
        return 0

    t0 = time.time()
    code, out = _run_cmd(
        [sys.executable, "-m", "pytest", "tests/", "--tb=short", "-q"],
        timeout=600,
    )
    elapsed = time.time() - t0

    # pytest 요약 줄 추출 ("12 passed in 4.2s" 형태)
    summary = ""
    for line in reversed(out.splitlines()):
        s = line.strip()
        if not s:
            continue
        if "passed" in s or "failed" in s or "error" in s:
            summary = s[:100]
            break
    if code == 0:
        _record(
            2, 1, "pytest tests/", Status.OK,
            f"{summary or 'all pass'} ({elapsed:.1f}s)",
        )
        return 0
    tail = out.splitlines()[-1][:80] if out else ""
    _record(
        2, 1, "pytest tests/", Status.FAIL,
        f"exit {code} / {summary or tail}",
    )
    return 1


# ──────────────────────────────────────────────────────────────────
# Phase 3. 통합 스모크
# ──────────────────────────────────────────────────────────────────
_NETWORK_TIMEOUT = 8.0


def step_dart_ping() -> tuple[str, str]:
    import requests
    from config.settings import settings
    if not settings.dart_api_key:
        return Status.FAIL, "DART_API_KEY 비어있음"
    # 삼성전자 고유번호 00126380, 2023 사업보고서
    url = "https://opendart.fss.or.kr/api/fnlttMultiAcnt.json"
    params = {
        "crtfc_key": settings.dart_api_key,
        "corp_code": "00126380",
        "bsns_year": "2023",
        "reprt_code": "11011",
    }
    r = requests.get(url, params=params, timeout=_NETWORK_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    status = data.get("status", "")
    if status == "000":
        n = len(data.get("list", []))
        return Status.OK, f"삼성전자 계정 {n}건"
    return Status.FAIL, f"DART status={status} msg={data.get('message', '')[:60]}"


def step_kiwoom_token() -> tuple[str, str]:
    from config.settings import settings
    if not settings.is_paper_trading:
        return Status.WARN, "IS_PAPER_TRADING=False (실전 모드, 토큰 발급 건너뜀)"
    if not settings.kiwoom_app_key or not settings.kiwoom_app_secret:
        return Status.FAIL, "키움 키 비어있음"
    from trading.kiwoom_api import KiwoomRestClient
    client = KiwoomRestClient()
    tok = client.token  # property 호출 → 발급 트리거
    return Status.OK, f"token len={len(tok)} (mock)"


def step_telegram_notify(notify: bool) -> tuple[str, str]:
    from notify.telegram import TelegramNotifier
    from config.settings import settings
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return Status.FAIL, "TELEGRAM_* 비어있음"
    t = TelegramNotifier()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"🔧 quant-system self-test 시작 — {ts}"
    if not notify:
        # getMe 로 연결만 확인
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{t.token}/getMe",
            timeout=_NETWORK_TIMEOUT,
        )
        data = r.json()
        if data.get("ok"):
            return Status.OK, f"@{data['result'].get('username', '?')} (ping only)"
        return Status.FAIL, f"getMe ok=False: {data.get('description', '')[:60]}"
    ok = t.send(msg, parse_mode="")
    return (
        (Status.OK, "시작 메시지 전송") if ok
        else (Status.FAIL, "send() False 반환")
    )


def step_sqlite_check() -> tuple[str, str]:
    from config.settings import settings
    db_path = Path(settings.db_path)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    if not db_path.exists():
        return Status.FAIL, f"DB 파일 없음: {db_path}"

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        ).fetchall()
    finally:
        conn.close()
    tables = {r[0] for r in rows}
    required = {
        "daily_price", "fundamental", "market_cap",
        "factor_score", "portfolio", "trade",
    }
    missing = required - tables
    if missing:
        return Status.FAIL, f"테이블 누락: {sorted(missing)}"

    # monitor.db 별도 확인 (있으면 OK, 없으면 WARN — 신규 런에선 당연히 없음)
    mon_path = db_path.parent / "monitor.db"
    mon = "monitor.db 존재" if mon_path.exists() else "monitor.db 없음"
    return Status.OK, f"{len(tables)} tables / {mon}"


def step_screener_smoke() -> tuple[str, str]:
    from config.calendar import previous_krx_business_day
    from strategy.screener import MultiFactorScreener

    # 전일 영업일 기준
    today = datetime.now().date()
    d = previous_krx_business_day(today)
    date_str = d.strftime("%Y%m%d")

    screener = MultiFactorScreener(request_delay=0.2)
    df = screener.screen(date_str)
    if df is None or df.empty:
        return Status.FAIL, f"{date_str} 결과 비어있음"
    return Status.OK, f"{date_str} → {len(df)}종목"


def run_phase_3(notify: bool = False) -> int:
    _print_phase(3, "통합 스모크")
    _safe(3, 1, "DART API 핑", step_dart_ping)
    _safe(3, 2, "키움 토큰 (paper)", step_kiwoom_token)
    _safe(3, 3, "텔레그램", lambda: step_telegram_notify(notify))
    _safe(3, 4, "SQLite 테이블", step_sqlite_check)
    _safe(3, 5, "스크리너 1회", step_screener_smoke)
    return sum(1 for r in _results if r[0] == 3 and r[3] == Status.FAIL)


# ──────────────────────────────────────────────────────────────────
# Phase 4. exe 번들 검증
# ──────────────────────────────────────────────────────────────────
_EXE_REQUIRED_MODULES = {
    # 핵심 모니터링 (빠지면 장중 리스크 감시 죽음)
    "monitor",
    "monitor.snapshot",
    "monitor.risk_guard",
    "monitor.benchmark",
    "monitor.drift",
    "monitor.alert",
    "monitor.storage",
    # DART 공시 알림
    "dart_notifier",
    "dart_notifier.notifier",
    "dart_notifier.filter",
    # 리포트
    "quantstats",
    # 스케줄러 본체
    "scheduler.main",
    # 데이터 계층 전부
    "data.collector",
    "data.dart_client",
    "data.processor",
    "data.storage",
    # 팩터·전략
    "factors.composite",
    "factors.value",
    "factors.momentum",
    "factors.quality",
    "strategy.screener",
    "strategy.rebalancer",
    "strategy.market_regime",
    # 리밸런싱 실주문
    "trading.kiwoom_api",
    "trading.order",
    # 알림
    "notify.telegram",
}


def _parse_pyz_toc(toc_path: Path) -> set[str]:
    """PYZ-00.toc 는 파이썬 튜플 리터럴이 담긴 텍스트.

    ast.literal_eval 로 파싱해 각 엔트리의 첫 원소(모듈명)를 추출한다.
    """
    text = toc_path.read_text(encoding="utf-8", errors="replace")
    # TOC 포맷: ('<pyz path>', [(name, path, kind), ...])
    parsed = ast.literal_eval(text)
    if isinstance(parsed, tuple) and len(parsed) == 2 and isinstance(parsed[1], list):
        entries = parsed[1]
    elif isinstance(parsed, list):
        entries = parsed
    else:
        return set()
    return {e[0] for e in entries if isinstance(e, tuple) and len(e) >= 1}


def step_exe_bundle() -> tuple[str, str]:
    build_dir = PROJECT_ROOT / "build" / "KoreanQuant"
    toc = build_dir / "PYZ-00.toc"
    if not toc.exists():
        return Status.WARN, f"{toc.relative_to(PROJECT_ROOT)} 없음 (아직 빌드 안 됨)"

    bundled = _parse_pyz_toc(toc)
    if not bundled:
        return Status.FAIL, "TOC 파싱 실패 (빈 목록)"

    missing = sorted(m for m in _EXE_REQUIRED_MODULES if m not in bundled)
    if missing:
        head = ", ".join(missing[:3])
        more = f" 외 {len(missing) - 3}" if len(missing) > 3 else ""
        return Status.FAIL, f"누락 {len(missing)}개: {head}{more}"
    return Status.OK, f"{len(bundled)} 모듈 번들 / 필수 {len(_EXE_REQUIRED_MODULES)}개 포함"


def step_exe_file() -> tuple[str, str]:
    exe = PROJECT_ROOT / "KoreanQuant.exe"
    if not exe.exists():
        exe = PROJECT_ROOT / "dist" / "KoreanQuant.exe"
    if not exe.exists():
        return Status.WARN, "KoreanQuant.exe 없음 (빌드 전)"
    mb = exe.stat().st_size / (1024 * 1024)
    mtime = datetime.fromtimestamp(exe.stat().st_mtime)
    age_days = (datetime.now() - mtime).days
    detail = f"{mb:.1f} MB, {mtime:%Y-%m-%d} ({age_days}일 전)"
    if age_days > 30:
        return Status.WARN, detail + " — 빌드 오래됨"
    return Status.OK, detail


def run_phase_4(skip: bool = False) -> int:
    _print_phase(4, "exe 번들 검증")
    if skip:
        _record(4, 1, "exe 번들", Status.SKIP, "--skip-exe-check")
        _record(4, 2, "exe 파일", Status.SKIP, "--skip-exe-check")
        return 0
    _safe(4, 1, "PYZ 모듈 포함", step_exe_bundle)
    _safe(4, 2, "exe 파일 존재", step_exe_file)
    return sum(1 for r in _results if r[0] == 4 and r[3] == Status.FAIL)


# ──────────────────────────────────────────────────────────────────
# 요약
# ──────────────────────────────────────────────────────────────────
def _build_summary(elapsed: float) -> tuple[str, int, int, int]:
    ok = sum(1 for r in _results if r[3] == Status.OK)
    fail = sum(1 for r in _results if r[3] == Status.FAIL)
    warn = sum(1 for r in _results if r[3] == Status.WARN)
    skip = sum(1 for r in _results if r[3] == Status.SKIP)
    total = len(_results)

    lines = [
        "quant-system self-test 요약",
        f"- 실행 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 소요: {elapsed:.1f}s",
        f"- OK {ok} / FAIL {fail} / WARN {warn} / SKIP {skip} (총 {total})",
        "",
    ]
    for phase in sorted({r[0] for r in _results}):
        lines.append(f"Phase {phase}:")
        for _, idx, name, status, detail in [r for r in _results if r[0] == phase]:
            mark = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}.get(status, "•")
            lines.append(f"  {mark} {phase}.{idx} {name}  {detail}")
        lines.append("")
    return "\n".join(lines).rstrip(), ok, fail, warn


def _notify_summary(summary_text: str) -> None:
    try:
        from notify.telegram import TelegramNotifier
        t = TelegramNotifier()
        t.send(summary_text, parse_mode="")
    except Exception as e:
        print(f"{_YELLOW}[WARN]{_RESET} 텔레그램 요약 전송 실패: {e}")


# ──────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="quant-system 배포 무결성 자가 검증")
    parser.add_argument("--notify", action="store_true",
                        help="결과 요약을 텔레그램으로도 전송")
    parser.add_argument("--skip-tests", action="store_true",
                        help="Phase 2 (pytest) 생략")
    parser.add_argument("--skip-exe-check", action="store_true",
                        help="Phase 4 (exe 번들 검증) 생략")
    args = parser.parse_args()

    print(f"{_BOLD}═══ quant-system selftest ═══{_RESET}")
    print(f"{_GRAY}project: {PROJECT_ROOT}{_RESET}")
    started = time.time()

    run_phase_1()
    # Phase 2 가 FAIL 이면 스모크·exe 검증은 의미 없으므로 즉시 종료
    phase2_fail = run_phase_2(skip=args.skip_tests)
    if phase2_fail:
        print(f"\n{_YELLOW}Phase 2 FAIL → Phase 3/4 생략{_RESET}")
    else:
        run_phase_3(notify=args.notify)
        run_phase_4(skip=args.skip_exe_check)

    elapsed = time.time() - started
    summary_text, ok, fail, warn = _build_summary(elapsed)

    print(f"\n{_BOLD}═══ 결과 ═══{_RESET}")
    print(
        f"OK {_GREEN}{ok}{_RESET}  FAIL {_RED}{fail}{_RESET}  "
        f"WARN {_YELLOW}{warn}{_RESET}  ({elapsed:.1f}s)"
    )

    if args.notify:
        _notify_summary(summary_text)

    return 1 if fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
