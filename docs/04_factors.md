# 04. 멀티팩터 전략 구현

## 4-1. 팩터 설계 원칙

### 공통 스코어링 방식
- 모든 팩터는 **순위(rank) 기반 0~100 정규화** 사용
- 단순 min-max보다 순위 기반이 이상치에 강건함
- 이상치는 **Winsorize** (상하위 1% 클리핑) 후 스코어 계산
- 팩터 데이터가 없는 종목은 해당 팩터에서만 제외, 다른 팩터는 유지

### 팩터별 방향
| 팩터 | 지표 | 방향 | 전처리 |
|------|------|------|--------|
| 밸류 | PBR | 낮을수록 고득점 → 역수 변환 후 순위 | 0 이하 제거, 상위 1% 클리핑 |
| 밸류 | PER | 낮을수록 고득점 → 역수 변환 후 순위 | 0 이하(적자) 제거, 상위 1% 클리핑 |
| 밸류 | DIV | 높을수록 고득점 → 그대로 순위 | 음수 제거 |
| 모멘텀 | 12M 수익률 | 높을수록 고득점 → 그대로 순위 | 상하위 1% Winsorize |
| 퀄리티 | ROE (40%) | 높을수록 고득점 → 그대로 순위 | BPS ≤ 0 제거, -50%~+100% 클리핑 |
| 퀄리티 | Earnings Yield (30%) | 1/PER, 높을수록 고득점 | PER > 0만 대상, 상위 1% 클리핑 |
| 퀄리티 | 배당 지급 (30%) | 배당수익률 순위, 미지급 포함(0점) | 음수 제거, 상위 1% 클리핑 |
| 퀄리티 | 부채비율 역수 (선택, 20%) | 낮을수록 좋음 → 역수 변환 후 순위 | 외부 데이터 있을 때만 |

---

## 4-2. factors/value.py

```python
# factors/value.py
import pandas as pd
import numpy as np
import logging
from config.settings import settings

logger = logging.getLogger(__name__)


class ValueFactor:
    """
    밸류 팩터 계산
    - PBR (Price-to-Book Ratio): 주가순자산비율, 낮을수록 저평가
    - PER (Price-to-Earnings Ratio): 주가수익비율, 낮을수록 저평가 (적자 제외)
    - DIV (Dividend Yield): 배당수익률, 높을수록 선호
    """

    def __init__(self):
        self.w = settings.value_weights  # ValueWeights (pbr=0.5, per=0.3, div=0.2)

    def calculate(self, fundamentals: pd.DataFrame) -> pd.Series:
        """
        복합 밸류 스코어 계산 (메인 진입점)

        Args:
            fundamentals: DataFrame (index=ticker, columns 중 PBR·PER·DIV 포함)

        Returns:
            Series (index=ticker, values=value_score 0~100, name='value_score')
        """
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # PBR 스코어 (낮을수록 고득점 → 역수 변환)
        if "PBR" in fundamentals.columns:
            pbr = fundamentals["PBR"].copy()
            pbr = pbr[pbr > 0]                              # 0 이하 제거
            pbr = pbr.clip(upper=pbr.quantile(0.99))        # 상위 1% 클리핑
            score_parts["PBR"] = (self._rank_score(1 / pbr), self.w.pbr)

        # PER 스코어 (낮을수록 고득점, 적자 기업 제외)
        if "PER" in fundamentals.columns:
            per = fundamentals["PER"].copy()
            per = per[per > 0]                              # 적자(음수) 및 0 제거
            per = per.clip(upper=per.quantile(0.99))
            score_parts["PER"] = (self._rank_score(1 / per), self.w.per)

        # DIV 스코어 (높을수록 고득점)
        if "DIV" in fundamentals.columns:
            div = fundamentals["DIV"].copy()
            div = div[div >= 0]                             # 음수 제거
            score_parts["DIV"] = (self._rank_score(div), self.w.div)

        if not score_parts:
            logger.warning("밸류 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="value_score")

        # 가중 평균 (공통 종목만)
        all_scores = [s for s, _ in score_parts.values()]
        common_idx = all_scores[0].index
        for s in all_scores[1:]:
            common_idx = common_idx.intersection(s.index)

        composite = pd.Series(0.0, index=common_idx)
        total_w = 0.0
        for name, (score, weight) in score_parts.items():
            composite += score.reindex(common_idx).fillna(0) * weight
            total_w += weight

        if total_w > 0:
            composite /= total_w

        composite.name = "value_score"
        logger.info(f"밸류 스코어 계산 완료: {len(composite)}개 종목")
        return composite.sort_values(ascending=False)

    @staticmethod
    def _rank_score(series: pd.Series) -> pd.Series:
        """순위 기반 0~100 정규화 (이상치에 강건)"""
        return series.rank(pct=True, na_option="keep") * 100
```

