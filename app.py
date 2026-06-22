"""Streamlit web UI (spec section 7) — the link the successor opens.

Flow: open link -> pick the Monday -> click "실행 (Run)" -> read tables -> download Excel.
AEMO data is fetched server-side automatically; a manual upload box is the fallback.

Homepage layout (Phase 2):
  (a) 이번주 vs 지난주 — Best Case 변화 (값 + 증감 화살표 + 숫자 설명)
  (b) 실제 값 매트릭스 — [2H/4H x 충전/방전/Spread] x 지역, Best Case / 고정시간 나란히
  (c) 2025년 비교 — 같은 달 / 연평균 대비
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure `src` is importable no matter the launch directory (Streamlit Cloud,
# `streamlit run`, or AppTest all differ in CWD / sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import (articles, generation, ingest, notices, report, weather)

st.set_page_config(page_title="NEM Weekly Spread Monitor", layout="wide")
st.title("NEM Weekly Spread Monitor")
st.caption("호주 NEM 주간 배터리 차익거래 스프레드 — NSW / QLD / VIC / SA")


def _most_recent_monday(today: date) -> date:
    return today - timedelta(days=today.weekday())


@st.cache_data(show_spinner=False)
def _cached_frames(run_date: date, auto_download: bool) -> dict:
    """Cache ONLY the slow network load (raw region frames), not the report.

    The deterministic tables are recomputed from these frames on every run, so a
    code change always takes effect — caching the whole report dict can otherwise
    return a stale structure after a redeploy (KeyError on new keys)."""
    return report.build_region_frames(run_date, auto_download=auto_download)


def _oe_key() -> str | None:
    """Open Electricity API key from Streamlit Secrets (cloud) — else None,
    and the module falls back to env / local .env for development."""
    try:
        return st.secrets.get("OPENELECTRICITY_API_KEY")  # type: ignore[no-any-return]
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_generation(run_date: date, api_key: str | None) -> dict:
    return generation.fetch_generation(run_date, days=30, api_key=api_key)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_weather(run_date: date) -> "pd.DataFrame":
    return weather.fetch_daily_temp(run_date, days=30)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_notices(run_date: date) -> dict:
    return notices.fetch_notices(run_date)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_articles(run_date: date) -> dict:
    return articles.fetch_articles(run_date)


def _temp_figure(temp: "pd.DataFrame", run_date: date):
    """Plotly daily temperature lines (4 regions) with a last/this-week divider."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for region in ingest.REGIONS:
        s = temp[temp["region"] == region].sort_values("date")
        if s.empty:
            continue
        fig.add_scatter(x=s["date"], y=s["temp_mean_c"], mode="lines+markers",
                        name=region)
    boundary = pd.Timestamp(run_date - timedelta(days=7)) - pd.Timedelta(hours=12)
    fig.add_vline(x=boundary, line_width=2, line_dash="dash", line_color="#444")
    fig.add_annotation(x=boundary, yref="paper", y=1.04, showarrow=False,
                       text="◀ 지난주 | 이번주 ▶", font=dict(size=11, color="#444"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis_title="°C",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.3))
    return fig


with st.sidebar:
    st.header("실행 설정")
    default_monday = _most_recent_monday(date.today())
    run_date = st.date_input(
        "실행 기준일 (월요일)", value=default_monday,
        help="이 월요일 기준 직전 7일(직전 월요일 00:05 ~ 이번 월요일 00:00)을 분석합니다.",
    )
    auto_download = st.checkbox("AEMO 데이터 자동 다운로드", value=True)
    st.markdown("---")
    st.markdown("**자동 다운로드 실패 시 보조**: 월별 CSV를 직접 올리세요.")
    uploads = st.file_uploader(
        "PRICE_AND_DEMAND_*.csv 업로드 (여러 개 가능)",
        type="csv", accept_multiple_files=True,
    )
    run = st.button("실행 (Run)", type="primary", width="stretch")

