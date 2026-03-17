"""PyInstaller 빌드 스크립트

실행: python build_exe.py
결과: dist/KoreanQuant.exe
"""

import PyInstaller.__main__
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def build() -> None:
    args = [
        os.path.join(PROJECT_ROOT, "gui", "app.py"),
        "--name=KoreanQuant",
        "--onefile",
        "--windowed",                    # 콘솔 창 숨김
        "--noconfirm",
        # 프로젝트 모듈 포함
        f"--paths={PROJECT_ROOT}",
        # config.yaml 등 데이터 파일 포함
        f"--add-data={os.path.join(PROJECT_ROOT, 'config', 'config.yaml')};config",
        # 히든 임포트 (동적 import 되는 모듈)
        "--hidden-import=config.settings",
        "--hidden-import=config.calendar",
        "--hidden-import=data.collector",
        "--hidden-import=data.storage",
        "--hidden-import=trading.kiwoom_api",
        "--hidden-import=trading.order",
        "--hidden-import=notify.telegram",
        "--hidden-import=strategy.screener",
        "--hidden-import=strategy.market_regime",
        "--hidden-import=strategy.rebalancer",
        "--hidden-import=factors.composite",
        "--hidden-import=factors.value",
        "--hidden-import=factors.momentum",
        "--hidden-import=factors.quality",
        "--hidden-import=scheduler.main",
        "--hidden-import=yaml",
        # 빌드 디렉토리
        f"--distpath={os.path.join(PROJECT_ROOT, 'dist')}",
        f"--workpath={os.path.join(PROJECT_ROOT, 'build')}",
        f"--specpath={PROJECT_ROOT}",
    ]

    print("=" * 50)
    print("Korean Quant System - exe 빌드 시작")
    print("=" * 50)

    PyInstaller.__main__.run(args)

    exe_path = os.path.join(PROJECT_ROOT, "dist", "KoreanQuant.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\n빌드 완료: {exe_path} ({size_mb:.1f} MB)")

        # 프로젝트 루트로 복사 (기존 파일 덮어쓰기)
        dest_path = os.path.join(PROJECT_ROOT, "KoreanQuant.exe")
        shutil.copy2(exe_path, dest_path)
        print(f"루트 폴더로 복사 완료: {dest_path}")
    else:
        print("\n빌드 실패!")
        sys.exit(1)


if __name__ == "__main__":
    build()