---

## 4-3. factors/momentum.py

```python
# factors/momentum.py
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class MomentumFactor:
    """
    모멘텀 팩터 계산
    표준: 12개월 수익률 (최근 1개월 제외)
    → 이유: 단기 반전(Short-term Reversal) 효과 제거
    → 계산: t-1개월 가격 / t-12개월 가격 - 1
             (ReturnCalculator.get_returns_for_universe에서 이미 계산된 값을 입력)
    """

    def calculate(
        self,
        returns_12m: pd.Series,
        returns_6m: pd.Series | None = None,
        returns_3m: pd.Series | None = None,
    ) -> pd.Series:
        """
        복합 모멘텀 스코어 계산 (메인 진입점)

        Args:
            returns_12m: 12개월 수익률 (index=ticker, 최근 1개월 제외된 값)
            returns_6m:  6개월 수익률 (선택)
            returns_3m:  3개월 수익률 (선택)

        Returns:
            Series (index=ticker, values=momentum_score 0~100)
        """
        # 기본: 12개월 단독
        score_12m = self._single_score(returns_12m)

        if returns_6m is None and returns_3m is None:
            score_12m.name = "momentum_score"
            return score_12m

        # 복합: 12M 60% + 6M 30% + 3M 10%
        score_6m = self._single_score(returns_6m) if returns_6m is not None else None
        score_3m = self._single_score(returns_3m) if returns_3m is not None else None

        result = score_12m * 0.60

        if score_6m is not None:
            result = result.add(
                score_6m.reindex(score_12m.index).fillna(50) * 0.30, fill_value=0
            )
        if score_3m is not None:
            result = result.add(
                score_3m.reindex(score_12m.index).fillna(50) * 0.10, fill_value=0
            )

        result.name = "momentum_score"
        logger.info(f"모멘텀 스코어 계산 완료: {len(result)}개 종목")
        return result

    @staticmethod
    def _single_score(returns: pd.Series) -> pd.Series:
        """단일 기간 수익률 → 0~100 순위 스코어 (Winsorize 포함)"""
        clean = returns.dropna()
        if clean.empty:
            return pd.Series(dtype=float)
        # 극단값 클리핑
        lower = clean.quantile(0.01)
        upper = clean.quantile(0.99)
        clipped = clean.clip(lower, upper)
        return clipped.rank(pct=True) * 100
```

---

## 4-4. factors/quality.py

