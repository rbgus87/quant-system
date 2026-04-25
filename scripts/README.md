# scripts/

실전 운용 스크립트.

## 운용 스크립트

| 파일 | 용도 | 실행 시점 |
|------|------|----------|
| `selftest.py` | 4단계 시스템 점검 | 리밸런싱 D-7 |
| `backfill_data.py` | 누락 데이터 수동 백필 | 리밸런싱 D-3 |
| `auto_backfill_missing.py` | 자동 누락 복구 | 스케줄러 시작 시 |
| `import_delisted.py` | 상장폐지 데이터 갱신 | 분기 1회 |
| `scan_imports.py` | exe 빌드 import 검증 | 빌드 시 |

## 과거 실험 스크립트

v2.0 수술/검증 과정의 일회성 실험 스크립트는 모두 제거됨. 이력은 Git에서 확인:

```bash
git log --all --oneline -- scripts/
```

대표적인 분석 결과는 `docs/reports/`에 보존:
- KOSDAQ 확장 평가 (`kosdaq_expansion_analysis.md`)
- PCR 팩터 무효화 영향 (`pcr_factor_analysis.md`)
- 부분 익절 분석 (`partial_exit_analysis.md`)
- 005620 사례 (`docs/case_studies/`)
