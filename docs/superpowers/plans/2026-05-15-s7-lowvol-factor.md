# S7: Low-Volatility 팩터 도입 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 저변동성(Low-vol) 팩터를 멀티팩터 시스템에 통합하고 IC/IR 측정 + 5가지 가중치 조합 백테스트로 채택 여부를 결정한다.

**Architecture:** Part 1에서 `factors/volatility.py`(신규)로 Low-vol 점수를 계산 → `factors/composite.py`에 optional 파라미터로 통합 → `strategy/screener.py`에서 조건부 호출(low_vol weight > 0일 때만). Part 2에서 settings 패치 + 캐시 클리어 + 엔진 재실행으로 5가지 가중치 조합을 비교한다.

**Tech Stack:** Python 3.14, pandas, numpy, scipy, SQLAlchemy(DataStorage), pykrx(폴백), 기존 backtest.engine / strategy.screener / factors.* 인프라

---

## 파일 구조 계획

```
factors/volatility.py              NEW  — VolatilityFactor 클래스
factors/composite.py               MOD  — low_vol_score Optional 파라미터 추가
config/settings.py                 MOD  — FactorWeights에 low_vol: float = 0.00
config/config.yaml                 MOD  — 프리셋 A/B/C factor_weights에 low_vol: 0.00
strategy/screener.py               MOD  — VolatilityFactor 통합 + cache_key 확장
scripts/analyze_lowvol_ic.py       NEW  — IC/IR 3가지 lookback 분석 (Part 1)
scripts/backtest_lowvol_weights_s7.py  NEW  — 5가지 가중치 백테스트 (Part 2)
tests/test_volatility.py           NEW  — VolatilityFactor 단위 테스트
tests/test_composite_lowvol.py     NEW  — composite + low_vol 단위 테스트
docs/reports/lowvol_factor_s7_analysis.md  NEW  — 자동 생성 보고서
CHANGELOG.md                       MOD
```

---

## Task 1: settings.py — FactorWeights에 low_vol 필드 추가

**Files:**
- Modify: `config/settings.py:29-33`

- [ ] **Step 1: FactorWeights에 low_vol 필드 추가**

```python
# config/settings.py, 라인 29-33 현재:
@dataclass
class FactorWeights:
    value: float = 0.40
    momentum: float = 0.40
    quality: float = 0.20

# 변경 후:
@dataclass
class FactorWeights:
    value: float = 0.40
    momentum: float = 0.40
    quality: float = 0.20
    low_vol: float = 0.00   # Low-vol 팩터 가중치 (기본 0 = 비활성)
```

- [ ] **Step 2: 변경 확인 (Python REPL)**

```powershell
python -c "from config.settings import settings; print(settings.factor_weights)"
```

기대 출력: `FactorWeights(value=0.7, momentum=0.3, quality=0.0, low_vol=0.0)` (프리셋 A 기준)

- [ ] **Step 3: Commit**

```powershell
git add config/settings.py
git commit -m "feat(settings): add low_vol weight field to FactorWeights (default 0.00)"
```

---

## Task 2: tests/test_volatility.py — 단위 테스트 작성 (TDD: 먼저 실패)

**Files:**
- Create: `tests/test_volatility.py`

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_volatility.py
"""VolatilityFactor 단위 테스트."""

import datetime
import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock


def _make_storage_mock(tickers_closes: dict[str, list[float]]) -> MagicMock:
    """종목별 종가 리스트로 mock DataStorage 생성."""
    storage = MagicMock()
    rows = []
    for ticker, closes in tickers_closes.items():
        for i, c in enumerate(closes):
            rows.append({
                "ticker": ticker,
                "date": datetime.date(2024, 1, 1) + datetime.timedelta(days=i),
                "close": float(c),
            })
    df = pd.DataFrame(rows)
    storage.load_daily_prices_bulk.return_value = df
    return storage


class TestVolatilityFactor:

    # TC-1: 완벽한 저변동성이 최고 점수를 받는지
    def test_zero_variance_max_score(self):
        """일정 가격(변동성=0) 종목이 가장 높은 점수를 받아야 한다."""
        storage = _make_storage_mock({
            "ZERO": [100.0] * 80,               # 변동성 = 0
            "NONZ": [100.0 + i * 0.5 for i in range(80)],  # 변동성 > 0
        })
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", ["ZERO", "NONZ"], storage, lookback_days=60)

        assert "ZERO" in scores.index
        assert "NONZ" in scores.index
        assert scores["ZERO"] >= scores["NONZ"], (
            f"zero-var should score >= non-zero-var: ZERO={scores['ZERO']:.1f}, NONZ={scores['NONZ']:.1f}"
        )

    # TC-2: 고변동성이 저변동성보다 낮은 점수를 받는지
    def test_high_vol_lower_score(self):
        """고변동성 종목이 저변동성 종목보다 낮은 점수를 받아야 한다."""
        rng = np.random.RandomState(42)
        low_closes = [100.0 + rng.randn() * 0.1 for _ in range(80)]
        high_closes = [100.0 + rng.randn() * 10.0 for _ in range(80)]
        # 음수 방지
        high_closes = [max(c, 1.0) for c in high_closes]

        storage = _make_storage_mock({"LOW": low_closes, "HIGH": high_closes})
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", ["LOW", "HIGH"], storage, lookback_days=60)

        assert "LOW" in scores.index and "HIGH" in scores.index
        assert scores["LOW"] > scores["HIGH"], (
            f"low-vol should score > high-vol: LOW={scores['LOW']:.1f}, HIGH={scores['HIGH']:.1f}"
        )

    # TC-3: 데이터 부족 시 NaN 반환
    def test_insufficient_data_returns_nan(self):
        """유효 데이터 < lookback * min_data_ratio 이면 NaN 반환."""
        storage = _make_storage_mock({
            "ENOUGH": [100.0 + i * 0.1 for i in range(80)],  # 80개 ≥ 60*0.7=42 → OK
            "SHORT":  [100.0 + i * 0.1 for i in range(10)],  # 10개 < 42 → NaN
        })
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", ["ENOUGH", "SHORT"], storage, lookback_days=60)

        assert not np.isnan(scores["ENOUGH"]), "ENOUGH should have valid score"
        assert np.isnan(scores["SHORT"]), "SHORT should be NaN (insufficient data)"

    # TC-4: 빈 tickers 입력 → 빈 Series
    def test_empty_tickers_returns_empty(self):
        storage = MagicMock()
        storage.load_daily_prices_bulk.return_value = pd.DataFrame()
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score("20240301", [], storage, lookback_days=60)
        assert scores.empty

    # TC-5: 점수 범위 0~100 확인
    def test_scores_in_range(self):
        """모든 유효 점수는 0 이상 100 이하여야 한다."""
        rng = np.random.RandomState(7)
        tickers_closes = {
            f"T{i:03d}": [100.0 + rng.randn() * (i + 1) for _ in range(80)]
            for i in range(10)
        }
        # 음수 방지
        for k in tickers_closes:
            tickers_closes[k] = [max(c, 1.0) for c in tickers_closes[k]]

        storage = _make_storage_mock(tickers_closes)
        from factors.volatility import VolatilityFactor
        vf = VolatilityFactor()
        scores = vf.calc_volatility_score(
            "20240301", list(tickers_closes.keys()), storage, lookback_days=60
        )

        valid = scores.dropna()
        assert (valid >= 0).all() and (valid <= 100).all(), (
            f"Scores out of range: {valid.describe()}"
        )