if run_date.weekday() != 0:
    st.warning("실행 기준일은 월요일이어야 합니다. 가장 가까운 월요일을 선택하세요.")
    st.stop()

if not run:
    st.info("왼쪽에서 월요일을 고르고 **실행 (Run)** 을 누르세요. "
            "처음 실행 시 데이터 다운로드로 수십 초 걸릴 수 있습니다.")
    st.stop()


def _parse_uploads(files) -> dict[str, pd.DataFrame]:
    """Group uploaded CSVs by region into combined frames."""
    by_region: dict[str, list[pd.DataFrame]] = {}
    for f in files or []:
        df = ingest.load_raw_csv_from_buffer(f)
        for region, g in df.groupby("region"):
            by_region.setdefault(region, []).append(g)
    return {r: ingest.combine(frames) for r, frames in by_region.items()}


with st.spinner("AEMO 데이터를 받아 계산 중…"):
    try:
        uploaded = _parse_uploads(uploads)
        if uploaded:
            rep = report.build_report(
                run_date, auto_download=auto_download, uploaded=uploaded
            )
        else:
            frames = _cached_frames(run_date, auto_download)
            rep = report.build_report(run_date, frames=frames)
    except Exception as exc:  # surface a readable message to a non-technical user
        st.error(f"실행 실패: {exc}")
        st.stop()

start, end = rep["window"]
st.success(f"완료 — 분석 주: **{rep['week_start']}**  "
           f"(구간 {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}), "
           f"지역: {', '.join(rep['regions'])}")

# Resolution gate
res = rep["resolution"]
if not res["is_5min"].all():
    st.error("⚠️ 일부 지역이 5분 해상도가 아닙니다. 숫자를 신뢰하기 전에 확인하세요 (spec 2-1).")
with st.expander("해상도 검증 결과 (5분/288구간 확인)"):
    st.dataframe(res, width="stretch")

# =========================================================================== #
# 주간 Spread 업데이트 — 지난주 대비 증감 + 실제 값 매트릭스
# =========================================================================== #
st.header("주간 Spread 업데이트")

st.subheader("지난주 대비 증감")
st.caption("설명은 숫자 변화를 그대로 풀어쓴 것이며, 원인 해석은 포함하지 않습니다 (사람이 직접 작성).")
change = rep.get("best_case_change")
if change is None:
    st.info("지난주 데이터가 충분하지 않아 주간 비교를 건너뛰었습니다 (직전 7일 데이터가 불완전).")
else:
    st.dataframe(
        report.format_change_table(change)[["지역", "용량", "설명 (지난주 대비)"]],
        width="stretch", hide_index=True,
    )

st.caption("실제 값 매트릭스 — [2H/4H × 충전/방전/Spread] × 지역 (AUD/MWh)")
col_bc, col_fx = st.columns(2)
with col_bc:
    st.markdown("**Best Case**")
    st.dataframe(rep["best_case_matrix"], width="stretch")
with col_fx:
    st.markdown("**고정시간**")
    st.dataframe(rep["fixed_time_matrix"], width="stretch")

# =========================================================================== #
# 2025년 Spread — 연평균 + 분석월 (대시보드 이미지 양식)
# =========================================================================== #
st.header("2025년 Spread")
st.caption("Spread, AUD/MWh — 2025년 연평균과 분석월 기준 참조값.")
bc25 = report.reference_2025_tables(rep["month_num"], "best_case")
fx25 = report.reference_2025_tables(rep["month_num"], "fixed_time")
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Best Case**")
    st.markdown("연 평균")
    st.dataframe(bc25["annual"], width="stretch")
    st.markdown(f"{bc25['month_label']} 평균")
    st.dataframe(bc25["month"], width="stretch")
with col_b:
    st.markdown("**고정시간**")
    st.markdown("연 평균")
    st.dataframe(fx25["annual"], width="stretch")
    st.markdown(f"{fx25['month_label']} 평균")
    st.dataframe(fx25["month"], width="stretch")

