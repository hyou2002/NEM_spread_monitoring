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
def _cached_report(run_date: date, auto_download: bool) -> dict:
    """Cache the deterministic pipeline by (run_date, auto_download).

    Only used when there are no manual uploads (uploaded DataFrames are not
    cache-key friendly). Speeds up re-runs and keeps us inside Cloud limits.
    """
    return report.build_report(run_date, auto_download=auto_download)


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
            rep = _cached_report(run_date, auto_download)
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

# --------------------------------------------------------------------------- #
# (a) Week-over-week Best Case change
# --------------------------------------------------------------------------- #
st.subheader("① 이번주 vs 지난주 — Best Case (AUD/MWh)")
st.caption("값 = 이번주, 화살표·숫자 = 지난주 대비 증감. 설명은 숫자 변화를 그대로 풀어쓴 것이며, "
           "원인 해석은 포함하지 않습니다 (사람이 직접 작성).")
change = rep.get("best_case_change")
if change is None:
    st.info("지난주 데이터가 충분하지 않아 주간 비교를 건너뛰었습니다 "
            "(직전 7일 데이터가 불완전).")
else:
    st.dataframe(report.format_change_table(change), width="stretch",
                 hide_index=True)

# --------------------------------------------------------------------------- #
# (b) Metric matrices — Best Case / Fixed Time side by side
# --------------------------------------------------------------------------- #
st.subheader("② 실제 값 — 매트릭스 (AUD/MWh)")
col_bc, col_fx = st.columns(2)
with col_bc:
    st.markdown("**Best Case (시간대 자유)**")
    st.dataframe(rep["best_case_matrix"], width="stretch")
with col_fx:
    st.markdown("**고정시간 (Fixed Time)**")
    st.dataframe(rep["fixed_time_matrix"], width="stretch")

# --------------------------------------------------------------------------- #
# (c) 2025 reference comparison
# --------------------------------------------------------------------------- #
st.subheader(f"③ 2025년 비교 — 분석월({rep['month_num']:02d}월) 및 연평균 대비 (Spread, AUD/MWh)")
ref = rep["reference_2025"]
col_bc2, col_fx2 = st.columns(2)
with col_bc2:
    st.markdown("**Best Case**")
    st.dataframe(ref[ref["방식"] == "best_case"].drop(columns="방식"),
                 width="stretch", hide_index=True)
with col_fx2:
    st.markdown("**고정시간 (Fixed Time)**")
    st.dataframe(ref[ref["방식"] == "fixed_time"].drop(columns="방식"),
                 width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
# Demand + download
# --------------------------------------------------------------------------- #
st.subheader("④ 전력수요 (밴드별 평균 MW: 24h / daytime 10–16 / peak 16–21)")
st.dataframe(rep["demand"].round(0), width="stretch")

st.download_button(
    "📥 Excel 다운로드 (① ~ ④ 결정론 코어)", data=report.to_excel_bytes(rep),
    file_name=f"nem_spread_{rep['week_start']}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()
st.caption("아래 ⑤·⑥은 외부 데이터(외부 API/스크래핑)입니다. 실패해도 위 ①~④ 계산에는 영향이 없습니다.")

# --------------------------------------------------------------------------- #
# (d) Generation mix / temperature / interconnector — ISOLATED
# --------------------------------------------------------------------------- #
st.subheader("⑤ 발전원 · 기온 · 연계선 (Open Electricity / Open-Meteo, 최근 30일)")
try:
    gen = _cached_generation(run_date, _oe_key())
    supply = gen["generation"]
    regions_avail = [r for r in ingest.REGIONS if r in set(supply["region"])]

    st.markdown("**발전원별 일별 발전량 (MWh, 누적 영역) — 재생에너지 중심**")
    sel = st.selectbox("지역 선택", regions_avail, key="gen_region")
    pivot = (supply[supply["region"] == sel]
             .pivot_table(index="date", columns="group", values="energy_mwh",
                          aggfunc="sum")
             .sort_index())
    # order columns renewable-first for readability
    order = [g for g in (generation.RENEWABLE + generation.NON_RENEWABLE
                         + generation.LOAD_GROUPS) if g in pivot.columns]
    st.area_chart(pivot[order])

    col_r, col_n = st.columns(2)
    with col_r:
        st.markdown("**재생에너지 비중 — 이번주 vs 지난주 (%)**")
        wk = gen["weekly"]
        share = wk.pivot(index="region", columns="period",
                         values="renewable_pct").reindex(regions_avail).round(1)
        st.dataframe(share, width="stretch")
    with col_n:
        st.markdown("**순수입 추정 (MWh, +수입/−수출) — 주간 합계**")
        st.caption("연계선 전용 지표가 없어 *추정값*: 순수입 = 수요 − 역내 발전.")
        ni = wk.pivot(index="region", columns="period",
                      values="net_import_mwh").reindex(regions_avail).round(0)
        st.dataframe(ni, width="stretch")

    st.markdown("**일평균 기온 (°C) — 도시(수요 중심지) 기준**")
    try:
        temp = _cached_weather(run_date)
        tpivot = temp.pivot_table(index="date", columns="region",
                                  values="temp_mean_c").sort_index()
        st.line_chart(tpivot)
    except weather.WeatherUnavailable as exc:
        st.info(f"기온 데이터 건너뜀: {exc}")
except generation.OEUnavailable as exc:
    st.info(f"발전·연계선 데이터 건너뜀 (다른 섹션에는 영향 없음): {exc}")
except Exception as exc:  # never let this section break the page
    st.warning(f"⑤ 섹션 일시 오류 (건너뜀): {exc}")

# --------------------------------------------------------------------------- #
# (e) AEMO notices + related articles — ISOLATED, no AI summary
# --------------------------------------------------------------------------- #
st.subheader("⑥ AEMO 공지 · 관련 아티클 (해당 주, 사람이 직접 검토·작성)")
st.caption("자동 요약 없음 — 원문 링크와 발췌만 제공합니다.")

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
