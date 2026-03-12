# backtest/report.py
import matplotlib

matplotlib.use("Agg")

import pandas as pd
import os
import logging
from typing import Optional
import quantstats as qs
import quantstats.reports as _qs_reports

logger = logging.getLogger(__name__)

# quantstats 0.0.81 + pandas 3.x 호환성 패치
# metrics.replace([-0, "-0"], 0) 이 pandas 3.x에서 ValueError 발생
_orig_metrics = _qs_reports.metrics


def _patched_metrics(*args, **kwargs):
    _orig_replace = pd.DataFrame.replace

    def _safe_replace(self, to_replace=None, value=None, **kw):
        try:
            return _orig_replace(self, to_replace, value, **kw)
        except ValueError:
            # pandas 3.x: 컬럼별 순회하여 안전하게 replace
            result = self.copy()
            for col in result.columns:
                try:
                    result[col] = result[col].replace(to_replace, value)
                except (ValueError, TypeError):
                    pass
            return result

    pd.DataFrame.replace = _safe_replace
    try:
        return _orig_metrics(*args, **kwargs)
    finally:
        pd.DataFrame.replace = _orig_replace


_qs_reports.metrics = _patched_metrics


def _fig_to_base64(fig) -> str:
    """matplotlib Figure를 base64 PNG 문자열로 변환."""
    import base64
    from io import BytesIO

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="#1a1a2e")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    buf.close()
    return b64


def _grade_color(value: float, thresholds: list[tuple[float, str]]) -> str:
    """값에 따라 신호등 색상 반환 (thresholds: [(기준, 색상), ...] 내림차순)."""
    for threshold, color in thresholds:
        if value >= threshold:
            return color
    return thresholds[-1][1]