# =========================================================================== #
# 전력 수요
# =========================================================================== #
st.header("전력 수요")
st.caption("밴드별 평균 MW — 시간대: 24h(전체) / daytime(10–16) / peak(16–21). 지난주·이번주 비교.")
st.dataframe(rep["demand_compare"].round(0), width="stretch", hide_index=True)

st.download_button(
    "📥 Excel 다운로드 (결정론 코어 표)", data=report.to_excel_bytes(rep),
    file_name=f"nem_spread_{rep['week_start']}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()
st.caption("아래는 외부 데이터(외부 API/스크래핑)입니다. 실패해도 위 계산에는 영향이 없습니다.")

# =========================================================================== #
# OpenNEM 발전량 및 기온 — ISOLATED
# =========================================================================== #
st.header("OpenNEM 발전량 및 기온")
try:
    gen = _cached_generation(run_date, _oe_key())
    regions_avail = gen["regions"]

    st.markdown("**발전원별 일별 발전량 (GWh/day)** — Open Electricity 트래커 재현. "
                "0선 위 = 발전(연료원별) + 수입, 0선 아래 = 부하(배터리 충전·펌핑·수출). "
                "점선 = 지난주/이번주 경계.")
    sel = st.selectbox("지역 선택", regions_avail, key="gen_region")
    st.plotly_chart(generation.tracker_figure(gen["daily"], sel, run_date),
                    use_container_width=True)

    st.markdown("**이번주 vs 지난주 요약 (GWh) — solar · wind · gas · 순수입(±)**")
    st.dataframe(gen["weekly"], width="stretch", hide_index=True)

    st.markdown("**일평균 기온(°C)**")
    try:
        temp = _cached_weather(run_date)
        st.plotly_chart(_temp_figure(temp, run_date), use_container_width=True)
    except weather.WeatherUnavailable as exc:
        st.info(f"기온 데이터 건너뜀: {exc}")
except generation.OEUnavailable as exc:
    st.info(f"발전 데이터 건너뜀 (다른 섹션에는 영향 없음): {exc}")
except Exception as exc:  # never let this section break the page
    st.warning(f"발전 섹션 일시 오류 (건너뜀): {exc}")

# =========================================================================== #
# AEMO 공지 · 관련 아티클 — ISOLATED, no AI summary
# =========================================================================== #
st.header("AEMO 공지 · 관련 아티클")
st.caption("해당 주, 사람이 직접 검토·작성. 자동 요약 없음 — 원문 링크와 발췌만 제공합니다.")

link_col = st.column_config.LinkColumn("link", display_text="열기")
try:
    nres = _cached_notices(run_date)
    st.markdown(f"**AEMO Market Notices** — 주간 {nres['total_in_week']}건 중 "
                f"최근 {nres['shown']}건 표시")
    ndf = notices.notices_dataframe(nres)
    if ndf.empty:
        st.write("표시할 공지가 없습니다.")
    else:
        st.dataframe(ndf, width="stretch", hide_index=True,
                     column_config={"link": link_col})
except notices.NoticesUnavailable as exc:
    st.info(f"AEMO 공지 건너뜀: {exc}")
except Exception as exc:
    st.warning(f"공지 섹션 일시 오류 (건너뜀): {exc}")

try:
    ares = _cached_articles(run_date)
    st.markdown("**관련 아티클** (WattClarity · RenewEconomy RSS)")
    adf = articles.articles_dataframe(ares)
    if adf.empty:
        st.write("표시할 아티클이 없습니다.")
    else:
        st.dataframe(adf, width="stretch", hide_index=True,
                     column_config={"link": link_col})
    if ares.get("errors"):
        st.caption(f"일부 피드 실패: {ares['errors']}")
except articles.ArticlesUnavailable as exc:
    st.info(f"아티클 건너뜀: {exc}")
except Exception as exc:
    st.warning(f"아티클 섹션 일시 오류 (건너뜀): {exc}")
