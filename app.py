"""Streamlit web UI (spec section 7) — the link the successor opens.

Flow: open link -> pick the Monday -> click "실행 (Run)" -> read tables -> download Excel.
AEMO data is fetched server-side automatically; a manual upload box is the fallback.

Homepage layout (Phase 2):
  (a) 이번주 vs 지난주 — Best Case 변화 (값 + 증감 화살표 + 숫자 설명)
  (b) 실제 값 매트릭스 — [2H/4H x 충전/방전/Spread] x 지역, Best Case / 고정시간 나란히
  (c) 2025년 비교 — 같은 달 / 연평균 대비
"""

from __future__ import annotations

import html as _html
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
st.caption("호주 NEM 주간 배터리 차익거래 스프레드: NSW / QLD / VIC / SA")


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
def _cached_gen_raw(run_date: date, api_key: str | None) -> dict:
    """Cache ONLY the raw network fetch (stable shape). The tracker structure is
    rebuilt fresh each run so code changes always take effect (no stale cache)."""
    return generation.fetch_raw(run_date, days=30, api_key=api_key)


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_weather(run_date: date) -> "pd.DataFrame":
    return weather.fetch_daily_temp(run_date, days=30)


# Bump when the notices/articles item STRUCTURE changes — it is part of the cache
# key, so increasing it invalidates any stale cached result after a redeploy.
_VIEW_VER = 2


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_notices(run_date: date, _ver: int = _VIEW_VER) -> dict:
    return notices.fetch_notices(run_date)


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_articles(run_date: date, _ver: int = _VIEW_VER) -> dict:
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
                       text="◀ 지난주 | 이번 주 ▶", font=dict(size=11, color="#444"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis_title="°C",
                      legend=dict(orientation="h", yanchor="bottom", y=-0.3))
    return fig


_TBL_CSS = (
    "<style>"
    "table.nemtbl{border-collapse:collapse;width:100%;font-size:0.9rem;margin:0 0 .4rem;}"
    "table.nemtbl th,table.nemtbl td{border:1px solid #d6d9de;padding:6px 10px;"
    "text-align:center;}"
    "table.nemtbl th{background:#f2f4f7;font-weight:600;}"
    "table.nemtbl td.grp{font-weight:600;background:#fafbfc;vertical-align:middle;}"
    "table.nemtbl td.txt{text-align:left;}"
    "table.nemtbl a{color:#2563eb;text-decoration:none;}"
    "</style>"
)


def _cell(col: str, val, link_col: str | None) -> str:
    if col == link_col and isinstance(val, str) and val.startswith("http"):
        href = _html.escape(val, quote=True)
        return f'<td><a href="{href}" target="_blank">열기</a></td>'
    s = "" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
    cls = ' class="txt"' if len(s) > 16 else ""
    return f"<td{cls}>{_html.escape(s)}</td>"


def _html_table(df: "pd.DataFrame", *, merge_col: str | None = None,
                link_col: str | None = None) -> str:
    """Static HTML table. ``merge_col`` merges repeated cells (rowspan; rows must
    be pre-grouped). ``link_col`` renders that column as a clickable link. All
    text is HTML-escaped."""
    cols = list(df.columns)
    rows = df.to_dict("records")
    parts = [_TBL_CSS, '<table class="nemtbl"><thead><tr>',
             *[f"<th>{_html.escape(str(c))}</th>" for c in cols],
             "</tr></thead><tbody>"]
    if merge_col:
        others = [c for c in cols if c != merge_col]
        j, n = 0, len(rows)
        while j < n:
            g = rows[j][merge_col]
            k = j
            while k < n and rows[k][merge_col] == g:
                k += 1
            for ri in range(j, k):
                parts.append("<tr>")
                if ri == j:
                    parts.append(f'<td class="grp" rowspan="{k - j}">'
                                 f"{_html.escape(str(g))}</td>")
                parts += [_cell(c, rows[ri][c], link_col) for c in others]
                parts.append("</tr>")
            j = k
    else:
        for rec in rows:
            parts.append("<tr>")
            parts += [_cell(c, rec[c], link_col) for c in cols]
            parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _static(df: "pd.DataFrame") -> None:
    """Render a plain static table (no index)."""
    st.markdown(_html_table(df), unsafe_allow_html=True)


def _static_indexed(df: "pd.DataFrame", index_name: str) -> None:
    """Render a static table showing the index as the first column."""
    st.markdown(_html_table(df.reset_index().rename(columns={"index": index_name})),
                unsafe_allow_html=True)


_MIDHEAD_CSS = (
    "<style>.midhead{background:#e6f0fb;border-left:4px solid #5b9bd5;"
    "padding:.3rem .7rem;border-radius:4px;font-weight:600;font-size:1.15rem;"
    "margin:.8rem 0 .4rem;}</style>"
)