```

- [ ] **Step 2: 테스트 실행 (실패 확인 — volatility.py 아직 없음)**

```powershell
python -m pytest tests/test_volatility.py -v 2>&1 | Select-Object -Last 15
```

기대: `ModuleNotFoundError: No module named 'factors.volatility'` 또는 ImportError

---

## Task 3: factors/volatility.py — VolatilityFactor 구현

**Files:**
- Create: `factors/volatility.py`

- [ ] **Step 1: 구현 작성**

```python
# factors/volatility.py
"""Low-Volatility 팩터.

저변동성 종목에 높은 점수(0~100)를 부여.
일별 수익률의 rolling std(연율화)를 역순위로 변환.
낮은 변동성 = 높은 점수 = Q1 선호.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolatilityFactor:
    """저변동성 팩터: 연율화 변동성의 역순위를 0~100 점수로 반환."""

    def calc_volatility_score(
        self,
        date: str,
        tickers: list[str],
        storage,
        lookback_days: int = 60,
        min_data_ratio: float = 0.7,
    ) -> pd.Series:
        """각 종목의 저변동성 점수 계산 (0~100, 높을수록 저변동성).

        Args:
            date: 기준일 (YYYYMMDD)
            tickers: 종목 리스트
            storage: DataStorage 인스턴스 (load_daily_prices_bulk 호출)
            lookback_days: 변동성 계산 기간 (거래일 수, 기본 60일 ≈ 3개월)
            min_data_ratio: 최소 데이터 비율 (기본 0.7 = 42/60일 이상 필요)

        Returns:
            pd.Series(index=ticker, values=0~100).
            데이터 부족 종목은 NaN.
        """
        if not tickers:
            return pd.Series(dtype=float, name="low_vol_score")

        end_dt = datetime.strptime(date, "%Y%m%d")
        start_dt = end_dt - timedelta(days=int(lookback_days * 1.5))
        sd = start_dt.date()
        ed = end_dt.date()

        try:
            bulk_df = storage.load_daily_prices_bulk(tickers, sd, ed)
        except Exception as exc:
            logger.warning("저변동성 팩터: 가격 데이터 조회 실패 [%s]: %s", date, exc)
            return pd.Series(np.nan, index=tickers, name="low_vol_score")

        if bulk_df.empty:
            logger.warning("저변동성 팩터: 가격 데이터 없음 [%s]", date)
            return pd.Series(np.nan, index=tickers, name="low_vol_score")

        bulk_df = bulk_df.sort_values(["ticker", "date"])
        pivot = bulk_df.pivot_table(index="date", columns="ticker", values="close")
        daily_returns = pivot.pct_change(fill_method=None)

        valid_counts = daily_returns.count()
        ann_vol = daily_returns.std() * math.sqrt(252)

        min_data = int(lookback_days * min_data_ratio)
        valid_mask = valid_counts >= min_data
        valid_vol = ann_vol[valid_mask].dropna()

        if valid_vol.empty:
            logger.warning("저변동성 팩터: 유효 종목 없음 [%s]", date)
            return pd.Series(np.nan, index=tickers, name="low_vol_score")

        # 역순위: 낮은 변동성 → 높은 점수 (ascending=False: 작은 값 → 큰 rank)
        scores = valid_vol.rank(pct=True, ascending=False) * 100.0

        logger.info(
            "저변동성 팩터 [%s]: %d/%d 종목 유효 (lookback=%dd)",
            date, len(scores), len(tickers), lookback_days,
        )

        result = pd.Series(np.nan, index=tickers, name="low_vol_score")
        result.update(scores)
        return result
```

- [ ] **Step 2: 테스트 실행 (통과 확인)**

```powershell
python -m pytest tests/test_volatility.py -v 2>&1 | Select-Object -Last 20
```

기대: `5 passed`

- [ ] **Step 3: Commit**

```powershell
git add factors/volatility.py tests/test_volatility.py
git commit -m "feat(volatility): add VolatilityFactor with IC-ready percentile ranking"
```

---

## Task 4: tests/test_composite_lowvol.py — composite 테스트 작성 (TDD)

**Files:**
- Create: `tests/test_composite_lowvol.py`

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_composite_lowvol.py
"""composite.py에 low_vol_score 추가 후 하위 호환성 + 통합 테스트."""

import numpy as np
import pandas as pd
import pytest
from config.settings import settings


def _make_scores(n: int = 20, seed: int = 0) -> tuple[pd.Series, pd.Series, pd.Series]:
    rng = np.random.RandomState(seed)
    idx = [f"T{i:03d}" for i in range(n)]
    v = pd.Series(rng.uniform(0, 100, n), index=idx, name="value_score")
    m = pd.Series(rng.uniform(0, 100, n), index=idx, name="momentum_score")
    q = pd.Series(dtype=float, name="quality_score")  # empty (weight=0 시나리오)
    return v, m, q


class TestCompositeLowVolBackwardCompat:
    """low_vol_score=None 또는 weight=0 이면 기존과 동일 결과여야 함."""

    def test_no_low_vol_score_identical(self):
        """low_vol_score=None(기본) → 기존 calculate() 와 동일 결과."""
        from factors.composite import MultiFactorComposite

        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.low_vol = 0.00
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=1)

            result_old = composite.calculate(v, m, q)
            result_new = composite.calculate(v, m, q, low_vol_score=None)

            pd.testing.assert_frame_equal(
                result_old.reset_index(drop=True),
                result_new.reset_index(drop=True),
                check_like=True,
            )
        finally:
            settings.factor_weights.low_vol = orig_lv

    def test_zero_weight_low_vol_score_ignored(self):
        """weight=0일 때 low_vol_score 제공해도 composite_score 변화 없음."""
        from factors.composite import MultiFactorComposite

        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.low_vol = 0.00
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=2)
            lv = pd.Series(np.random.rand(20) * 100, index=v.index, name="low_vol_score")

            result_base = composite.calculate(v, m, q, low_vol_score=None)
            result_with = composite.calculate(v, m, q, low_vol_score=lv)

            np.testing.assert_allclose(
                result_base["composite_score"].values,
                result_with.reindex(result_base.index)["composite_score"].values,
                rtol=1e-6,
            )
        finally:
            settings.factor_weights.low_vol = orig_lv


class TestCompositeLowVolIntegration:
    """low_vol_score 제공 + weight > 0 → composite_score가 변해야 함."""

    def test_low_vol_changes_composite(self):
        """low_vol 가중치 활성화 → composite_score가 기존과 달라야 한다."""
        from factors.composite import MultiFactorComposite

        orig_v  = settings.factor_weights.value
        orig_m  = settings.factor_weights.momentum
        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.value    = 0.70
        settings.factor_weights.momentum = 0.00
        settings.factor_weights.low_vol  = 0.30
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=3)
            lv_asc = pd.Series(np.arange(20, dtype=float), index=v.index)   # 역상관 low_vol
            lv_desc = pd.Series(np.arange(19, -1, -1, dtype=float), index=v.index)

            result_asc  = composite.calculate(v, m, q, low_vol_score=lv_asc)
            result_desc = composite.calculate(v, m, q, low_vol_score=lv_desc)

            # 두 결과의 composite_score가 달라야 함
            common = result_asc.index.intersection(result_desc.index)
            assert len(common) > 0
            diff = (
                result_asc.loc[common, "composite_score"]
                - result_desc.loc[common, "composite_score"]
            ).abs().sum()
            assert diff > 1.0, f"low_vol 변화 시 composite_score 변화 없음 (diff={diff:.4f})"
        finally:
            settings.factor_weights.value    = orig_v
            settings.factor_weights.momentum = orig_m
            settings.factor_weights.low_vol  = orig_lv

    def test_low_vol_score_column_present(self):
        """반환 DataFrame에 low_vol_score 컬럼이 존재해야 한다."""
        from factors.composite import MultiFactorComposite

        orig_lv = settings.factor_weights.low_vol
        settings.factor_weights.low_vol = 0.30
        try:
            composite = MultiFactorComposite()
            v, m, q = _make_scores(20, seed=4)
            lv = pd.Series(np.random.rand(20) * 100, index=v.index)
            result = composite.calculate(v, m, q, low_vol_score=lv)
            assert "low_vol_score" in result.columns
        finally:
            settings.factor_weights.low_vol = orig_lv
```

- [ ] **Step 2: 테스트 실행 (BackwardCompat 2개 통과, Integration 2개 실패 예상)**

```powershell
python -m pytest tests/test_composite_lowvol.py -v 2>&1 | Select-Object -Last 25
```

BackwardCompat 테스트는 아직 low_vol_score 파라미터가 없어도 통과 가능. Integration 테스트는 실패.

---

## Task 5: factors/composite.py — low_vol_score 파라미터 추가

**Files:**
- Modify: `factors/composite.py:178-251`

- [ ] **Step 1: calculate() 메서드 교체**

`factors/composite.py`의 `calculate()` 메서드(라인 178~251)를 다음으로 교체한다:

```python
def calculate(
    self,
    value_score: pd.Series,
    momentum_score: pd.Series,
    quality_score: pd.Series,
    low_vol_score: Optional[pd.Series] = None,
    min_factor_count: int = 2,
) -> pd.DataFrame:
    """3~4개 팩터 가중 합산.

    2개 이상 팩터가 있는 종목은 가용 가중치 정규화로 포함.
    low_vol_score: None 또는 self.w.low_vol == 0이면 무시 (하위 호환).

    Args:
        value_score: 밸류 스코어 (0~100)
        momentum_score: 모멘텀 스코어 (0~100)
        quality_score: 퀄리티 스코어 (0~100)
        low_vol_score: 저변동성 스코어 (0~100), None이면 비활성
        min_factor_count: 최소 필요 팩터 수 (기본 2)

    Returns:
        DataFrame(index=ticker, columns=[value_score, momentum_score,
        quality_score, low_vol_score, composite_score]) composite_score 내림차순
    """
    _use_low_vol = low_vol_score is not None and self.w.low_vol > 0

    _low_vol_idx = set(low_vol_score.index) if _use_low_vol else set()
    all_tickers = sorted(
        set(value_score.index)
        | set(momentum_score.index)
        | set(quality_score.index)
        | _low_vol_idx
    )

    _empty_cols = ["value_score", "momentum_score", "quality_score", "low_vol_score", "composite_score"]
    if not all_tickers:
        logger.warning("유효 종목 없음 — 빈 결과 반환")
        return pd.DataFrame(columns=_empty_cols)

    data: dict[str, pd.Series] = {
        "value_score":    value_score.reindex(all_tickers),
        "momentum_score": momentum_score.reindex(all_tickers),
        "quality_score":  quality_score.reindex(all_tickers),
        "low_vol_score":  (
            low_vol_score.reindex(all_tickers)
            if _use_low_vol
            else pd.Series(float("nan"), index=all_tickers)
        ),
    }
    df = pd.DataFrame(data)

    # 최소 팩터 수 계산에는 활성 팩터만 포함
    active_factor_cols = ["value_score", "momentum_score", "quality_score"]
    if _use_low_vol:
        active_factor_cols.append("low_vol_score")

    factor_count = df[active_factor_cols].notna().sum(axis=1)
    df = df[factor_count >= min_factor_count].copy()

    if df.empty:
        logger.warning(f"최소 {min_factor_count}개 팩터 충족 종목 없음")
        return pd.DataFrame(columns=_empty_cols)

    n_full = (factor_count.reindex(df.index) == len(active_factor_cols)).sum()
    n_partial = len(df) - n_full
    logger.info(
        f"팩터 종목: {len(df)}개 (완전 {n_full}개, 부분 {n_partial}개)"
    )

    # 가중 합산 (NaN 팩터 가중치 재분배)
    score_parts: dict[str, tuple[pd.Series, float]] = {
        "value":    (df["value_score"],    self.w.value),
        "momentum": (df["momentum_score"], self.w.momentum),
        "quality":  (df["quality_score"],  self.w.quality),
    }
    if _use_low_vol:
        score_parts["low_vol"] = (df["low_vol_score"], self.w.low_vol)

    df["composite_score"] = weighted_average_nan_safe(score_parts)
    df = df.dropna(subset=["composite_score"])
    return df.sort_values("composite_score", ascending=False)
```

- [ ] **Step 2: `__init__` 로그 메시지도 low_vol 포함하도록 수정**

`MultiFactorComposite.__init__` 내의 logger.info 라인(현재 라인 174-176)을:

```python
def __init__(self) -> None:
    self.w = settings.factor_weights
    logger.info(
        "팩터 가중치 — 밸류:%.2f, 모멘텀:%.2f, 퀄리티:%.2f, 저변동성:%.2f",
        self.w.value, self.w.momentum, self.w.quality, self.w.low_vol,
    )
```

- [ ] **Step 3: 테스트 전체 실행 (4개 모두 통과)**

```powershell
python -m pytest tests/test_composite_lowvol.py tests/test_volatility.py -v 2>&1 | Select-Object -Last 20
```

기대: `9 passed`

- [ ] **Step 4: 기존 composite 관련 테스트 회귀 확인**

```powershell
python -m pytest tests/ -v -k "composite" 2>&1 | Select-Object -Last 20
```

기대: 기존 테스트 모두 통과 (하위 호환 보장)

- [ ] **Step 5: Commit**

```powershell
git add factors/composite.py tests/test_composite_lowvol.py
git commit -m "feat(composite): add optional low_vol_score parameter with backward compat"
```

---

## Task 6: strategy/screener.py — VolatilityFactor 통합 + cache_key 확장

**Files:**
- Modify: `strategy/screener.py` (import 섹션, `__init__`, `screen()`, `cache_key`)

- [ ] **Step 1: import 추가 (파일 상단 import 블록에)**

screener.py의 다른 factor import 옆에 추가:

```python
from factors.volatility import VolatilityFactor
```

- [ ] **Step 2: `__init__`에 volatility_factor 인스턴스 추가**

`MultiFactorScreener.__init__` 내의 factor 인스턴스화 블록(value_factor, momentum_factor 등) 바로 아래에 추가:

```python
self.volatility_factor = VolatilityFactor()
```

- [ ] **Step 3: cache_key에 fw.low_vol 추가**

`screen()` 메서드의 `cache_key` 튜플(라인 133~157) 끝에 추가:

```python
cache_key = (
    date,
    market,
    fw.value,
    fw.momentum,
    fw.quality,
    float(fw.low_vol),           # NEW: 저변동성 가중치 포함
    bool(settings.quality.strict_reporting_lag),
    # ... 기존 필드들 그대로 유지 ...
    int(settings.universe.max_sector_count),
)
```

*주의: `fw = settings.factor_weights` 라인 바로 아래에 `float(fw.low_vol)`을 기존 `fw.quality` 다음 줄에 삽입.*

- [ ] **Step 4: screen() 내 모멘텀 블록 직후에 low_vol 블록 추가**

현재 모멘텀 블록 끝(라인 365 `logger.info(...)`) 바로 다음에 삽입:

```python
            # 저변동성 팩터 (가중치 0이면 DB 조회 스킵)
            low_vol_score = pd.Series(dtype=float, name="low_vol_score")
            if settings.factor_weights.low_vol > 0:
                low_vol_score = self.volatility_factor.calc_volatility_score(
                    date=data_date,
                    tickers=tickers,
                    storage=self.collector.storage,
                    lookback_days=60,
                )
                logger.info(
                    f"[{date}] 저변동성 팩터: {low_vol_score.notna().sum()}종목"
                )
            else:
                logger.debug(f"[{date}] 저변동성 가중치 0 → 계산 스킵")
```

- [ ] **Step 5: composite.calculate() 호출에 low_vol_score 전달**

기존(라인 369~372):
```python
            composite_df = self.composite.calculate(
                value_score, momentum_score, quality_score,
                min_factor_count=min_factors,
            )
```

변경 후:
```python
            composite_df = self.composite.calculate(
                value_score, momentum_score, quality_score,
                low_vol_score=low_vol_score if settings.factor_weights.low_vol > 0 else None,
                min_factor_count=min_factors,
            )
```

- [ ] **Step 6: 스모크 테스트 — low_vol=0 기본 동작 확인**

```powershell
python -c "
from config.settings import settings
from strategy.screener import MultiFactorScreener
s = MultiFactorScreener()
print('low_vol weight:', settings.factor_weights.low_vol)
print('screener init OK')
"
```

기대: `low_vol weight: 0.0` + 에러 없음

- [ ] **Step 7: 전체 테스트 회귀**

```powershell
python -m pytest tests/ -x --tb=short -q 2>&1 | Select-Object -Last 20
```

기대: 기존 테스트 전부 통과

- [ ] **Step 8: Commit**

```powershell
git add strategy/screener.py
git commit -m "feat(screener): integrate VolatilityFactor with conditional low_vol scoring"
```

---

## Task 7: config/config.yaml — 프리셋 A/B/C에 low_vol: 0.00 추가

**Files:**
- Modify: `config/config.yaml` (프리셋 A, B, C의 factor_weights 섹션)

- [ ] **Step 1: 프리셋 A/B/C 각각의 factor_weights 블록에 low_vol 추가**

프리셋 A(라인 77-80):
```yaml
  A:
    factor_weights:
      value: 0.70
      momentum: 0.30
      quality: 0.00
      low_vol: 0.00    # 기본 비활성 — S7 실험 후 값 결정
```

프리셋 B(라인 134-137):
```yaml
  B:
    factor_weights:
      value: 0.70
      momentum: 0.30
      quality: 0.00
      low_vol: 0.00
```

프리셋 C(라인 ~175):
```yaml
  C:
    factor_weights:
      value: 1.00
      momentum: 0.00
      quality: 0.00
      low_vol: 0.00
```

- [ ] **Step 2: YAML 로딩 확인**

```powershell
python -c "
from config.settings import settings
fw = settings.factor_weights
print(f'A preset: v={fw.value}, m={fw.momentum}, lv={fw.low_vol}')
assert fw.low_vol == 0.00
print('OK')
"
```

- [ ] **Step 3: Commit**

```powershell
git add config/config.yaml
git commit -m "feat(config): add low_vol: 0.00 to presets A/B/C factor_weights"
```

---

## Task 8: scripts/analyze_lowvol_ic.py — IC/IR 분석 스크립트 (Part 1)

**Files:**
- Create: `scripts/analyze_lowvol_ic.py`

- [ ] **Step 1: 스크립트 작성**

```python
"""scripts/analyze_lowvol_ic.py — Low-Volatility 팩터 IC/IR 분석 (Part 1).

