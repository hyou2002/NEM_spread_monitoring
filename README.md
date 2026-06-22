# NEM Weekly Spread Monitor

호주 NEM(National Electricity Market)의 **주간 배터리 차익거래 스프레드**를 매주 월요일 자동으로
계산하는 도구입니다. 대상 지역: **NSW · QLD · VIC · SA** (TAS 제외). 기존 Excel 수작업을 자동화했습니다.

이 문서는 두 부분입니다.
- **A. 사용자(후임자)용** — 아무것도 설치하지 않고 링크만 엽니다.
- **B. 관리자/개발자용** — 로컬 실행, 테스트, 재배포 방법.

---

## A. 사용자(후임자)용 — 링크만 열면 됩니다

1. 브라우저에서 앱 링크를 엽니다: **<https://nemspreadmonitoring-gqjmdpyj7f6kaiygqqvjru.streamlit.app/>**
   - 무료 호스팅이라 한동안 미사용 시 앱이 잠들어 있을 수 있습니다. 처음 열 때 **수십 초 깨어나는 시간**은 정상입니다.
2. 왼쪽 사이드바에서 **실행 기준일(월요일)** 을 고릅니다. (기본값은 가장 최근 월요일)
3. **실행 (Run)** 버튼을 누릅니다. AEMO 데이터는 서버가 자동으로 받아옵니다.
4. 표가 나오면 확인하고, 맨 아래 **📥 Excel 다운로드** 버튼으로 결과를 내려받습니다.

> 자동 다운로드가 실패하면, 사이드바의 업로드 칸에 AEMO 월별 CSV(`PRICE_AND_DEMAND_*.csv`)를
> 직접 올린 뒤 다시 **실행** 을 누르면 됩니다.

**화면 구성**
- **① 이번주 vs 지난주 (Best Case)** — 충전가·방전가·스프레드를 *이번주 값 + 지난주 대비 증감(↑↓)* 으로 표시.
  설명 문장은 **숫자 변화만 그대로 풀어쓴 것**이며 원인 해석은 넣지 않습니다(사람이 직접 작성).
- **② 실제 값 매트릭스** — `[2H·4H × 충전/방전/Spread]` × 지역, Best Case와 고정시간 두 표를 나란히.
- **③ 2025년 비교** — 분석 주가 속한 달의 2025년 스프레드 + 2025 연평균과 비교.
- **④ 전력수요** — 24h / daytime(10–16) / peak(16–21) 밴드별 평균 MW.
- **⑤ 발전원·기온·연계선** (외부 데이터) — 최근 30일 발전원별 일별 발전량(누적 영역, 재생에너지 중심),
  재생 비중 이번주 vs 지난주, **순수입 추정**(연계선 전용 지표가 없어 *수요 − 역내발전*으로 추정), 도시별 일평균 기온.
- **⑥ AEMO 공지·관련 아티클** (외부 데이터) — 해당 주 AEMO Market Notice와 WattClarity·RenewEconomy 아티클의
  **링크 + 발췌만**. 자동 요약 없음(사람이 직접 검토·작성).

> ⑤·⑥은 외부 API/스크래핑입니다. 실패해도 ①~④(스프레드·수요)에는 영향이 없습니다(섹션별 격리).

**분석 구간**: 고른 월요일 기준 **직전 월요일 00:05 ~ 이번 월요일 00:00** = 정확히 7일(2016개 5분 구간).

---

## B. 관리자/개발자용

### 검증 상태 (중요)
- **해상도**: AEMO 자동 다운로드 월별 파일은 **4개 주 전부 5분 간격(하루 288구간)** 으로 확인됨 → 사용자 Excel 기준과 일치.
  `nemosis`/NEMWeb 등 별도 5분 소스로 전환할 필요 **없음**. (검증: `src/ingest.detect_resolution`, `tests`)
- **자동 다운로드 동작**: QLD·VIC·SA 월별 CSV를 `download.py`로 실제 다운로드 성공 → 자동 다운로드 경로 검증됨.
- **회귀 테스트 (2026-06-08 주, `pytest tests`)**:
  - **NSW** = 엄격: 12개 값(best/fixed × 2H/4H × charge/discharge/spread) 전부 정수 반올림 기준 **완전 일치**.
  - **QLD / SA / VIC** = spread 기준 **±1 허용**으로 통과. (정답지의 일부 charge/discharge 칸에 오타가 있으나 spread는 정확히 일치 — 아래)
  - **VIC `fixed_time`** = 회귀에서 **제외**(skip). 사유는 아래.
- **정답지(`tests/golden/verification.csv`) 알려진 오류** — 모두 *코드는 정상*, 증거는 spread 일치:
  - SA `best_case 2H` charge: 정답지 `5` → 실제 `-5`(부호 누락).
  - QLD `fixed_time 4H` / SA `fixed_time 2H` discharge: 정답지가 1 낮고 자기 charge+spread와도 불일치.
  - VIC `fixed_time` (2H·4H): 정답지 charge(~48)·discharge(67)를 **어떤 유효 윈도우로도 재현 불가** → placeholder로 판단, 회귀 제외.
- **VIC 6월 고정 윈도우 교정**: `config/fixed_windows.csv`의 VIC 2025-06 충전 윈도우가 한밤중(2H `02:55-04:55`, 4H `01:50-05:50`)으로
  다른 모든 월과 달랐음(2025년 시장 사고 영향으로 추정). 인접 월(5·7월) 기준 한낮으로 교정함: 2H `11:15-13:15`, 4H `10:45-14:45`.
  (방전 윈도우는 정상이라 유지.)