```python
# factors/quality.py
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class QualityFactor:
    """
    퀄리티 팩터 계산 (4개 지표, NaN-aware 가중합)
    - ROE = EPS / BPS × 100 (40%, 수익성)
    - Earnings Yield = 1 / PER (30%, 이익수익률)
    - 배당 지급 여부 (30%, 기업 질 신호, 미지급=0점 포함)
    - 부채비율 역수 (선택 20%, 외부 데이터 필요 시 가중치 재분배)
    """

    def calculate(
        self,
        fundamentals: pd.DataFrame,
        debt_ratio: pd.Series | None = None,
    ) -> pd.Series:
        """
        복합 퀄리티 스코어 계산 (메인 진입점)

        Args:
            fundamentals: DataFrame (index=ticker, EPS·BPS·PER·DIV 컬럼 필요)
            debt_ratio:   부채비율 Series (선택, index=ticker)

        Returns:
            Series (index=ticker, values=quality_score 0~100)
        """
        score_parts: dict[str, tuple[pd.Series, float]] = {}

        # ROE 스코어 (40% 가중)
        roe_score = self._calc_roe_score(fundamentals)
        if not roe_score.empty:
            score_parts["roe"] = (roe_score, 0.40)

        # Earnings Yield 스코어 (30% 가중)
        ey_score = self._calc_earnings_yield_score(fundamentals)
        if not ey_score.empty:
            score_parts["earnings_yield"] = (ey_score, 0.30)

        # 배당 지급 스코어 (30% 가중)
        div_score = self._calc_dividend_score(fundamentals)
        if not div_score.empty:
            score_parts["dividend"] = (div_score, 0.30)

        # 부채비율 역수 스코어 (선택, 20% 가중, 데이터 있을 때만)
        if debt_ratio is not None and not debt_ratio.empty:
            d = debt_ratio[debt_ratio >= 0]
            d = d.clip(upper=d.quantile(0.99))
            debt_score = (1 / (d + 1)).rank(pct=True) * 100
            score_parts["debt"] = (debt_score, 0.20)

        if not score_parts:
            logger.warning("퀄리티 팩터: 유효한 지표 없음")
            return pd.Series(dtype=float, name="quality_score")

        # NaN-aware 가중 합산 (종목별 가용 가중치 정규화)
        all_tickers = sorted(set().union(*(s.index for s, _ in score_parts.values())))
        df = pd.DataFrame(index=all_tickers)
        weights: dict[str, float] = {}
        for name, (score, weight) in score_parts.items():
            df[name] = score.reindex(all_tickers)
            weights[name] = weight

        weighted_sum = pd.Series(0.0, index=all_tickers)
        weight_sum = pd.Series(0.0, index=all_tickers)
        for col, w in weights.items():
            mask = df[col].notna()
            weighted_sum[mask] += df.loc[mask, col] * w
            weight_sum[mask] += w

        valid_mask = weight_sum > 0
        result = pd.Series(dtype=float, index=all_tickers)
        result[valid_mask] = weighted_sum[valid_mask] / weight_sum[valid_mask]
        result = result.dropna()
        result.name = "quality_score"
        logger.info(f"퀄리티 스코어 계산 완료: {len(result)}개 종목")
        return result

    @staticmethod
    def _calc_roe_score(fundamentals: pd.DataFrame) -> pd.Series:
        """ROE = EPS / BPS × 100 → 순위 스코어 (BPS ≤ 0 제외, -50%~+100% 클리핑)"""
        if "EPS" not in fundamentals.columns or "BPS" not in fundamentals.columns:
            return pd.Series(dtype=float)
        eps, bps = fundamentals["EPS"], fundamentals["BPS"]
        valid = bps[bps > 0].index
        roe = (eps[valid] / bps[valid]) * 100
        roe = roe.clip(lower=-50, upper=100)
        return roe.rank(pct=True) * 100

    @staticmethod
    def _calc_earnings_yield_score(fundamentals: pd.DataFrame) -> pd.Series:
        """Earnings Yield (1/PER) → 순위 스코어 (PER > 0인 종목만)"""
        if "PER" not in fundamentals.columns:
            return pd.Series(dtype=float)
        per = fundamentals["PER"]
        valid = per[per > 0]
        if valid.empty:
            return pd.Series(dtype=float)
        ey = 1 / valid
        ey = ey.clip(upper=ey.quantile(0.99))
        return ey.rank(pct=True) * 100

    @staticmethod
    def _calc_dividend_score(fundamentals: pd.DataFrame) -> pd.Series:
        """배당수익률 → 순위 스코어 (미지급=0점 포함)"""
        if "DIV" not in fundamentals.columns:
            return pd.Series(dtype=float)
        div = fundamentals["DIV"].fillna(0)
        valid = div[div >= 0]
        if valid.empty:
            return pd.Series(dtype=float)
        valid = valid.clip(upper=valid.quantile(0.99))
        return valid.rank(pct=True) * 100
```

---

## 4-5. factors/composite.py