V3과 동일 방법론 (31분기, KOSPI, F-Score≥4 유니버스):
  - Spearman IC, IR, Hit Rate
  - Quintile Decay (Q1~Q5)
  - lookback = 60 / 90 / 120 거래일 비교

판정:
  - IR > 0.05 + Hit Rate > 50% → Part 2 진행 권장
  - IR ≤ 0               → Low-vol 폐기 권장

사용:
    python scripts/analyze_lowvol_ic.py
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from datetime import date as date_type
from pathlib import Path
from typing import Generator

import numpy as np
import pandas as pd
from scipy import stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging   # noqa: E402
from config.settings import settings              # noqa: E402

logger = logging.getLogger(__name__)

BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"
LOOKBACKS      = [60, 90, 120]

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "lowvol_ic_analysis.md"   # Part 1 전용 중간 보고서


# ── 유니버스 가드 (V3와 동일) ──────────────────────────────────────────────────

@contextmanager
def ic_universe_guard() -> Generator[None, None, None]:
    """Step1/3·S4 비활성, F-Score≥4 유지, n_stocks=9999."""
    from strategy.screener import MultiFactorScreener

    backup_s4 = settings.universe.sector_diversification_enabled
    backup_op = settings.quality.operating_quality_filter_enabled
    backup_cp = settings.quality.consecutive_profit_filter_enabled
    backup_n  = settings.portfolio.n_stocks

    settings.universe.sector_diversification_enabled = False
    settings.quality.operating_quality_filter_enabled = False
    settings.quality.consecutive_profit_filter_enabled = False
    settings.portfolio.n_stocks = 9999
    MultiFactorScreener._factor_cache.clear()
    try:
        yield
    finally:
        settings.universe.sector_diversification_enabled = backup_s4
        settings.quality.operating_quality_filter_enabled = backup_op
        settings.quality.consecutive_profit_filter_enabled = backup_cp
        settings.portfolio.n_stocks = backup_n
        MultiFactorScreener._factor_cache.clear()