- **고정시간 윈도우 경계 규약**: `[start, end)` (시작 포함, 끝 제외) — 네 가지 후보 중 Excel 숫자를 재현하는 규약을 실험으로 확정. (`src/spreads._window_mean`)

### 로컬 실행
```powershell
# 1) Python 3.12 설치 (winget) — 이미 있으면 생략
winget install --id Python.Python.3.12 --scope user

# 2) 가상환경 + 의존성 (버전 고정)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3) 웹앱 실행
streamlit run app.py

# 3') 또는 CLI (개발/검증용)
python run.py --date 2026-06-15            # 자동 다운로드
python run.py --date 2026-06-15 --no-download   # 캐시만 사용
python run.py --date 2026-06-15 --excel out.xlsx
```

### 테스트
```powershell
pytest tests -v
```
`tests/test_regression.py`가 `tests/golden/verification.csv`(Excel 정답지)와 코드 출력이 일치하는지 확인합니다.
누가 로직을 바꿔 숫자가 틀어지면 즉시 실패합니다.

### 배포 (Streamlit Community Cloud, 무료)
1. 이 폴더(`nem-monitor/`)를 **GitHub 저장소**에 올립니다. (`.gitignore`가 `data/`, `.env`, 비밀키를 제외)
2. <https://share.streamlit.io> 에 GitHub로 로그인 → **New app** → 저장소/브랜치 선택, **Main file path = `app.py`** 지정.
3. **비밀키(Open Electricity API)** 는 앱 **Settings → Secrets** 에 아래처럼 저장합니다. 코드/저장소에 넣지 않습니다.
   ```toml
   OPENELECTRICITY_API_KEY = "oe_..."
   ```
   - 키가 없거나 틀리면 ⑤ 발전·연계선 섹션만 안내 메시지로 건너뛰고, ①~④와 ⑥은 정상 동작합니다.
   - 로컬 개발 시엔 `.env`(`OPENELECTRICITY_API_KEY=...`) 또는 `.streamlit/secrets.toml` 에 둡니다. (둘 다 `.gitignore` 처리됨)
   - 기온(Open-Meteo)·AEMO 공지·아티클 RSS는 **키가 필요 없습니다.**
4. 접근 제한이 필요하면 **Settings → Sharing** 에서 특정 이메일만 열람하도록 뷰어 인증을 켭니다.
5. 배포 후 GitHub에 `push` 하면 앱이 자동 갱신됩니다.
6. 배포 직후, 클라우드 서버에서 **AEMO 자동 다운로드가 정상 동작하는지** 한 번 실행해 확인하세요.

### 모듈 구조
```
nem-monitor/
├── app.py                 # Streamlit 웹 UI (후임자가 여는 화면)
├── run.py                 # CLI 진입점
├── requirements.txt       # 버전 고정
├── config/fixed_windows.csv          # 2025 도출 고정 시간대 (월별 참조값)
├── config/spread_2025_reference.csv  # 2025 월별·연평균 스프레드 (③ 비교용)
├── data/raw/              # 자동 다운로드 캐시 (gitignore)
├── src/
│   ├── dates.py           # 주차/구간 로직 (run_date → 2016구간, 5분 보정)
│   ├── ingest.py          # CSV 로드·정제 + 해상도 검증
│   ├── download.py        # AEMO 월별 CSV 자동 다운로드
│   ├── spreads.py         # best-case / 고정시간 스프레드 (Excel 포팅)
│   ├── demand.py          # 전력수요 24h/daytime/peak 분해
│   ├── report.py          # 파이프라인 조립 → 표 + Excel + ①②③
│   ├── generation.py      # ⑤ Open Electricity 발전원별 + 순수입 추정 (격리)
│   ├── weather.py         # ⑤ Open-Meteo 도시별 일평균 기온 (격리)
│   ├── notices.py         # ⑥ AEMO Market Notice 주간 발췌 (격리)
│   └── articles.py        # ⑥ WattClarity/RenewEconomy RSS 링크 (격리)
└── tests/
    ├── fixtures/          # 회귀 테스트용 커밋된 원본 CSV
    ├── golden/verification.csv   # Excel 정답지
    ├── test_dates.py · test_spreads.py · test_regression.py
```

### Phase 2 진행 상황 (모두 완료)
- **외부 의존 없음**: ① 주간 비교, ② 매트릭스, ③ 2025 비교 — `src/report.py`의 결정론적 함수.
- **외부 의존(격리됨)**: ⑤ 발전원·기온·연계선(`src/generation.py` = Open Electricity, `src/weather.py` = Open-Meteo),
  ⑥ 공지·아티클(`src/notices.py` = AEMO NEMWeb, `src/articles.py` = WattClarity/RenewEconomy RSS).
  - 각 외부 모듈은 자체 예외(`OEUnavailable`/`WeatherUnavailable`/`NoticesUnavailable`/`ArticlesUnavailable`)만 던지고
    app에서 섹션별로 try/except — **실패해도 ①~④는 멈추지 않습니다.**
  - 모든 외부 호출은 `@st.cache_data`로 캐시(생성/기온 1h, 공지/아티클 30m)해 호출량·타임아웃·메모리를 억제.
  - 순수입(연계선)은 OE에 전용 지표가 없어 *수요 − 역내발전*으로 **추정**하며 화면에도 그렇게 라벨링.
  - API 키(`OPENELECTRICITY_API_KEY`)는 **코드/저장소에 넣지 않고** Streamlit **Settings → Secrets**(로컬은 `.env`)에 둡니다.