def _midhead(text: str) -> None:
    """중제목 — light-blue background band."""
    st.markdown(f"{_MIDHEAD_CSS}<div class='midhead'>{_html.escape(text)}</div>",
                unsafe_allow_html=True)


def _pager(n: int, key: str, page_size: int = 5) -> tuple[int, int]:
    """Render ◀ / ▶ nav and return the (start, end) slice for the current page."""
    pages = max(1, (n + page_size - 1) // page_size)
    pg = st.session_state.get(key, 0)
    c1, c2, c3 = st.columns([1, 2, 1])
    if c1.button("◀ 이전", key=f"{key}_prev", disabled=(pg <= 0)):
        pg -= 1
    if c3.button("다음 ▶", key=f"{key}_next", disabled=(pg >= pages - 1)):
        pg += 1
    pg = max(0, min(pg, pages - 1))
    st.session_state[key] = pg
    c2.markdown(f"<div style='text-align:center;padding-top:.4rem;color:#555;'>"
                f"{pg + 1} / {pages}</div>", unsafe_allow_html=True)
    return pg * page_size, (pg + 1) * page_size


def _paged_table(df: "pd.DataFrame", *, key: str, link_col: str | None = None,
                 page_size: int = 5) -> None:
    """Dynamic table paged with ◀ / ▶ buttons (no long scroll)."""
    lo, hi = _pager(len(df), key, page_size)
    st.markdown(_html_table(df.iloc[lo:hi], link_col=link_col),
                unsafe_allow_html=True)


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
    run = st.button("실행", type="primary", width="stretch")

if run_date.weekday() != 0:
    st.warning("실행 기준일은 월요일이어야 합니다. 가장 가까운 월요일을 선택하세요.")
    st.stop()

if run:
    st.session_state["has_run"] = True
if not st.session_state.get("has_run"):
    st.info("왼쪽에서 월요일을 고르고 **실행** 을 누르세요. "
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
st.success(f"완료! 분석주: **{rep['week_start']}** "
           f"(구간 {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}), "
           f"지역: {', '.join(rep['regions'])}")

# Resolution gate
res = rep["resolution"]
if not res["is_5min"].all():
    st.error("⚠️ 일부 지역이 5분 해상도가 아닙니다. 숫자를 신뢰하기 전에 확인하세요 (spec 2-1).")
with st.expander("해상도 검증 결과 (5분/288구간 확인)"):
    _static(res)

# =========================================================================== #
# 1. 주간 Spread 업데이트
# =========================================================================== #
st.header("1. 주간 Spread 업데이트")

_midhead("지난주 대비 증감")
st.caption("설명은 숫자 변화만 풀어쓴 것이며, 원인 해석은 포함하지 않습니다.  \n단위: AUD/MWh")
change = rep.get("best_case_change")
if change is None:
    st.info("지난주 데이터가 충분하지 않아 주간 비교를 건너뛰었습니다 (직전 7일 데이터가 불완전).")
else:
    chg = report.format_change_table(change)[["지역", "용량", "설명 (지난주 대비)"]]
    st.markdown(_html_table(chg, merge_col="지역"), unsafe_allow_html=True)

_midhead("이번 주 Spread")
st.caption("[2H/4H × 충전/방전/Spread] × 지역 (AUD/MWh)")
col_bc, col_fx = st.columns(2)
with col_bc:
    st.markdown("**Best Case**")
    _static_indexed(rep["best_case_matrix"], "구분")
with col_fx:
    st.markdown("**고정시간**")
    _static_indexed(rep["fixed_time_matrix"], "구분")

_midhead("2025년 Spread")
st.caption("Spread, AUD/MWh — 2025년 연평균과 분석월 기준 참조값.")
bc25 = report.reference_2025_tables(rep["month_num"], "best_case")
fx25 = report.reference_2025_tables(rep["month_num"], "fixed_time")
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Best Case**")
    st.caption("연 평균")
    _static_indexed(bc25["annual"], "구분")
    st.caption(f"{bc25['month_label']} 평균")
    _static_indexed(bc25["month"], "구분")
with col_b:
    st.markdown("**고정시간**")
    st.caption("연 평균")
    _static_indexed(fx25["annual"], "구분")
    st.caption(f"{fx25['month_label']} 평균")
    _static_indexed(fx25["month"], "구분")

# =========================================================================== #
# 2. 전력 수요
# =========================================================================== #
st.header("2. 전력 수요")
st.caption("지난주·이번 주 시간대별 평균 수요(MW/5분) 비교. 24h(전체) / daytime(10–16) / peak(16–21)")
_dem = rep["demand_compare"].copy()
for _c in ("지난주", "이번 주", "증감"):
    _dem[_c] = _dem[_c].map(lambda v: "-" if pd.isna(v) else f"{v:,.0f}")
st.markdown(_html_table(_dem, merge_col="지역"), unsafe_allow_html=True)

st.download_button(
    "📥 Excel 다운로드 (결정론 코어 표)", data=report.to_excel_bytes(rep),
    file_name=f"nem_spread_{rep['week_start']}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()
st.caption("아래는 외부 데이터(외부 API/스크래핑)입니다. OpenNEM의 경우 최근 1년 데이터까지만 "
           "열람 가능하며, 로딩에 실패하더라도 위 계산에는 영향이 없습니다.")

# =========================================================================== #
# OpenNEM 발전량 및 기온 — ISOLATED
# =========================================================================== #
st.header("3. OpenNEM 발전량 및 기온")
try:
    gen = generation.build_tracker(_cached_gen_raw(run_date, _oe_key()), run_date)
    regions_avail = gen["regions"]

    _midhead("발전원별 일별 발전량 (GWh/day)")
    st.caption("Open Electricity 웹사이트와 최대한 유사하게 구현해보았습니다.  \n"
               "0선 위 = 발전원별 발전량 + 수입량, 0선 아래 = 배터리 및 양수 충전량 + 수출량")
    sel = st.selectbox("지역 선택", regions_avail, key="gen_region")
    st.plotly_chart(generation.tracker_figure(gen["daily"], sel, run_date),
                    width="stretch")

    st.markdown("**지난주 대비 증감 (GWh)**")
    st.caption("지난주·이번 주 solar · wind · gas · 순수입(±) 증감 비교.")
    _wk = gen["weekly"].copy()
    for _c in ("지난주", "이번 주", "증감"):
        _wk[_c] = _wk[_c].map(lambda v: "-" if pd.isna(v) else f"{v:,.1f}")
    st.markdown(_html_table(_wk, merge_col="지역"), unsafe_allow_html=True)

    _midhead("일평균 기온(°C)")
    try:
        temp = _cached_weather(run_date)
        st.plotly_chart(_temp_figure(temp, run_date), width="stretch")
    except weather.WeatherUnavailable as exc:
        st.info(f"기온 데이터 건너뜀: {exc}")
except generation.OEUnavailable as exc:
    st.info(f"발전 데이터 건너뜀 (다른 섹션에는 영향 없음): {exc}")
except Exception as exc:  # never let this section break the page
    st.warning(f"발전 섹션 일시 오류 (건너뜀): {exc}")

# =========================================================================== #
# AEMO 공지 · 관련 아티클 — ISOLATED, no AI summary
# =========================================================================== #
st.header("4. AEMO 공지 · 관련 아티클")
st.caption("AEMO Notice 및 관련 아티클의 원문 링크만 제공합니다.")

try:
    nres = _cached_notices(run_date, _VIEW_VER)
    _midhead("AEMO Market Notices")
    st.caption(f"(주간 {nres['total_in_week']}건 중 가격검토 공지 제외, {nres['shown']}건) "
               "· 제목을 펼치면 원문이 표시됩니다. (AEMO 원본 파일은 브라우저에서 열리지 않고 "
               "다운로드되어, 본문을 앱 안에 표시합니다.)")
    items = nres["items"]
    if not items:
        st.write("표시할 공지가 없습니다.")
    else:
        lo, hi = _pager(len(items), "pg_notices")
        for it in items[lo:hi]:
            with st.expander(f"[{it['date']}] {it['title']}"):
                st.caption(f"유형: {it['type']}")
                st.text(it.get("text", ""))
                st.markdown(f"[원문 파일(.txt 내려받기)]({it['link']})")
except notices.NoticesUnavailable as exc:
    st.info(f"AEMO 공지 건너뜀: {exc}")
except Exception as exc:
    st.warning(f"공지 섹션 일시 오류 (건너뜀): {exc}")

try:
    ares = _cached_articles(run_date, _VIEW_VER)
    _midhead("관련 아티클")
    st.caption("WattClarity · RenewEconomy RSS")
    adf = articles.articles_dataframe(ares)
    if adf.empty:
        st.write("표시할 아티클이 없습니다.")
    else:
        _paged_table(adf, key="pg_articles", link_col="link")
    if ares.get("errors"):
        st.warning(f"일부 아티클 피드 실패(해당 소스만 제외됨): {ares['errors']}")
except articles.ArticlesUnavailable as exc:
    st.info(f"아티클 건너뜀: {exc}")
except Exception as exc:
    st.warning(f"아티클 섹션 일시 오류 (건너뜀): {exc}")