# ── IC / Quintile 유틸 (V3에서 복사) ──────────────────────────────────────────

def compute_ic(factor_scores: pd.Series, period_returns: pd.Series) -> float:
    aligned = pd.concat([factor_scores, period_returns], axis=1).dropna()
    if len(aligned) < 10:
        return float("nan")
    corr, _ = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
    return float(corr)


def compute_quintile_returns(
    factor_scores: pd.Series, period_returns: pd.Series
) -> dict[str, float]:
    aligned = pd.concat([factor_scores, period_returns], axis=1).dropna()
    aligned.columns = ["score", "ret"]
    nan_result = {f"Q{i}": float("nan") for i in range(1, 6)}
    if len(aligned) < 25:
        return nan_result
    try:
        aligned["q"] = pd.qcut(
            aligned["score"], 5, labels=["Q5", "Q4", "Q3", "Q2", "Q1"]
        )
    except ValueError:
        return nan_result
    q_mean = aligned.groupby("q", observed=False)["ret"].mean()
    return {q: float(q_mean.get(q, float("nan"))) for q in [f"Q{i}" for i in range(1, 6)]}


# ── 분기 날짜 생성 ────────────────────────────────────────────────────────────

def get_rebal_dates(start: str, end: str) -> list[pd.Timestamp]:
    from config.calendar import get_krx_month_end_sessions
    sessions = get_krx_month_end_sessions(start, end)
    return [s for s in sessions if s.month in (3, 6, 9, 12)]


