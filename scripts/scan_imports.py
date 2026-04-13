"""scan_imports.py — 프로젝트 내 사용 모듈을 AST로 수집하고
build_exe.py 의 --hidden-import 선언과 대조한다.

- 누락된 내부 모듈이 있으면 stdout 에 `MISSING <module>` 출력
- 누락이 하나라도 있으면 exit 1

selftest.py 1단계(정적 분석)에서 서브프로세스로 호출된다.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 내부 최상위 패키지 (외부 라이브러리와 구분)
INTERNAL_PACKAGES = {
    "config", "data", "factors", "strategy", "backtest",
    "trading", "notify", "monitor", "scheduler", "dart_notifier",
    "gui",
}

# 스캔에서 제외할 디렉토리
EXCLUDE_DIRS = {
    "build", "dist", ".venv", "venv", "__pycache__",
    "tests", "scripts", "notebooks", "archived",
    "data",  # 데이터 디렉토리 (DB 파일)
    "logs", ".git",
}

# 추가로 PyInstaller 가 못 잡는 외부 hidden-import 필요 모듈
# (런타임에 동적 import 되므로 명시 필요)
REQUIRED_EXTERNAL_HIDDEN = {
    "yaml",        # config.yaml 로드
    "quantstats",  # HTML 리포트
    "apscheduler", # 스케줄러
    "finance_datareader",  # FDR 폴백
    "pykrx_openapi",       # KRX Open API
}


def collect_used_internal_modules() -> set[str]:
    """프로젝트 소스코드에서 import 된 내부 모듈명을 수집."""
    used: set[str] = set()

    for py_file in PROJECT_ROOT.rglob("*.py"):
        rel = py_file.relative_to(PROJECT_ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in INTERNAL_PACKAGES:
                        used.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top in INTERNAL_PACKAGES:
                        used.add(node.module)

    return used


def parse_hidden_imports(build_exe_path: Path) -> set[str]:
    """build_exe.py 에서 --hidden-import=<module> 패턴을 추출."""
    declared: set[str] = set()
    if not build_exe_path.exists():
        return declared

    text = build_exe_path.read_text(encoding="utf-8")
    for match in re.finditer(r"--hidden-import=([a-zA-Z0-9_.]+)", text):
        declared.add(match.group(1))
    return declared


def main() -> int:
    build_exe = PROJECT_ROOT / "build_exe.py"
    used_internal = collect_used_internal_modules()
    declared = parse_hidden_imports(build_exe)

    # 1) 내부 모듈 누락 체크
    missing_internal: list[str] = []
    for mod in sorted(used_internal):
        top = mod.split(".")[0]
        # top-level 패키지 자체만 선언돼도 OK 로 간주
        if mod in declared or top in declared:
            continue
        # 서브모듈이 선언돼도 top-level 이 선언됐다면 OK
        parent_parts = mod.split(".")
        ok = any(
            ".".join(parent_parts[: i + 1]) in declared
            for i in range(len(parent_parts))
        )
        if not ok:
            missing_internal.append(mod)

    # 2) 외부 런타임 hidden-import 누락 체크
    missing_external: list[str] = []
    for mod in sorted(REQUIRED_EXTERNAL_HIDDEN):
        if mod not in declared:
            missing_external.append(mod)

    if not missing_internal and not missing_external:
        print(
            f"OK  내부 {len(used_internal)}개 / 외부 필수 "
            f"{len(REQUIRED_EXTERNAL_HIDDEN)}개 hidden-import 확인"
        )
        return 0

    for mod in missing_internal:
        print(f"MISSING internal  {mod}")
    for mod in missing_external:
        print(f"MISSING external  {mod}")
    print(
        f"\n누락 내부 {len(missing_internal)}개, 외부 {len(missing_external)}개. "
        f"build_exe.py 에 --hidden-import=<module> 추가 필요."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
