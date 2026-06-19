"""Streamlit web UI (spec section 7) — the link the successor opens.

Flow: open link -> pick the Monday -> click "실행 (Run)" -> read tables -> download Excel.
AEMO data is fetched server-side automatically; a manual upload box is the fallback.
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

from src import ingest, report

st.set_page_config(page_title="NEM Weekly Spread Monitor", layout="wide")
st.title("NEM Weekly Spread Monitor")
st.caption("호주 NEM 주간 배터리 차익거래 스프레드 — NSW / QLD / VIC / SA")


def _most_recent_monday(today: date) -> date:
    return today - timedelta(days=today.weekday())


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
        rep = report.build_report(
            run_date, auto_download=auto_download, uploaded=uploaded or None
        )
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

st.subheader("주간 스프레드 (AUD/MWh)")
st.dataframe(rep["spreads"].round(1), width="stretch")

st.subheader("스프레드 요약 — 지역 × 방식/용량")
st.dataframe(report.spreads_pivot(rep["spreads"]), width="stretch")

st.subheader("전력수요 (밴드별 평균 MW: 24h / daytime 10–16 / peak 16–21)")
st.dataframe(rep["demand"].round(0), width="stretch")

st.download_button(
    "📥 Excel 다운로드", data=report.to_excel_bytes(rep),
    file_name=f"nem_spread_{rep['week_start']}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