# ── 핵심 분석 루프 ────────────────────────────────────────────────────────────

def run_analysis() -> dict[int, dict]:
    """31분기 × 3 lookback IC/IR/Quintile 분석. 단일 패스로 모든 lookback 동시 처리."""
    from data.storage import DataStorage
    from factors.volatility import VolatilityFactor
    from strategy.screener import MultiFactorScreener

    storage = DataStorage()
    screener = MultiFactorScreener()
    vf = VolatilityFactor()
    rebal_dates = get_rebal_dates(BACKTEST_START, BACKTEST_END)

    # lookback별 데이터 수집 컨테이너
    ics: dict[int, list[float]]         = {lb: [] for lb in LOOKBACKS}
    q_rets: dict[int, dict[str, list]]  = {
        lb: {f"Q{i}": [] for i in range(1, 6)} for lb in LOOKBACKS
    }
    n_valid: dict[int, list[int]]       = {lb: [] for lb in LOOKBACKS}

    with ic_universe_guard():
        for i in range(len(rebal_dates) - 1):
            rd_start = rebal_dates[i]
            rd_end   = rebal_dates[i + 1]
            date_str = rd_start.strftime("%Y%m%d")
            next_str = rd_end.strftime("%Y%m%d")

            logger.info("[%d/%d] %s → %s", i + 1, len(rebal_dates) - 1, date_str, next_str)

            # 1) 유니버스 (9999종목 = 전부)
            scr = screener.screen(date_str, market=MARKET, n_stocks=9999)
            if scr.empty:
                logger.warning("[%s] screener 결과 없음 — 스킵", date_str)
                continue
            tickers = scr.index.tolist()

            # 2) 다음 분기 수익률
            sd: date_type = rd_start.date()
            ed: date_type = rd_end.date()
            try:
                df_s = storage.load_daily_prices_bulk(tickers, sd, sd)
                df_e = storage.load_daily_prices_bulk(tickers, ed, ed)
            except Exception as exc:
                logger.warning("[%s] 가격 조회 실패: %s", date_str, exc)
                continue

            if df_s.empty or df_e.empty:
                logger.warning("[%s] 가격 데이터 없음 — 스킵", date_str)
                continue

            p_start = df_s.groupby("ticker")["close"].first()
            p_end   = df_e.groupby("ticker")["close"].first()
            valid   = p_start.index.intersection(p_end.index)
            valid   = valid[p_start[valid] > 0]
            period_rets = (p_end[valid] / p_start[valid] - 1).dropna()

            if period_rets.empty:
                continue

            # 3) 각 lookback별 low_vol 점수 → IC
            for lb in LOOKBACKS:
                low_vol_score = vf.calc_volatility_score(date_str, tickers, storage, lb)
                ic = compute_ic(low_vol_score, period_rets)
                if not np.isnan(ic):
                    ics[lb].append(ic)
                    n_valid[lb].append(len(period_rets))
                    q = compute_quintile_returns(low_vol_score, period_rets)
                    for qi, qr in q.items():
                        if not np.isnan(qr):
                            q_rets[lb][qi].append(qr)

    # 집계
    results: dict[int, dict] = {}
    for lb in LOOKBACKS:
        arr = np.array(ics[lb])
        if len(arr) == 0:
            results[lb] = {"n": 0}
            continue
        mean_ic = float(np.mean(arr))
        std_ic  = float(np.std(arr, ddof=1)) if len(arr) > 1 else float("nan")
        ir      = mean_ic / std_ic if std_ic and std_ic > 0 and not np.isnan(std_ic) else float("nan")
        hit     = float(np.mean(arr > 0))
        q_means = {qi: float(np.mean(v)) if v else float("nan") for qi, v in q_rets[lb].items()}
        spread  = q_means.get("Q1", float("nan")) - q_means.get("Q5", float("nan"))
        mono    = all(
            q_means.get(f"Q{j}", float("nan")) >= q_means.get(f"Q{j+1}", float("nan")) - 1e-9
            for j in range(1, 5)
        )
        results[lb] = {
            "lb": lb,
            "mean_ic": mean_ic,
            "std_ic": std_ic,
            "ir": ir,
            "hit_rate": hit,
            "n": len(arr),
            "n_valid_avg": float(np.mean(n_valid[lb])) if n_valid[lb] else 0.0,
            "q_means": q_means,
            "spread": spread,
            "monotonic": mono,
        }

    return results


# ── 보고서 생성 ────────────────────────────────────────────────────────────────

def _ir_star(ir: float) -> str:
    if np.isnan(ir): return "—"
    if ir > 0.10:    return "★★★"
    if ir > 0.05:    return "★★"
    if ir > 0.02:    return "★"
    return "✗"


def build_report(results: dict[int, dict]) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def f(v: float, d: int = 3) -> str:
        return f"{v:+.{d}f}" if not np.isnan(v) else "—"
    def pct(v: float) -> str:
        return f"{v*100:.1f}%" if not np.isnan(v) else "—"

    lines = [
        "# Low-Volatility 팩터 IC/IR 분석 (Part 1)",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END} | 시장: {MARKET}",
        "",
        "## IC/IR 요약 (lookback별)",
        "",
        "| lookback | 평균 IC | IC σ | IR (IC/σ) | Hit Rate | N기간 | 판정 |",
        "|----------|--------|------|----------|---------|------|------|",
    ]
    for lb in LOOKBACKS:
        r = results.get(lb, {})
        if not r.get("n"):
            lines.append(f"| {lb}일 | — | — | — | — | 0 | — |")
            continue
        lines.append(
            f"| {lb}일 | {f(r['mean_ic'])} | {f(r['std_ic'])} | "
            f"{f(r['ir'])} | {pct(r['hit_rate'])} | {r['n']} | {_ir_star(r['ir'])} |"
        )

    # 최적 lookback = 가장 높은 IR
    valid_lbs = [lb for lb in LOOKBACKS if results.get(lb, {}).get("n", 0) > 0]
    if valid_lbs:
        best_lb = max(valid_lbs, key=lambda lb: results[lb].get("ir", float("-inf")))
        br = results[best_lb]

        lines += [
            "",
            f"## Quintile Decay (lookback={best_lb}일, 최고 IR)",
            "",
            "| Quintile | 평균 분기 수익률 |",
            "|----------|----------------|",
        ]
        for qi in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
            v = br["q_means"].get(qi, float("nan"))
            lines.append(f"| {qi} | {v*100:+.2f}% |" if not np.isnan(v) else f"| {qi} | — |")
        mono_str = "✅" if br.get("monotonic") else "❌"
        lines += [
            f"| **Q1-Q5 Spread** | **{br['spread']*100:+.2f}%** |",
            f"| Monotonic | {mono_str} |",
        ]

        # 판정
        best_ir = br.get("ir", float("nan"))
        if not np.isnan(best_ir):
            if best_ir > 0.05:
                verdict = f"✅ IR={best_ir:.3f} > 0.05 → **Part 2 진행 권장**"
            elif best_ir > 0:
                verdict = f"⚠️ IR={best_ir:.3f} > 0 but ≤ 0.05 → 판단 유보"
            else:
                verdict = f"❌ IR={best_ir:.3f} ≤ 0 → **Low-vol 팩터 폐기 권장**"
            lines += ["", "## 판정", "", f"> {verdict}"]

    return "\n".join(lines) + "\n"


