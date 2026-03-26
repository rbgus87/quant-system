"""월간 vs 분기 리밸런싱 비교"""
import os, sys, logging, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("CONFIG_PATH", "config/config.yaml")

from config.logging_config import setup_logging
from config.settings import settings
from backtest.engine import MultiFactorBacktest
from backtest.metrics import PerformanceAnalyzer
from strategy.screener import MultiFactorScreener
from dataclasses import fields as dc_fields

setup_logging()
logger = logging.getLogger(__name__)

START = "2017-01-01"
END = "2024-12-31"

results = {}
for freq in ["monthly", "quarterly"]:
    logger.info(f"\n{'='*60}")
    logger.info(f"리밸런싱 주기: {freq}")
    logger.info(f"{'='*60}")

    settings.portfolio.rebalance_frequency = freq
    MultiFactorScreener._factor_cache.clear()

    engine = MultiFactorBacktest(initial_cash=settings.portfolio.initial_cash)
    result = engine.run(START, END)

    analyzer = PerformanceAnalyzer()
    returns = result["returns"].dropna()
    metrics = analyzer.summary(result["portfolio_value"], returns)
    metrics["freq"] = freq
    results[freq] = metrics

# 비교 테이블
print(f"\n{'='*70}")
print(f"{'월간 vs 분기 리밸런싱 비교 (2017-2024)':^70}")
print(f"{'='*70}")
print(f"{'지표':>18} | {'Monthly':>12} | {'Quarterly':>12} | {'차이':>10}")
print(f"{'-'*70}")
for key, label in [
    ("cagr", "CAGR"),
    ("mdd", "MDD"),
    ("sharpe", "Sharpe"),
    ("sortino", "Sortino"),
    ("calmar", "Calmar"),
    ("volatility", "Volatility"),
    ("total_return", "Total Return"),
]:
    m = results["monthly"].get(key, 0)
    q = results["quarterly"].get(key, 0)
    diff = q - m
    if key in ("cagr", "mdd", "volatility", "total_return"):
        print(f"{label:>18} | {m:>11.2%} | {q:>11.2%} | {diff:>+9.2%}")
    else:
        print(f"{label:>18} | {m:>11.3f} | {q:>11.3f} | {diff:>+9.3f}")
print(f"{'='*70}")

# 원복
settings.portfolio.rebalance_frequency = "quarterly"