```python
# factors/composite.py
import pandas as pd
import numpy as np
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

# 금융주 섹터명 (WICS 기준)
FINANCE_SECTORS = {"은행", "증권", "보험", "기타금융", "다각화된금융"}


class MultiFactorComposite:
    """
    멀티팩터 스코어 합산 및 최종 종목 선정
    밸류 40% + 모멘텀 40% + 퀄리티 20%
    """

    def __init__(self):
        self.w = settings.factor_weights
        logger.info(
            f"팩터 가중치 — 밸류:{self.w.value}, 모멘텀:{self.w.momentum}, 퀄리티:{self.w.quality}"
        )

    def calculate(
        self,
        value_score: pd.Series,
        momentum_score: pd.Series,
        quality_score: pd.Series,
    ) -> pd.DataFrame:
        """
        3개 팩터 가중 합산

        Args:
            value_score, momentum_score, quality_score: 각각 0~100 스코어 Series

        Returns:
            DataFrame(index=ticker, columns=[value_score, momentum_score, quality_score, composite_score])
            composite_score 내림차순 정렬
        """
        # 세 팩터 공통 종목만 사용
        common = (
            set(value_score.index)
            & set(momentum_score.index)
            & set(quality_score.index)
        )
        logger.info(f"팩터 공통 종목: {len(common)}개")

        df = pd.DataFrame(
            {
                "value_score": value_score.reindex(common),
                "momentum_score": momentum_score.reindex(common),
                "quality_score": quality_score.reindex(common),
            }
        )
        df["composite_score"] = (
            df["value_score"] * self.w.value
            + df["momentum_score"] * self.w.momentum
            + df["quality_score"] * self.w.quality
        )
        return df.sort_values("composite_score", ascending=False)

    def apply_universe_filter(
        self,
        composite_df: pd.DataFrame,
        market_cap: pd.Series,
        finance_tickers: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        유니버스 필터 적용

        Args:
            market_cap:      시가총액 Series (index=ticker)
            finance_tickers: 금융주 종목 코드 리스트 (pykrx 섹터 조회 후 전달)

        Returns:
            필터 적용된 DataFrame
        """
        result = composite_df.copy()

        # 시가총액 하위 10% 제외
        if not market_cap.empty:
            threshold = market_cap.quantile(settings.universe.min_market_cap_percentile / 100)
            valid = market_cap[market_cap >= threshold].index
            before = len(result)
            result = result[result.index.isin(valid)]
            logger.info(f"시가총액 필터: {before} → {len(result)}개")

        # 금융주 제외
        if finance_tickers:
            before = len(result)
            result = result[~result.index.isin(finance_tickers)]
            logger.info(f"금융주 제외: {before} → {len(result)}개")

        return result

    def select_top(
        self,
        composite_df: pd.DataFrame,
        n: int | None = None,
    ) -> pd.DataFrame:
        """상위 N개 종목 선정 (동일 비중)"""
        n = n or settings.portfolio.n_stocks
        selected = composite_df.head(n).copy()
        selected["weight"] = 1.0 / len(selected)

        logger.info(
            f"포트폴리오 구성 완료: {len(selected)}개 종목 | "
            f"평균 복합스코어: {selected['composite_score'].mean():.1f}"
        )
        return selected
```

---

## 4-6. 팩터 검증 (Jupyter)

```python
# notebooks/02_factor_analysis.ipynb

from data.collector import KRXDataCollector, ReturnCalculator
from factors.value import ValueFactor
from factors.momentum import MomentumFactor
from factors.quality import QualityFactor
from factors.composite import MultiFactorComposite
import matplotlib.pyplot as plt
import seaborn as sns

DATE = "20240101"
collector = KRXDataCollector()

# 데이터 수집
fundamentals = collector.get_fundamentals_all(DATE)
market_cap = collector.get_market_cap(DATE)
print(f"종목 수: {len(fundamentals)}")
print(f"PBR 분포:\n{fundamentals['PBR'].describe()}")

# 팩터 계산
v = ValueFactor().calculate(fundamentals)
print(f"\n밸류 스코어 상위 5:\n{v.head()}")

tickers = fundamentals.index.tolist()
ret_calc = ReturnCalculator()
returns_12m = ret_calc.get_returns_for_universe(tickers[:50], DATE, 12, 1)  # 50개만 테스트
m = MomentumFactor().calculate(returns_12m)

q = QualityFactor().calculate(fundamentals)

# 복합 스코어
comp = MultiFactorComposite()
composite_df = comp.calculate(v, m, q)
filtered = comp.apply_universe_filter(composite_df, market_cap["market_cap"])
portfolio = comp.select_top(filtered, n=30)
print(f"\n최종 포트폴리오:\n{portfolio[['composite_score','weight']].head(10)}")

# 팩터 간 상관관계
corr = composite_df[["value_score", "momentum_score", "quality_score"]].corr()
sns.heatmap(corr, annot=True, cmap="coolwarm")
plt.title("팩터 간 상관관계")
plt.show()
```

---

## 4-7. 주의사항

| 항목 | 내용 |
|------|------|
| ROE 직접 계산 | pykrx는 ROE 컬럼을 직접 제공하지 않음. `EPS / BPS × 100`으로 계산 필요 |
| 퀄리티 구성 변경 | 실제 구현은 ROE(40%) + EY(30%) + Dividend(30%) + Debt(선택 20%). NaN-aware 가중합 사용 |
| 팩터 독립성 | 밸류와 퀄리티는 약한 역상관. 모멘텀은 독립적. 상관관계 분석 필수 |
| NaN-aware 합산 | 2/3 팩터 이상 유효하면 편입 가능 (min_factor_count=2), 가중치 자동 재분배 |
| 부채비율 | pykrx 미제공 → 외부 데이터 있을 때만 20% 가중으로 추가 (없으면 나머지 지표로 100% 구성) |
| 팩터 분포 시각화 | 백테스트 전에 반드시 각 팩터 스코어 분포와 상관관계를 Jupyter에서 확인 |