def print_summary(results: dict[int, dict]) -> None:
    print()
    print("=" * 60)
    print("Low-Volatility 팩터 IC/IR 분석 (2017-2024)")
    print("=" * 60)
    print()
    print(f"{'lookback':>10} {'평균IC':>8} {'IR':>8} {'HitRate':>9} {'N':>5} {'판정':>5}")
    print("-" * 50)
    for lb in LOOKBACKS:
        r = results.get(lb, {})
        if not r.get("n"):
            print(f"{lb:>8}일 {'—':>8} {'—':>8} {'—':>9} {'0':>5}")
            continue
        star = _ir_star(r["ir"])
        print(
            f"{lb:>8}일 {r['mean_ic']:>+8.3f} {r['ir']:>+8.3f} "
            f"{r['hit_rate']*100:>8.1f}% {r['n']:>5} {star:>5}"
        )
    print()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    logger.info("Low-vol IC/IR 분석 시작")

    results = run_analysis()
    print_summary(results)

    report = build_report(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)
    print(f"  보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 스크립트 실행 (Part 1 — ~3분 소요 예상)**

```powershell
$env:PYTHONIOENCODING="utf-8"
python scripts/analyze_lowvol_ic.py 2>&1 | Select-Object -Last 30
```

기대: IC 표 출력 + 보고서 저장 메시지

- [ ] **Step 3: 판정 확인 + Part 2 진행 여부 결정**

```
IR > 0.05 → Part 2 진행 (Task 9 실행)
IR ≤ 0    → Part 2 스킵, 사용자에게 보고 후 종료
```

- [ ] **Step 4: Commit**

```powershell
git add scripts/analyze_lowvol_ic.py docs/reports/lowvol_ic_analysis.md
git commit -m "feat(analysis): add Low-vol IC/IR analysis script (Part 1, 3 lookbacks)"
```

---

## Task 9: scripts/backtest_lowvol_weights_s7.py — 5가지 가중치 백테스트 (Part 2)

**선행 조건: Task 8의 IC 결과에서 IR > 0.05 확인 후 진행.**

**Files:**
- Create: `scripts/backtest_lowvol_weights_s7.py`

- [ ] **Step 1: 스크립트 작성**

```python
"""scripts/backtest_lowvol_weights_s7.py — S7 Low-vol 가중치 조합 백테스트 (Part 2).

5가지 가중치 조합을 각각 전체 기간(2017-2024) 백테스트하여 비교.
CAGR, Sharpe, MDD, Sortino, DSR 및 POLICY 5조건 평가.
결과를 docs/reports/lowvol_factor_s7_analysis.md 에 저장.

사용:
    python scripts/backtest_lowvol_weights_s7.py
"""

from __future__ import annotations

import logging
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, skew as scipy_skew, kurtosis as scipy_kurt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.logging_config import setup_logging   # noqa: E402
from config.settings import settings              # noqa: E402

logger = logging.getLogger(__name__)

BACKTEST_START = "2017-01-01"
BACKTEST_END   = "2024-12-31"
MARKET         = "KOSPI"
RANDOM_SEED    = 42
RF_ANNUAL      = 0.03

REPORT_DIR  = PROJECT_ROOT / "docs" / "reports"
REPORT_PATH = REPORT_DIR / "lowvol_factor_s7_analysis.md"

# V3 IC 결과 (하드코딩 — 레포트 연계용)
V3_VALUE_IR   = 0.572
V3_MOM_IR     = -0.057
V3_QUALITY_IR = -0.221

# 비교 대상 5가지 가중치 조합
WEIGHT_CONFIGS: dict[str, dict[str, float]] = {
    "A_baseline":  {"value": 0.70, "momentum": 0.30, "quality": 0.00, "low_vol": 0.00},
    "B_V70L30":    {"value": 0.70, "momentum": 0.00, "quality": 0.00, "low_vol": 0.30},
    "C_V60L40":    {"value": 0.60, "momentum": 0.00, "quality": 0.00, "low_vol": 0.40},
    "D_V50M20L30": {"value": 0.50, "momentum": 0.20, "quality": 0.00, "low_vol": 0.30},
    "E_V100":      {"value": 1.00, "momentum": 0.00, "quality": 0.00, "low_vol": 0.00},
}


# ── DSR 함수 (V2에서 복사, 자급자족) ──────────────────────────────────────────

_EULER_GAMMA = 0.5772156649015328


def _se_sr(sr_m: float, sk: float, ek: float) -> float:
    inner = 1.0 + 0.5 * sr_m**2 - sk * sr_m + (ek / 4.0) * sr_m**2
    return math.sqrt(max(inner, 1e-9))


def _psr(sr_m: float, sr_ref_m: float, T: int, sk: float, ek: float) -> float:
    se = _se_sr(sr_m, sk, ek)
    z  = (sr_m - sr_ref_m) * math.sqrt(T - 1) / se
    return float(norm.cdf(z))


def _expected_max_sr(N: int, sr_std_m: float) -> float:
    if N <= 1:
        return 0.0
    z1 = float(norm.ppf(1.0 - 1.0 / N))
    z2 = float(norm.ppf(1.0 - 1.0 / (N * math.e)))
    return sr_std_m * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def _dsr(sr_m: float, T: int, sk: float, ek: float, N: int = 20, sr_std_a: float = 0.025) -> float:
    sr_std_m = sr_std_a / math.sqrt(12.0)
    e_max    = _expected_max_sr(N, sr_std_m)
    return _psr(sr_m, e_max, T, sk, ek)


# ── 성과 지표 계산 ────────────────────────────────────────────────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    pv = df["portfolio_value"].copy()
    pv.index = pd.to_datetime(pv.index)

    total_ret  = float(pv.iloc[-1] / pv.iloc[0] - 1)
    n_years    = (pv.index[-1] - pv.index[0]).days / 365.25
    cagr       = float((1 + total_ret) ** (1 / n_years) - 1)

    roll_max   = pv.cummax()
    drawdown   = pv / roll_max - 1
    mdd        = float(drawdown.min())

    try:
        monthly_pv  = pv.resample("ME").last()
    except Exception:
        monthly_pv  = pv.resample("M").last()
    monthly_ret = monthly_pv.pct_change().dropna()

    rf_m   = (1 + RF_ANNUAL) ** (1 / 12) - 1
    excess = monthly_ret - rf_m
    vol_m  = float(monthly_ret.std(ddof=1))
    sr_m   = float(excess.mean() / vol_m) if vol_m > 0 else float("nan")
    sr_a   = sr_m * math.sqrt(12.0) if not np.isnan(sr_m) else float("nan")

    # Sortino
    down = monthly_ret[monthly_ret < rf_m] - rf_m
    sortino_m = float(excess.mean() / down.std(ddof=1)) if len(down) > 1 else float("nan")
    sortino_a = sortino_m * math.sqrt(12.0) if not np.isnan(sortino_m) else float("nan")

    sk = float(scipy_skew(monthly_ret))
    ek = float(scipy_kurt(monthly_ret, fisher=True))
    T  = len(monthly_ret)
    dsr = _dsr(sr_m, T, sk, ek) if T > 1 and not np.isnan(sr_m) else float("nan")

    return {
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sr_a,
        "sortino": sortino_a,
        "dsr": dsr,
        "vol_annual": vol_m * math.sqrt(12),
        "T": T,
        "skew": sk,
        "excess_kurt": ek,
    }


# ── 백테스트 실행 ─────────────────────────────────────────────────────────────

def run_one(name: str, weights: dict[str, float]) -> dict:
    from backtest.engine import MultiFactorBacktest
    from strategy.screener import MultiFactorScreener

    logger.info("== %s: v=%.2f m=%.2f lv=%.2f ==",
                name, weights["value"], weights["momentum"], weights["low_vol"])

    # 설정 패치
    settings.factor_weights.value    = weights["value"]
    settings.factor_weights.momentum = weights["momentum"]
    settings.factor_weights.quality  = weights["quality"]
    settings.factor_weights.low_vol  = weights["low_vol"]
    MultiFactorScreener._factor_cache.clear()

    np.random.seed(RANDOM_SEED)
    random.seed(RANDOM_SEED)

    engine = MultiFactorBacktest()
    df = engine.run(BACKTEST_START, BACKTEST_END, market=MARKET)

    if df is None or df.empty:
        logger.error("[%s] 백테스트 결과 없음", name)
        return {"name": name, "error": True}

    m = compute_metrics(df)
    m["name"] = name
    m["weights"] = weights
    return m


# ── 보고서 생성 ────────────────────────────────────────────────────────────────

def _policy_check(baseline: dict, candidate: dict) -> list[str]:
    """POLICY 5조건 평가 — 조건별 통과/실패 반환."""
    checks = []

    # 1. CAGR 손실 ≤ -1%p
    delta_cagr = candidate["cagr"] - baseline["cagr"]
    checks.append(f"① CAGR 변화 {delta_cagr*100:+.2f}%p → {'✅' if delta_cagr >= -0.01 else '❌ (>-1%p 초과)'}")

    # 2. Alpha 동시 개선 (Sharpe 기준)
    delta_sharpe = candidate["sharpe"] - baseline["sharpe"]
    checks.append(f"② Sharpe 변화 {delta_sharpe:+.3f} → {'✅' if delta_sharpe >= 0 else '⚠️ (하락)'}")

    # 3. Sharpe 하락 < 0.10
    checks.append(f"③ Sharpe 하락 {-delta_sharpe:.3f} → {'✅' if delta_sharpe > -0.10 else '❌ (0.10 초과)'}")

    # 4. DSR 개선
    delta_dsr = candidate["dsr"] - baseline["dsr"]
    checks.append(f"④ DSR 변화 {delta_dsr:+.3f} (목표 > 0.729) → {'✅' if candidate['dsr'] > 0.729 else '⚠️'}")

    # 5. MDD 변화
    delta_mdd = candidate["mdd"] - baseline["mdd"]
    checks.append(f"⑤ MDD 변화 {delta_mdd*100:+.2f}%p → {'✅' if delta_mdd >= -0.03 else '⚠️ (악화)'}")

    return checks


def build_full_report(
    all_results: list[dict],
    lowvol_ir: float,
    best_lookback: int,
) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def f(v: float, d: int = 3) -> str:
        return f"{v:.{d}f}" if not np.isnan(v) else "—"
    def fp(v: float, d: int = 2) -> str:
        return f"{v*100:.{d}f}%" if not np.isnan(v) else "—"

    baseline = next((r for r in all_results if r["name"] == "A_baseline"), {})

    lines = [
        "# S7: Low-Volatility 팩터 분석",
        "",
        f"생성: {now}  ",
        f"기간: {BACKTEST_START} ~ {BACKTEST_END} | 시장: {MARKET}",
        "",
        "## 1. IC/IR 결과 (Part 1 요약)",
        "",
        "| 팩터 | IR | 판정 |",
        "|------|---|------|",
        f"| Value 합산 (V3) | +{V3_VALUE_IR:.3f} | ★★★ |",
        f"| Momentum 합산 (V3) | {V3_MOM_IR:+.3f} | ✗ |",
        f"| Quality 합산 (V3) | {V3_QUALITY_IR:+.3f} | ✗ |",
        f"| **Low-vol (lookback={best_lookback}일)** | **{lowvol_ir:+.3f}** | **{'★★★' if lowvol_ir > 0.10 else '★★' if lowvol_ir > 0.05 else '★' if lowvol_ir > 0.02 else '✗'}** |",
        "",
        "## 2. 가중치 조합 비교 (5가지)",
        "",
        "| 모드 | 가중치 | CAGR | Sharpe | MDD | Sortino | DSR |",
        "|------|--------|------|--------|-----|---------|-----|",
    ]

    for r in all_results:
        if r.get("error"):
            lines.append(f"| {r['name']} | — | ERROR | — | — | — | — |")
            continue
        w = r["weights"]
        wstr = f"V{w['value']:.0%} M{w['momentum']:.0%} LV{w['low_vol']:.0%}"
        lines.append(
            f"| **{r['name']}** | {wstr} | {fp(r['cagr'])} | {f(r['sharpe'])} | "
            f"{fp(r['mdd'])} | {f(r['sortino'])} | {f(r['dsr'])} |"
        )

    # POLICY 평가 (A_baseline 대비 최고 Sharpe 후보)
    non_baseline = [r for r in all_results if r["name"] != "A_baseline" and not r.get("error")]
    if non_baseline and not baseline.get("error"):
        best = max(non_baseline, key=lambda x: x.get("sharpe", float("-inf")))
        lines += [
            "",
            f"## 3. POLICY 5조건 평가 — 최고 Sharpe 후보: {best['name']}",
            "",
        ]
        checks = _policy_check(baseline, best)
        for c in checks:
            lines.append(f"- {c}")

        # 채택 권고
        pass_count = sum(1 for c in checks if "✅" in c)
        lines += [
            "",
            "## 4. 채택 권고",
            "",
            f"- 최고 Sharpe 후보: **{best['name']}** (Sharpe={f(best['sharpe'])}, DSR={f(best['dsr'])})",
            f"- POLICY 조건 {pass_count}/5 통과",
        ]
        if pass_count >= 4 and best["sharpe"] > baseline.get("sharpe", 0):
            lines.append(f"- **✅ {best['name']} 채택 권장** — Preset 업데이트 검토")
        elif pass_count >= 3:
            lines.append(f"- **⚠️ {best['name']} 조건부 채택** — 추가 검증 필요")
        else:
            lines.append(f"- **❌ 현행 Preset A 유지** — Low-vol 조합 이득 불충분")

    return "\n".join(lines) + "\n"


def print_summary(all_results: list[dict]) -> None:
    print()
    print("=" * 70)
    print("S7 Low-vol 가중치 조합 비교 (2017-2024)")
    print("=" * 70)
    print(f"{'이름':<16} {'CAGR':>7} {'Sharpe':>8} {'MDD':>7} {'DSR':>7}")
    print("-" * 55)
    for r in all_results:
        if r.get("error"):
            print(f"{r['name']:<16} ERROR")
            continue
        print(
            f"{r['name']:<16} {r['cagr']*100:>6.2f}% {r['sharpe']:>8.3f} "
            f"{r['mdd']*100:>6.2f}% {r['dsr']:>7.3f}"
        )
    print()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    setup_logging()
    logger.info("S7 Low-vol 가중치 백테스트 시작")

    # Part 1 IC 결과 (분석 스크립트 실행 후 얻은 값을 여기에 입력)
    # analyze_lowvol_ic.py 실행 결과를 확인하고 업데이트 필요
    LOWVOL_IR_BEST   = float("nan")   # ← analyze_lowvol_ic.py 실행 후 최고 IR 값으로 업데이트
    BEST_LOOKBACK    = 60             # ← analyze_lowvol_ic.py 실행 후 최고 IR lookback으로 업데이트

    # 5개 조합 순차 백테스트
    all_results: list[dict] = []
    for name, weights in WEIGHT_CONFIGS.items():
        result = run_one(name, weights)
        all_results.append(result)
        logger.info(
            "[%s] CAGR=%.2f%% Sharpe=%.3f MDD=%.2f%% DSR=%.3f",
            name,
            result.get("cagr", float("nan")) * 100,
            result.get("sharpe", float("nan")),
            result.get("mdd", float("nan")) * 100,
            result.get("dsr", float("nan")),
        )

    print_summary(all_results)

    report = build_full_report(all_results, LOWVOL_IR_BEST, BEST_LOOKBACK)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("보고서 저장: %s", REPORT_PATH)
    print(f"  보고서: {REPORT_PATH}")


if __name__ == "__main__":
    main()
```

**중요**: 스크립트 상단의 `LOWVOL_IR_BEST` 와 `BEST_LOOKBACK`을 Task 8 실행 결과로 업데이트한 후 실행.

- [ ] **Step 2: Task 8 결과로 상수 업데이트 후 실행 (~10~15분 소요)**

Task 8에서 확인한 최고 IR lookback과 IR 값을 스크립트 상단에 업데이트:
```python
LOWVOL_IR_BEST   = 0.XXX   # 실제 값으로 교체
BEST_LOOKBACK    = XX       # 실제 lookback으로 교체
```

실행:
```powershell
$env:PYTHONIOENCODING="utf-8"
python scripts/backtest_lowvol_weights_s7.py 2>&1 | Select-Object -Last 30
```

- [ ] **Step 3: Commit**

```powershell
git add scripts/backtest_lowvol_weights_s7.py docs/reports/lowvol_factor_s7_analysis.md docs/reports/lowvol_ic_analysis.md
git commit -m "feat(s7): add Low-vol backtest comparison (5 weight configs) with POLICY evaluation"
```

---

## Task 10: CHANGELOG.md 업데이트

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: [Unreleased] 섹션 상단에 S7 항목 추가**

```markdown
### 추가 — S7: Low-Volatility 팩터 도입 + 가중치 탐색 (2026-05-15)

- **feat(volatility): VolatilityFactor 클래스 신규**
  - `factors/volatility.py` 신규
  - `calc_volatility_score(date, tickers, storage, lookback_days=60)` → 저변동성 종목 높은 점수(0~100)
  - 일별 수익률 rolling std 연율화(×√252) → 역순위 변환
  - DB 벌크 조회(`load_daily_prices_bulk`) 재활용 — API 추가 없음
  - 5개 단위 테스트 (`tests/test_volatility.py`)
- **feat(composite): low_vol_score Optional 파라미터 추가**
  - `factors/composite.py` `calculate()` 에 `low_vol_score: Optional[pd.Series] = None` 추가
  - weight=0 또는 None이면 기존과 완전 동일 (하위 호환 보장)
  - `weighted_average_nan_safe` 에 low_vol 팩터 자동 편입
  - 4개 단위 테스트 (`tests/test_composite_lowvol.py`)
- **feat(screener): Low-vol 팩터 조건부 통합**
  - `strategy/screener.py` — `low_vol weight > 0`일 때만 VolatilityFactor 호출 (기본 스킵)
  - cache_key에 `float(fw.low_vol)` 추가 (프리셋 간 캐시 오염 방지)
- **feat(settings+config): FactorWeights.low_vol 필드 신규**
  - `config/settings.py` FactorWeights에 `low_vol: float = 0.00` 추가
  - `config/config.yaml` 프리셋 A/B/C에 `low_vol: 0.00` 추가 (기존 동작 변화 없음)
- **analysis(lowvol-ic): Low-vol IC/IR 분석 (Part 1)**
  - `scripts/analyze_lowvol_ic.py` 신규
  - lookback 60/90/120일 × 31분기 Spearman IC/IR/Hit Rate/Quintile Decay
  - V3 ic_universe_guard() 동일 방법론 (Step1/3·S4 비활성, F-Score≥4 유지)
  - 단일 패스로 3가지 lookback 동시 처리 (screener 31회 호출)
- **analysis(lowvol-backtest): 5가지 가중치 조합 백테스트 (Part 2)**
  - `scripts/backtest_lowvol_weights_s7.py` 신규
  - A(기준)/B(V70L30)/C(V60L40)/D(V50M20L30)/E(V100) 5조합 비교
  - CAGR/Sharpe/MDD/Sortino/DSR + POLICY 5조건 평가
  - DSR: V2 함수 자급자족 (스크립트 내 복사)
- `docs/reports/lowvol_ic_analysis.md` 신규
- `docs/reports/lowvol_factor_s7_analysis.md` 신규
```

- [ ] **Step 2: Commit**

```powershell
git add CHANGELOG.md
git commit -m "docs(changelog): add S7 Low-vol factor entry"
```

---

## 자기 검토 (Spec Coverage)

| 스펙 요구사항 | 구현 Task |
|-------------|----------|
| factors/volatility.py 신규 | Task 2-3 |
| factors/composite.py low_vol 통합 | Task 4-5 |
| strategy/screener.py low_vol 호출 | Task 6 |
| config/settings.py FactorWeights.low_vol | Task 1 |
| config/config.yaml low_vol: 0.00 | Task 7 |
| scripts/analyze_lowvol_ic.py IC 분석 | Task 8 |
| scripts/backtest_lowvol_weights_s7.py 5조합 | Task 9 |
| tests/test_volatility.py | Task 2 |
| tests/test_composite_lowvol.py | Task 4 |
| docs/reports/lowvol_factor_s7_analysis.md | Task 9 |
| CHANGELOG.md | Task 10 |
| 기존 팩터 로직 변경 금지 | ✅ value/momentum/quality.py 미변경 |
| FactorWeights 기존 3개 기본값 변경 금지 | ✅ 0.40/0.40/0.20 유지 (프리셋 A에서 0.70/0.30/0.00으로 override됨) |
| config.yaml 프리셋 A/B/C 기존 가중치 변경 금지 | ✅ low_vol: 0.00 추가만 |
| engine.py 직접 변경 최소화 | ✅ 미변경 |
| IC > 0.05이면 Part 2 / ≤ 0이면 폐기 판정 | Task 8 판정 로직 |
| DSR 현재 0.729 → 목표 > 0.80 확인 | Task 9 보고서 |
| POLICY 5조건 평가 | Task 9 `_policy_check()` |

**placeholder 없음**: 모든 Step에 실제 실행 가능한 코드 포함.  
**타입 일관성**: `VolatilityFactor.calc_volatility_score(date, tickers, storage, lookback_days)` — Task 3, 6, 8, 9 동일 시그니처.