class ReportGenerator:
    """백테스트 성과 HTML 리포트 생성"""

    def generate_html(
        self,
        returns: pd.Series,
        benchmark_returns: Optional[pd.Series] = None,
        output_path: str = "reports/backtest_report.html",
        title: str = "멀티팩터 퀀트 전략",
    ) -> None:
        """quantstats HTML 리포트 생성

        Args:
            returns: 일별 수익률 Series (index=date)
            benchmark_returns: 벤치마크 수익률 Series (선택)
            output_path: 출력 파일 경로
            title: 리포트 제목
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # 벤치마크 날짜 정렬
        benchmark = None
        if benchmark_returns is not None and not benchmark_returns.empty:
            # 공통 날짜로 정렬
            common_dates = returns.index.intersection(benchmark_returns.index)
            if len(common_dates) > 10:
                benchmark = benchmark_returns.reindex(common_dates)
                logger.info(
                    f"벤치마크 날짜 정렬: {len(benchmark_returns)} → {len(common_dates)}개"
                )
            else:
                logger.warning("벤치마크 공통 날짜 부족, 벤치마크 없이 생성")

        try:
            qs.reports.html(
                returns,
                benchmark=benchmark,
                title=title,
                output=output_path,
            )
            logger.info(f"HTML 리포트 저장: {output_path}")
        except Exception as e:
            logger.error(f"HTML 리포트 생성 실패: {e}", exc_info=True)
            raise

    def generate_korean_html(
        self,
        portfolio_values: pd.Series,
        returns: pd.Series,
        metrics: dict,
        output_path: str = "reports/backtest_report_kr.html",
        title: str = "멀티팩터 퀀트 백테스트",
        benchmark_values: Optional[pd.Series] = None,
        turnover_log: Optional[list[dict]] = None,
        factor_ic: Optional[dict[str, float]] = None,
    ) -> None:
        """한글 HTML 백테스트 리포트 생성

        Args:
            portfolio_values: 일별 포트폴리오 가치 Series (index=date)
            returns: 일별 수익률 Series
            metrics: PerformanceAnalyzer.summary() 결과 dict
            output_path: 출력 파일 경로
            title: 리포트 제목
            benchmark_values: 벤치마크 가치 Series (선택)
            turnover_log: 리밸런싱별 턴오버 기록 (선택)
            factor_ic: 팩터별 IC 값 (선택)
        """
        import matplotlib.pyplot as plt
        import numpy as np
        from backtest.metrics import PerformanceAnalyzer

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        analyzer = PerformanceAnalyzer()

        # --- 차트 생성 ---
        plt.rcParams.update({
            "font.family": "Malgun Gothic",
            "axes.unicode_minus": False,
            "figure.facecolor": "#1a1a2e",
            "axes.facecolor": "#16213e",
            "text.color": "#e0e0e0",
            "axes.labelcolor": "#e0e0e0",
            "xtick.color": "#a0a0a0",
            "ytick.color": "#a0a0a0",
        })

        charts: dict[str, str] = {}

        # 1) 누적 수익률 차트
        fig, ax = plt.subplots(figsize=(10, 4))
        cum_ret = (1 + returns).cumprod()
        ax.plot(cum_ret.index, cum_ret.values, color="#00d2ff", linewidth=1.5, label="전략")
        if benchmark_values is not None and len(benchmark_values) > 1:
            bm_norm = benchmark_values / benchmark_values.iloc[0]
            common = cum_ret.index.intersection(bm_norm.index)
            if len(common) > 10:
                ax.plot(common, bm_norm.reindex(common).values,
                        color="#ff6b6b", linewidth=1.2, alpha=0.7, label="KOSPI")
        ax.set_title("누적 수익률", fontsize=14, fontweight="bold", color="white")
        ax.legend(loc="upper left", fontsize=10)
        ax.grid(True, alpha=0.2)
        charts["cumulative"] = _fig_to_base64(fig)
        plt.close(fig)

        # 2) 낙폭 차트
        fig, ax = plt.subplots(figsize=(10, 3))
        rolling_max = portfolio_values.cummax()
        drawdown = (portfolio_values - rolling_max) / rolling_max
        ax.fill_between(drawdown.index, drawdown.values, 0, color="#ff6b6b", alpha=0.5)
        ax.plot(drawdown.index, drawdown.values, color="#ff6b6b", linewidth=0.8)
        ax.set_title("낙폭 (Drawdown)", fontsize=14, fontweight="bold", color="white")
        ax.grid(True, alpha=0.2)
        charts["drawdown"] = _fig_to_base64(fig)
        plt.close(fig)

        # 3) 월별 수익률 히트맵
        monthly_table = analyzer.monthly_returns(portfolio_values)
        if not monthly_table.empty:
            heatmap_data = monthly_table.drop(columns=["연간"], errors="ignore")
            fig, ax = plt.subplots(figsize=(10, max(3, len(heatmap_data) * 0.6)))
            import seaborn as sns
            sns.heatmap(
                heatmap_data * 100,
                annot=True, fmt=".1f", center=0,
                cmap="RdYlGn", linewidths=0.5,
                cbar_kws={"label": "수익률(%)"},
                ax=ax,
            )
            ax.set_title("월별 수익률 (%)", fontsize=14, fontweight="bold", color="white")
            ax.set_ylabel("")
            charts["monthly"] = _fig_to_base64(fig)
            plt.close(fig)

        # 3-b) 연도별 월간 수익률 바 차트 (3개월 이상 데이터가 있는 연도만)
        monthly_pnl_df = analyzer.monthly_pnl(portfolio_values)
        yearly_month_charts: list[str] = []
        if not monthly_pnl_df.empty:
            for year in sorted(monthly_pnl_df["year"].unique()):
                year_data = monthly_pnl_df[monthly_pnl_df["year"] == year]
                if len(year_data) < 3:
                    continue  # 데이터 부족한 연도는 스킵
                fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5), gridspec_kw={"height_ratios": [3, 2]})
                fig.subplots_adjust(hspace=0.4)

                months = year_data["month"].values
                rets = year_data["return_pct"].values * 100
                pnls = year_data["pnl"].values

                month_labels = [f"{m}월" for m in months]
                colors_ret = ["#4ecdc4" if r >= 0 else "#ff6b6b" for r in rets]
                colors_pnl = ["#4ecdc4" if p >= 0 else "#ff6b6b" for p in pnls]

                # 수익률 바 차트
                bars1 = ax1.bar(month_labels, rets, color=colors_ret, width=0.6)
                ax1.set_title(f"{year}년 월별 수익률 (%)", fontsize=13, fontweight="bold", color="white")
                ax1.axhline(y=0, color="#a0a0a0", linewidth=0.5)
                ax1.grid(True, alpha=0.2, axis="y")
                for i, (bar, v) in enumerate(zip(bars1, rets)):
                    offset = 0.3 if v >= 0 else -0.6
                    ax1.text(bar.get_x() + bar.get_width() / 2, v + offset,
                             f"{v:.1f}%", ha="center", va="bottom" if v >= 0 else "top",
                             fontsize=9, color="#e0e0e0")

                # 손익 금액 바 차트
                bars2 = ax2.bar(month_labels, pnls, color=colors_pnl, width=0.6)
                ax2.set_title(f"{year}년 월별 손익 (원)", fontsize=13, fontweight="bold", color="white")
                ax2.axhline(y=0, color="#a0a0a0", linewidth=0.5)
                ax2.grid(True, alpha=0.2, axis="y")
                ax2.yaxis.set_major_formatter(plt.FuncFormatter(
                    lambda x, _: f"{x/10000:,.0f}만" if abs(x) >= 10000 else f"{x:,.0f}"
                ))
                for bar, v in zip(bars2, pnls):
                    label = f"+{v/10000:,.0f}만" if v >= 0 else f"{v/10000:,.0f}만"
                    if abs(v) < 10000:
                        label = f"+{v:,.0f}" if v >= 0 else f"{v:,.0f}"
                    offset = max(abs(pnls.max() if len(pnls) else 0), abs(pnls.min() if len(pnls) else 0)) * 0.05
                    ax2.text(bar.get_x() + bar.get_width() / 2,
                             v + (offset if v >= 0 else -offset),
                             label, ha="center", va="bottom" if v >= 0 else "top",
                             fontsize=8, color="#e0e0e0")

                yearly_month_charts.append(_fig_to_base64(fig))
                plt.close(fig)

        # 4) 연도별 수익률 막대 (2개 이상 연도일 때만 차트 생성)
        yearly = analyzer.yearly_returns(portfolio_values)
        if not yearly.empty and len(yearly) >= 2:
            fig, ax = plt.subplots(figsize=(10, 3))
            colors = ["#4ecdc4" if v >= 0 else "#ff6b6b" for v in yearly.values]
            ax.bar([str(y) for y in yearly.index], yearly.values * 100, color=colors)
            ax.set_title("연도별 수익률 (%)", fontsize=14, fontweight="bold", color="white")
            ax.axhline(y=0, color="#a0a0a0", linewidth=0.5)
            ax.grid(True, alpha=0.2, axis="y")
            for i, v in enumerate(yearly.values):
                ax.text(i, v * 100 + (1 if v >= 0 else -2),
                        f"{v*100:.1f}%", ha="center", fontsize=9, color="#e0e0e0")
            charts["yearly"] = _fig_to_base64(fig)
            plt.close(fig)

        # 5) 롤링 12개월 수익률 차트
        rolling_ret = analyzer.rolling_returns(portfolio_values, window=252)
        if not rolling_ret.empty:
            fig, ax = plt.subplots(figsize=(10, 3))
            ax.plot(rolling_ret.index, rolling_ret.values * 100, color="#4ecdc4", linewidth=1.2)
            ax.axhline(y=0, color="#a0a0a0", linewidth=0.5)
            ax.fill_between(rolling_ret.index, rolling_ret.values * 100, 0,
                            where=rolling_ret.values >= 0, color="#4ecdc4", alpha=0.2)
            ax.fill_between(rolling_ret.index, rolling_ret.values * 100, 0,
                            where=rolling_ret.values < 0, color="#ff6b6b", alpha=0.2)
            ax.set_title("12개월 롤링 수익률 (%)", fontsize=14, fontweight="bold", color="white")
            ax.grid(True, alpha=0.2)
            charts["rolling_ret"] = _fig_to_base64(fig)
            plt.close(fig)

        # 6) 롤링 샤프 비율 차트
        rolling_sh = analyzer.rolling_sharpe(returns, window=252)
        if not rolling_sh.empty:
            fig, ax = plt.subplots(figsize=(10, 3))
            ax.plot(rolling_sh.index, rolling_sh.values, color="#ffd93d", linewidth=1.2)
            ax.axhline(y=0, color="#a0a0a0", linewidth=0.5)
            ax.axhline(y=0.5, color="#4ecdc4", linewidth=0.5, linestyle="--", alpha=0.5)
            ax.fill_between(rolling_sh.index, rolling_sh.values, 0,
                            where=rolling_sh.values >= 0, color="#ffd93d", alpha=0.15)
            ax.fill_between(rolling_sh.index, rolling_sh.values, 0,
                            where=rolling_sh.values < 0, color="#ff6b6b", alpha=0.15)
            ax.set_title("12개월 롤링 샤프 비율", fontsize=14, fontweight="bold", color="white")
            ax.grid(True, alpha=0.2)
            charts["rolling_sharpe"] = _fig_to_base64(fig)
            plt.close(fig)

        # 기간 정보 (카드/차트에서 사용)
        start_dt = portfolio_values.index[0].strftime("%Y-%m-%d")
        end_dt = portfolio_values.index[-1].strftime("%Y-%m-%d")
        start_val = portfolio_values.iloc[0]
        end_val = portfolio_values.iloc[-1]

        # --- 지표 카드 데이터 ---
        cagr = metrics.get("cagr", 0)
        mdd = metrics.get("mdd", 0)
        sharpe = metrics.get("sharpe", 0)
        total_ret = metrics.get("total_return", 0)
        total_pnl = end_val - start_val

        def _fmt_amount(v: float) -> str:
            """금액을 만원 단위로 포맷"""
            sign = "+" if v >= 0 else ""
            if abs(v) >= 100_000_000:
                return f"{sign}{v / 100_000_000:,.1f}억원"
            if abs(v) >= 10_000:
                return f"{sign}{v / 10_000:,.0f}만원"
            return f"{sign}{v:,.0f}원"

        cards = [
            {
                "label": "연 수익률 (CAGR)",
                "value": f"{cagr * 100:.1f}%",
                "color": _grade_color(cagr, [(0.10, "#4ecdc4"), (0.05, "#ffd93d"), (0, "#ff8c42"), (-999, "#ff6b6b")]),
                "desc": "매년 평균 복합 수익률",
            },
            {
                "label": "최대 낙폭 (MDD)",
                "value": f"{mdd * 100:.1f}%",
                "color": _grade_color(mdd, [(-0.15, "#4ecdc4"), (-0.25, "#ffd93d"), (-0.35, "#ff8c42"), (-999, "#ff6b6b")]),
                "desc": f"고점 대비 최대 손실 ({_fmt_amount(end_val * mdd)})",
            },
            {
                "label": "샤프 비율",
                "value": f"{sharpe:.2f}",
                "color": _grade_color(sharpe, [(0.8, "#4ecdc4"), (0.5, "#ffd93d"), (0.3, "#ff8c42"), (-999, "#ff6b6b")]),
                "desc": "위험 대비 수익 효율",
            },
            {
                "label": "총 수익",
                "value": f"{total_ret * 100:.1f}%",
                "color": _grade_color(total_ret, [(0.5, "#4ecdc4"), (0.2, "#ffd93d"), (0, "#ff8c42"), (-999, "#ff6b6b")]),
                "desc": f"순손익 {_fmt_amount(total_pnl)}",
            },
        ]

        # --- 상세 지표 테이블 ---
        detail_rows = [
            ("연 수익률 (CAGR)", f"{cagr * 100:.2f}%", "매년 평균 얼마나 벌었는지"),
            ("총 수익률", f"{total_ret * 100:.2f}%", "원금 대비 전체 수익"),
            ("연환산 변동성", f"{metrics.get('volatility', 0) * 100:.2f}%", "수익률 흔들림 정도 (낮을수록 안정)"),
            ("최대 낙폭 (MDD)", f"{mdd * 100:.2f}%", "고점에서 최대 손실폭"),
            ("MDD 회복 기간", f"{metrics.get('mdd_recovery_days', 0)}거래일", "최대 낙폭 후 원금 회복까지"),
            ("샤프 비율", f"{sharpe:.3f}", "위험 1단위당 초과수익 (0.5 이상 양호)"),
            ("소르티노 비율", f"{metrics.get('sortino', 0):.3f}", "하락 위험 대비 수익 (1.0 이상 양호)"),
            ("칼마 비율", f"{metrics.get('calmar', 0):.3f}", "CAGR / MDD (0.5 이상 양호)"),
            ("일 VaR (95%)", f"{metrics.get('var_95', 0) * 100:.2f}%", "95% 확률로 하루 최대 손실 한도"),
            ("일 승률", f"{metrics.get('win_rate', 0) * 100:.1f}%", "수익이 난 날의 비율"),
            ("투자 기간", f"{metrics.get('n_years', 0):.1f}년", "백테스트 기간"),
        ]

        # 벤치마크 지표 추가
        if "excess_return" in metrics:
            detail_rows.append(
                ("초과수익률", f"{metrics['excess_return'] * 100:.2f}%", "벤치마크(KOSPI) 대비 추가 수익")
            )
        if "information_ratio" in metrics:
            detail_rows.append(
                ("정보 비율 (IR)", f"{metrics['information_ratio']:.3f}", "초과수익의 일관성 (0.5 이상 양호)")
            )
        if "benchmark_cagr" in metrics:
            detail_rows.append(
                ("벤치마크 CAGR", f"{metrics['benchmark_cagr'] * 100:.2f}%", "KOSPI 연 수익률")
            )

        # --- 벤치마크 비교 테이블 ---
        benchmark_compare_html = ""
        if benchmark_values is not None and len(benchmark_values) > 1:
            bm_cagr = metrics.get("benchmark_cagr", 0)
            bm_mdd = metrics.get("benchmark_mdd", 0)
            bm_ret = float(benchmark_values.iloc[-1] / benchmark_values.iloc[0] - 1) if len(benchmark_values) >= 2 else 0
            bm_vol = float(benchmark_values.pct_change().dropna().std() * np.sqrt(252)) if len(benchmark_values) > 2 else 0
            bm_sharpe = analyzer.calculate_sharpe(benchmark_values.pct_change().dropna()) if len(benchmark_values) > 2 else 0

            def _color_cell(val: float, higher_better: bool = True) -> str:
                if higher_better:
                    return "#4ecdc4" if val > 0 else "#ff6b6b"
                return "#4ecdc4" if val < 0 else "#ff6b6b"

            compare_rows = [
                ("총 수익률", f"{total_ret * 100:.2f}%", f"{bm_ret * 100:.2f}%", total_ret - bm_ret, True),
                ("연 수익률 (CAGR)", f"{cagr * 100:.2f}%", f"{bm_cagr * 100:.2f}%", cagr - bm_cagr, True),
                ("최대 낙폭 (MDD)", f"{mdd * 100:.2f}%", f"{bm_mdd * 100:.2f}%", mdd - bm_mdd, False),
                ("연환산 변동성", f"{metrics.get('volatility', 0) * 100:.2f}%", f"{bm_vol * 100:.2f}%",
                 metrics.get("volatility", 0) - bm_vol, False),
                ("샤프 비율", f"{sharpe:.3f}", f"{bm_sharpe:.3f}", sharpe - bm_sharpe, True),
            ]

            compare_html = ""
            for name, strat_v, bm_v, diff, higher in compare_rows:
                diff_color = _color_cell(diff, higher)
                diff_sign = "+" if diff > 0 else ""
                if "수익률" in name or "변동성" in name or "낙폭" in name:
                    diff_str = f"{diff_sign}{diff * 100:.2f}%p"
                else:
                    diff_str = f"{diff_sign}{diff:.3f}"
                compare_html += f"""
                <tr>
                    <td class="metric-name">{name}</td>
                    <td class="metric-value">{strat_v}</td>
                    <td class="metric-value">{bm_v}</td>
                    <td class="metric-value" style="color:{diff_color}">{diff_str}</td>
                </tr>"""

            benchmark_compare_html = f"""
    <div class="section">
        <div class="section-title">전략 vs KOSPI 비교</div>
        <table>
            <thead><tr><th>지표</th><th>전략</th><th>KOSPI</th><th>차이</th></tr></thead>
            <tbody>{compare_html}
            </tbody>
        </table>
    </div>"""

        # --- 월별 수익률 HTML 테이블 ---
        monthly_table_html = ""
        if not monthly_table.empty:
            month_header = "<tr><th>연도</th>"
            for col in monthly_table.columns:
                month_header += f"<th>{col}</th>"
            month_header += "</tr>"

            month_rows = ""
            for year in monthly_table.index:
                month_rows += f"<tr><td>{year}</td>"
                for col in monthly_table.columns:
                    val = monthly_table.loc[year, col]
                    if pd.isna(val):
                        month_rows += "<td style='color:#555'>-</td>"
                    else:
                        pct = val * 100
                        color = "#4ecdc4" if val >= 0 else "#ff6b6b"
                        weight = "bold" if col == "연간" else "normal"
                        month_rows += f"<td style='color:{color};font-weight:{weight}'>{pct:.1f}%</td>"
                month_rows += "</tr>"

            monthly_table_html = f"""
    <div class="section">
        <div class="section-title">월별 수익률 요약</div>
        <table>
            <thead>{month_header}</thead>
            <tbody>{month_rows}</tbody>
        </table>
    </div>"""

        # --- 연도별 월간 바 차트 HTML ---
        yearly_month_charts_html = ""
        if yearly_month_charts:
            chart_imgs = ""
            for chart_b64 in yearly_month_charts:
                chart_imgs += f'<img src="data:image/png;base64,{chart_b64}" class="chart-img">\n'
            yearly_month_charts_html = f"""
    <div class="section">
        <div class="section-title">연도별 월간 손익 차트</div>
        {chart_imgs}
    </div>"""

        # --- 월별 자산 추이 상세 테이블 ---
        monthly_pnl_html = ""
        if not monthly_pnl_df.empty:
            pnl_rows = ""
            prev_year = None
            for _, row in monthly_pnl_df.iterrows():
                year = int(row["year"])
                month = int(row["month"])
                start_v = row["start_value"]
                end_v = row["end_value"]
                pnl = row["pnl"]
                ret = row["return_pct"]
                pnl_color = "#4ecdc4" if pnl >= 0 else "#ff6b6b"
                pnl_sign = "+" if pnl >= 0 else ""
                ret_sign = "+" if ret >= 0 else ""

                # 연도 구분선
                year_label = f"{year}" if year != prev_year else ""
                year_style = "border-top:2px solid #3a3a5a;" if year != prev_year and prev_year is not None else ""
                prev_year = year

                pnl_rows += f"""
                <tr style="{year_style}">
                    <td>{year_label}</td>
                    <td>{month}월</td>
                    <td>{start_v:,.0f}</td>
                    <td>{end_v:,.0f}</td>
                    <td style="color:{pnl_color};font-weight:bold">{pnl_sign}{pnl:,.0f}</td>
                    <td style="color:{pnl_color}">{ret_sign}{ret * 100:.2f}%</td>
                </tr>"""

            # 연도별 소계
            yearly_summary_rows = ""
            for year in sorted(monthly_pnl_df["year"].unique()):
                yr_data = monthly_pnl_df[monthly_pnl_df["year"] == year]
                yr_start = yr_data.iloc[0]["start_value"]
                yr_end = yr_data.iloc[-1]["end_value"]
                yr_pnl = yr_end - yr_start
                yr_ret = yr_pnl / yr_start if yr_start != 0 else 0
                yr_color = "#4ecdc4" if yr_pnl >= 0 else "#ff6b6b"
                yr_sign = "+" if yr_pnl >= 0 else ""
                yearly_summary_rows += f"""
                <tr style="background:#16213e;font-weight:bold">
                    <td style="color:#00d2ff">{year}년 합계</td>
                    <td>-</td>
                    <td>{yr_start:,.0f}</td>
                    <td>{yr_end:,.0f}</td>
                    <td style="color:{yr_color}">{yr_sign}{yr_pnl:,.0f}</td>
                    <td style="color:{yr_color}">{yr_sign}{yr_ret * 100:.2f}%</td>
                </tr>"""

            monthly_pnl_html = f"""
    <div class="section">
        <div class="section-title">월별 자산 추이</div>
        <div style="color:#888;font-size:12px;margin-bottom:10px">단위: 원</div>
        <table>
            <thead><tr><th>연도</th><th>월</th><th>월초 자산</th><th>월말 자산</th><th>손익</th><th>수익률</th></tr></thead>
            <tbody>{pnl_rows}
            </tbody>
        </table>
        <div style="margin-top:15px">
            <div style="color:#888;font-size:12px;margin-bottom:8px">연도별 요약</div>
            <table>
                <thead><tr><th>연도</th><th></th><th>연초 자산</th><th>연말 자산</th><th>연간 손익</th><th>연간 수익률</th></tr></thead>
                <tbody>{yearly_summary_rows}
                </tbody>
            </table>
        </div>
    </div>"""

        # --- 연도별 수익률 HTML 테이블 ---
        yearly_table_html = ""
        if not yearly.empty:
            yr_rows = ""
            for y in yearly.index:
                val = yearly.loc[y]
                color = "#4ecdc4" if val >= 0 else "#ff6b6b"
                yr_rows += f"""
                <tr>
                    <td class="metric-name">{y}년</td>
                    <td class="metric-value" style="color:{color}">{val * 100:.2f}%</td>
                </tr>"""

            yearly_table_html = f"""
    <div class="section">
        <div class="section-title">연도별 수익률</div>
        <table>
            <thead><tr><th>연도</th><th>수익률</th></tr></thead>
            <tbody>{yr_rows}</tbody>
        </table>
    </div>"""

        # --- Top 5 낙폭 상세 테이블 ---
        top_dd_html = ""
        top_dds = analyzer.top_drawdowns(portfolio_values, n=5)
        if top_dds:
            dd_rows = ""
            for idx, dd in enumerate(top_dds, 1):
                start_s = dd["start"].strftime("%Y-%m-%d") if dd["start"] is not None else "-"
                trough_s = dd["trough"].strftime("%Y-%m-%d") if dd["trough"] is not None else "-"
                end_s = dd["end"].strftime("%Y-%m-%d") if dd["end"] is not None else "미회복"
                days_s = f'{dd["days"]}일' if dd["days"] is not None else "-"
                rec_s = f'{dd["recovery_days"]}일' if dd["recovery_days"] is not None else "미회복"
                depth_color = "#ff6b6b" if dd["depth"] < -0.15 else "#ff8c42"
                dd_rows += f"""
                <tr>
                    <td class="metric-name">#{idx}</td>
                    <td class="metric-value" style="color:{depth_color}">{dd['depth'] * 100:.1f}%</td>
                    <td>{start_s}</td>
                    <td>{trough_s}</td>
                    <td>{end_s}</td>
                    <td>{days_s}</td>
                    <td>{rec_s}</td>
                </tr>"""

            top_dd_html = f"""
    <div class="section">
        <div class="section-title">Top 5 낙폭 구간</div>
        <table>
            <thead><tr><th>#</th><th>낙폭</th><th>시작일</th><th>바닥일</th><th>회복일</th><th>하락 기간</th><th>회복 기간</th></tr></thead>
            <tbody>{dd_rows}</tbody>
        </table>
    </div>"""

        # --- 최고/최저 기간 ---
        best_worst_html = ""
        bw = analyzer.best_worst_periods(portfolio_values, returns)
        if bw:
            bw_rows = ""
            labels = [
                ("best_day", "최고 일간 수익", True),
                ("worst_day", "최저 일간 수익", False),
                ("best_month", "최고 월간 수익", True),
                ("worst_month", "최저 월간 수익", False),
                ("best_year", "최고 연간 수익", True),
                ("worst_year", "최저 연간 수익", False),
            ]
            for key, label, is_best in labels:
                if key in bw:
                    d = bw[key]
                    date_str = d["date"].strftime("%Y-%m-%d") if hasattr(d["date"], "strftime") else str(d["date"])
                    color = "#4ecdc4" if is_best else "#ff6b6b"
                    bw_rows += f"""
                    <tr>
                        <td class="metric-name">{label}</td>
                        <td class="metric-value" style="color:{color}">{d['value'] * 100:.2f}%</td>
                        <td>{date_str}</td>
                    </tr>"""

            best_worst_html = f"""
    <div class="section">
        <div class="section-title">최고/최저 수익률 기간</div>
        <table>
            <thead><tr><th>구분</th><th>수익률</th><th>날짜</th></tr></thead>
            <tbody>{bw_rows}</tbody>
        </table>
    </div>"""

        # --- 수익률 분포 통계 ---
        dist_html = ""
        dist = analyzer.return_distribution(returns)
        if dist:
            skew_color = "#4ecdc4" if dist["skewness"] > 0 else "#ff6b6b"
            dist_html = f"""
    <div class="section">
        <div class="section-title">수익률 분포 분석</div>
        <table>
            <thead><tr><th>지표</th><th>값</th><th>해석</th></tr></thead>
            <tbody>
                <tr>
                    <td class="metric-name">왜도 (Skewness)</td>
                    <td class="metric-value" style="color:{skew_color}">{dist['skewness']:.3f}</td>
                    <td class="metric-desc">양수: 우측 꼬리 (큰 수익 가능), 음수: 좌측 꼬리 (큰 손실 위험)</td>
                </tr>
                <tr>
                    <td class="metric-name">첨도 (Kurtosis)</td>
                    <td class="metric-value">{dist['kurtosis']:.3f}</td>
                    <td class="metric-desc">3 초과: 극단적 변동 잦음 (팻테일), 3 미만: 안정적</td>
                </tr>
                <tr>
                    <td class="metric-name">최대 연속 손실일</td>
                    <td class="metric-value" style="color:#ff6b6b">{dist['max_consecutive_loss']}일</td>
                    <td class="metric-desc">연속으로 손실을 본 최장 거래일 수</td>
                </tr>
                <tr>
                    <td class="metric-name">최대 연속 수익일</td>
                    <td class="metric-value" style="color:#4ecdc4">{dist['max_consecutive_win']}일</td>
                    <td class="metric-desc">연속으로 수익을 본 최장 거래일 수</td>
                </tr>
            </tbody>
        </table>
    </div>"""

        # --- 리밸런싱 요약 ---
        rebal_html = ""
        if turnover_log and len(turnover_log) > 0:
            avg_turnover = sum(t["turnover_rate"] for t in turnover_log) / len(turnover_log)
            total_sells = sum(t["sells"] for t in turnover_log)
            total_buys = sum(t["buys"] for t in turnover_log)

            rebal_summary = f"""
                <tr>
                    <td class="metric-name">리밸런싱 횟수</td>
                    <td class="metric-value">{len(turnover_log)}회</td>
                    <td class="metric-desc">전체 기간 동안 포트폴리오 재조정 횟수</td>
                </tr>
                <tr>
                    <td class="metric-name">평균 턴오버율</td>
                    <td class="metric-value">{avg_turnover * 100:.1f}%</td>
                    <td class="metric-desc">리밸런싱 시 평균 종목 교체 비율</td>
                </tr>
                <tr>
                    <td class="metric-name">총 매도 건수</td>
                    <td class="metric-value">{total_sells}건</td>
                    <td class="metric-desc">전체 기간 포트폴리오에서 제외된 종목 수</td>
                </tr>
                <tr>
                    <td class="metric-name">총 매수 건수</td>
                    <td class="metric-value">{total_buys}건</td>
                    <td class="metric-desc">전체 기간 포트폴리오에 신규 편입된 종목 수</td>
                </tr>"""

            # 리밸런싱 상세 이력
            rebal_detail = ""
            for idx, t in enumerate(turnover_log):
                date_fmt = f"{t['date'][:4]}-{t['date'][4:6]}-{t['date'][6:]}"
                has_details = t.get("sell_details") or t.get("buy_details")
                toggle_id = f"rebal_detail_{idx}"

                # 매매 상세 서브테이블 (좌우 2열 배치)
                detail_sub = ""
                if has_details:
                    sell_details = t.get("sell_details", [])
                    buy_details = t.get("buy_details", [])

                    # 매도 패널
                    sell_panel = ""
                    if sell_details:
                        sell_rows = ""
                        sell_total = 0
                        total_pnl = 0
                        for s in sell_details:
                            sell_total += s['amount']
                            ret = s.get('return_pct')
                            buy_px = s.get('buy_price')
                            # 수익률 표시: 색상 분기
                            if ret is not None:
                                ret_color = '#4ecdc4' if ret >= 0 else '#ff6b6b'
                                ret_str = f'<span style="color:{ret_color}">{ret:+.1%}</span>'
                                pnl = s['price'] * s['quantity'] - (buy_px or 0) * s['quantity']
                                total_pnl += pnl
                            else:
                                ret_str = '<span style="color:#555">-</span>'
                            buy_px_str = f"{buy_px:,.0f}" if buy_px else "-"
                            sell_rows += f"""<tr>
                                <td>{s['name']}</td>
                                <td style="color:#888">{s['ticker']}</td>
                                <td>{s['quantity']:,}</td>
                                <td style="color:#888">{buy_px_str}</td>
                                <td>{s['price']:,.0f}</td>
                                <td style="color:#ff6b6b">{s['amount']:,.0f}</td>
                                <td>{ret_str}</td>
                            </tr>"""
                        # 합계 행: 손익 합계 색상
                        pnl_color = '#4ecdc4' if total_pnl >= 0 else '#ff6b6b'
                        pnl_str = f'<span style="color:{pnl_color}">{total_pnl:+,.0f}</span>' if total_pnl != 0 else ''
                        sell_panel = f"""<div class="trade-panel">
                            <div class="trade-panel-header" style="color:#ff6b6b">매도 ({len(sell_details)}종목)</div>
                            <table style="font-size:11px">
                                <thead><tr><th>종목</th><th>코드</th><th>수량</th><th>매수가</th><th>매도가</th><th>금액</th><th>수익률</th></tr></thead>
                                <tbody>{sell_rows}
                                <tr style="border-top:1px solid #3a3a5a;font-weight:bold">
                                    <td colspan="5" style="color:#aaa">합계</td>
                                    <td style="color:#ff6b6b">{sell_total:,.0f}</td>
                                    <td>{pnl_str}</td>
                                </tr></tbody>
                            </table>
                        </div>"""
                    else:
                        sell_panel = """<div class="trade-panel">
                            <div class="trade-panel-header" style="color:#ff6b6b">매도</div>
                            <div style="color:#555;font-size:11px;padding:10px">매도 없음</div>
                        </div>"""

                    # 매수 패널
                    buy_panel = ""
                    if buy_details:
                        buy_rows = ""
                        buy_total = 0
                        for b in buy_details:
                            buy_total += b['amount']
                            buy_rows += f"""<tr>
                                <td>{b['name']}</td>
                                <td style="color:#888">{b['ticker']}</td>
                                <td>{b['quantity']:,}</td>
                                <td>{b['price']:,.0f}</td>
                                <td style="color:#4ecdc4">{b['amount']:,.0f}</td>
                            </tr>"""
                        buy_panel = f"""<div class="trade-panel">
                            <div class="trade-panel-header" style="color:#4ecdc4">매수 ({len(buy_details)}종목)</div>
                            <table style="font-size:11px">
                                <thead><tr><th>종목</th><th>코드</th><th>수량</th><th>단가</th><th>금액</th></tr></thead>
                                <tbody>{buy_rows}
                                <tr style="border-top:1px solid #3a3a5a;font-weight:bold">
                                    <td colspan="4" style="color:#aaa">합계</td>
                                    <td style="color:#4ecdc4">{buy_total:,.0f}</td>
                                </tr></tbody>
                            </table>
                        </div>"""
                    else:
                        buy_panel = """<div class="trade-panel">
                            <div class="trade-panel-header" style="color:#4ecdc4">매수</div>
                            <div style="color:#555;font-size:11px;padding:10px">매수 없음</div>
                        </div>"""

                    detail_sub = f"""
                    <tr id="{toggle_id}" class="trade-detail" style="display:none">
                        <td colspan="6" style="padding:0">
                            <div class="trade-grid">
                                {sell_panel}
                                {buy_panel}
                            </div>
                        </td>
                    </tr>"""

                toggle_btn = ""
                if has_details:
                    toggle_btn = f' <span class="toggle-btn" onclick="toggleDetail(\'{toggle_id}\')" style="cursor:pointer;color:#00d2ff;font-size:11px">[상세]</span>'

                rebal_detail += f"""
                <tr>
                    <td>{date_fmt}{toggle_btn}</td>
                    <td>{t['n_holdings_before']}</td>
                    <td>{t['n_holdings_after']}</td>
                    <td style="color:#ff6b6b">{t['sells']}</td>
                    <td style="color:#4ecdc4">{t['buys']}</td>
                    <td>{t['turnover_rate'] * 100:.0f}%</td>
                </tr>{detail_sub}"""

            rebal_html = f"""
    <div class="section">
        <div class="section-title">리밸런싱 요약</div>
        <table>
            <thead><tr><th>지표</th><th>값</th><th>해석</th></tr></thead>
            <tbody>{rebal_summary}</tbody>
        </table>
        <div style="margin-top:15px">
            <table>
                <thead><tr><th>날짜</th><th>변경 전</th><th>변경 후</th><th>매도</th><th>매수</th><th>턴오버</th></tr></thead>
                <tbody>{rebal_detail}</tbody>
            </table>
        </div>
    </div>
    <script>
    function toggleDetail(id) {{
        var el = document.getElementById(id);
        if (el) el.style.display = el.style.display === 'none' ? 'table-row' : 'none';
    }}
    </script>"""

        # --- 팩터 기여도 ---
        factor_html = ""
        if factor_ic and len(factor_ic) > 0:
            factor_rows = ""
            factor_labels = {
                "value_score": ("밸류", "저PBR/저PER 종목이 고수익과 연관"),
                "momentum_score": ("모멘텀", "최근 상승 종목이 추가 상승과 연관"),
                "quality_score": ("퀄리티", "우량 종목(고ROE 등)이 고수익과 연관"),
            }
            for key, ic in factor_ic.items():
                label, desc = factor_labels.get(key, (key, ""))
                color = "#4ecdc4" if ic > 0.02 else ("#ffd93d" if ic > -0.02 else "#ff6b6b")
                grade = "강함" if abs(ic) > 0.05 else ("보통" if abs(ic) > 0.02 else "약함")
                factor_rows += f"""
                <tr>
                    <td class="metric-name">{label}</td>
                    <td class="metric-value" style="color:{color}">{ic:.4f}</td>
                    <td class="metric-desc">{grade} | {desc}</td>
                </tr>"""

            factor_html = f"""
    <div class="section">
        <div class="section-title">팩터 기여도 (IC: Information Coefficient)</div>
        <table>
            <thead><tr><th>팩터</th><th>IC</th><th>해석</th></tr></thead>
            <tbody>{factor_rows}</tbody>
        </table>
        <div style="color:#666;font-size:11px;margin-top:8px;padding:0 15px">
            IC &gt; 0: 높은 팩터 점수 = 높은 수익률 (팩터 유효), IC &lt; 0: 역방향 관계
        </div>
    </div>"""

        # --- HTML 조립 ---
        cards_html = ""
        for c in cards:
            cards_html += f"""
            <div class="card">
                <div class="card-value" style="color:{c['color']}">{c['value']}</div>
                <div class="card-label">{c['label']}</div>
                <div class="card-desc">{c['desc']}</div>
            </div>"""

        detail_html = ""
        for name, val, desc in detail_rows:
            detail_html += f"""
            <tr>
                <td class="metric-name">{name}</td>
                <td class="metric-value">{val}</td>
                <td class="metric-desc">{desc}</td>
            </tr>"""

        # --- 섹션 조립 (빈 섹션 없도록 조건부 조립) ---
        sections = []

        # 상세 지표 테이블 (항상 존재)
        detail_section = f"""<div class="section">
        <div class="section-title">상세 성과 지표</div>
        <table>
            <thead><tr><th>지표</th><th>값</th><th>해석</th></tr></thead>
            <tbody>{detail_html}</tbody>
        </table>
    </div>"""

        # 1. 메인 차트 (누적수익률 + 낙폭)
        main_charts_html = ""
        for key in ["cumulative", "drawdown"]:
            if key in charts:
                main_charts_html += f'<img src="data:image/png;base64,{charts[key]}" class="chart-img">\n'
        if main_charts_html:
            sections.append(f"""<div class="section">
        <div class="section-title">성과 차트</div>
        {main_charts_html}
    </div>""")

        # 2. 상세 지표 + 벤치마크/팩터 (있으면 나란히, 없으면 단독 풀와이드)
        side_sections = []
        if benchmark_compare_html:
            side_sections.append(benchmark_compare_html)
        if factor_html:
            side_sections.append(factor_html)

        if side_sections:
            # 보조 섹션이 있으면 2열 그리드
            side_content = "".join(side_sections)
            sections.append(f'<div class="grid-2">{detail_section}<div>{side_content}</div></div>')
        else:
            # 없으면 단독 풀와이드
            sections.append(detail_section)

        # 3. 히트맵 + 연도별 차트 (둘 다 있으면 2열, 아니면 단독)
        hm_items = []
        if "monthly" in charts:
            hm_items.append(f'<img src="data:image/png;base64,{charts["monthly"]}" class="chart-img">')
        if "yearly" in charts:
            hm_items.append(f'<img src="data:image/png;base64,{charts["yearly"]}" class="chart-img">')
        if len(hm_items) == 2:
            sections.append(f"""<div class="section">
        <div class="section-title">기간별 수익률</div>
        <div class="grid-2">{"".join(hm_items)}</div>
    </div>""")
        elif hm_items:
            sections.append(f"""<div class="section">
        <div class="section-title">기간별 수익률</div>
        {hm_items[0]}
    </div>""")

        # 4. 롤링 차트 (둘 다 있으면 2열)
        rolling_items = []
        for key in ["rolling_ret", "rolling_sharpe"]:
            if key in charts:
                rolling_items.append(f'<img src="data:image/png;base64,{charts[key]}" class="chart-img">')
        if len(rolling_items) == 2:
            sections.append(f"""<div class="section">
        <div class="section-title">롤링 분석</div>
        <div class="grid-2">{"".join(rolling_items)}</div>
    </div>""")
        elif rolling_items:
            sections.append(f"""<div class="section">
        <div class="section-title">롤링 분석</div>
        {rolling_items[0]}
    </div>""")

        # 5. 연도별 월간 손익 차트
        if yearly_month_charts_html:
            sections.append(yearly_month_charts_html)

        # 6. 월별 테이블들 + 연도별 수익률 (세로 스택)
        if monthly_table_html:
            sections.append(monthly_table_html)
        if monthly_pnl_html:
            sections.append(monthly_pnl_html)
        if yearly_table_html:
            sections.append(yearly_table_html)

        # 7. 리스크 분석 (Top DD + 최고/최저/분포)
        risk_parts = []
        if top_dd_html:
            risk_parts.append(top_dd_html)
        if best_worst_html:
            risk_parts.append(best_worst_html)
        if dist_html:
            risk_parts.append(dist_html)

        if len(risk_parts) >= 2:
            # 2열 그리드: 첫 번째 + 나머지를 한 열에 묶기
            left = risk_parts[0]
            right = "<div>" + "".join(risk_parts[1:]) + "</div>"
            sections.append(f'<div class="grid-2">{left}{right}</div>')
        elif risk_parts:
            sections.append(risk_parts[0])

        # 8. 리밸런싱
        if rebal_html:
            sections.append(rebal_html)

        all_sections = "\n".join(sections)

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0f0f23; color: #e0e0e0; font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; padding: 20px; line-height: 1.5; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ color: #00d2ff; font-size: 22px; margin-bottom: 4px; }}
.subtitle {{ color: #888; font-size: 13px; margin-bottom: 16px; }}
.cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }}
.card {{ background: #1a1a2e; border-radius: 8px; padding: 14px 10px; text-align: center; border: 1px solid #2a2a4a; }}
.card-value {{ font-size: 24px; font-weight: bold; margin-bottom: 2px; }}
.card-label {{ font-size: 12px; color: #aaa; margin-bottom: 2px; }}
.card-desc {{ font-size: 11px; color: #666; }}
.section {{ margin-bottom: 16px; }}
.section-title {{ font-size: 15px; color: #00d2ff; margin-bottom: 8px; border-bottom: 1px solid #2a2a4a; padding-bottom: 6px; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }}
.grid-2 > .section {{ margin-bottom: 0; }}
table {{ width: 100%; border-collapse: collapse; background: #1a1a2e; border-radius: 6px; overflow: hidden; }}
th {{ background: #16213e; color: #00d2ff; text-align: center; padding: 7px 12px; font-size: 12px; white-space: nowrap; }}
td {{ padding: 6px 12px; border-bottom: 1px solid #2a2a4a; font-size: 12px; text-align: center; }}
th:first-child {{ text-align: left; }}
td:first-child {{ text-align: left; }}
.metric-name {{ color: #ccc; text-align: left; }}
.metric-value {{ color: #fff; font-weight: bold; text-align: center; }}
.metric-desc {{ color: #888; font-size: 11px; text-align: left; }}
.chart-img {{ width: 100%; border-radius: 6px; margin-bottom: 8px; display: block; }}
.grid-2 .chart-img {{ margin-bottom: 0; }}
.footer {{ text-align: center; color: #555; font-size: 11px; margin-top: 20px; padding-top: 10px; border-top: 1px solid #2a2a4a; }}
.trade-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 8px 10px; background: #0f0f23; }}
.trade-panel {{ background: #12122a; border-radius: 6px; padding: 8px 10px; border: 1px solid #2a2a4a; }}
.trade-panel-header {{ font-size: 12px; font-weight: bold; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #2a2a4a; }}
.trade-panel table {{ margin: 0; }}
@media (max-width: 900px) {{
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
    .grid-2 {{ grid-template-columns: 1fr; }}
    .trade-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="container">
    <h1>{title}</h1>
    <div class="subtitle">{start_dt} ~ {end_dt} | 시작 {start_val:,.0f}원 &rarr; 종료 {end_val:,.0f}원 (손익 {_fmt_amount(total_pnl)})</div>

    <div class="cards">{cards_html}
    </div>

    {all_sections}

    <div class="footer">
        Korean Quant Backtest Report | Generated by korean-quant system
    </div>
</div>
</body>
</html>"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"한글 HTML 리포트 저장: {output_path}")

    def fetch_kospi_benchmark(self, start_date: str, end_date: str) -> pd.Series:
        """KOSPI 벤치마크 수익률 조회 (FinanceDataReader)

        Args:
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)

        Returns:
            KOSPI 일별 수익률 Series
        """
        try:
            import FinanceDataReader as fdr

            kospi = fdr.DataReader("KS11", start_date, end_date)
            returns = kospi["Close"].pct_change().dropna()
            logger.info(f"KOSPI 벤치마크 로드: {len(returns)}일")
            return returns
        except Exception as e:
            logger.warning(f"KOSPI 벤치마크 로드 실패: {e}")
            return pd.Series(dtype=float)
