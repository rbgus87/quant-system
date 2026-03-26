# scripts/

v2.0 수술 과정에서 사용한 실험 스크립트. 실전 운용에 불필요.

`archived/` 폴더에 보존되어 있으며, 전략 검증 과정을 재현할 때 참고용으로 사용합니다.

## 포함된 스크립트

| 파일 | 용도 |
|------|------|
| `v2_validation.py` | 팩터별 단독/조합 전략 비교 (V/M/Q 가중치 실험) |
| `v2_mdd_experiment.py` | MDD 관리 실험 (시장 레짐, 서킷브레이커) |
| `v2_stock_risk.py` | 종목 레벨 리스크 관리 (트레일링 스톱, 변동성 필터) |
| `v2_recent_opt.py` | 최근 구간 최적화 + 역검증 (과적합 체크) |
| `v2_period_analysis.py` | 구간별/연도별/롤링 CAGR 분석 |
| `alpha_experiments.py` | 팩터별 단독 + 종목 수 변화 실험 |
| `backtest_v11_comparison.py` | v1.1 vs v2.0 비교 |
| 기타 | 진단/분석 도구 |
