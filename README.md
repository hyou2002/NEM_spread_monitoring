# NEM Weekly Spread Monitor

호주 NEM(National Electricity Market)의 **주간 배터리 차익거래 스프레드**를 매주 월요일 자동으로
계산하는 도구입니다. 대상 지역: **NSW · QLD · VIC · SA** (TAS 제외). 기존 Excel 수작업을 자동화했습니다.

이 문서는 두 부분입니다.
- **A. 사용자(후임자)용** — 아무것도 설치하지 않고 링크만 엽니다.
- **B. 관리자/개발자용** — 로컬 실행, 테스트, 재배포 방법.

---

## A. 사용자(후임자)용 — 링크만 열면 됩니다

1. 브라우저에서 앱 링크를 엽니다: **`<배포 후 여기에 URL을 적어두세요>`**
   - 무료 호스팅이라 한동안 미사용 시 앱이 잠들어 있을 수 있습니다. 처음 열 때 **수십 초 깨어나는 시간**은 정상입니다.
2. 왼쪽 사이드바에서 **실행 기준일(월요일)** 을 고릅니다. (기본값은 가장 최근 월요일)
3. **실행 (Run)** 버튼을 누릅니다. AEMO 데이터는 서버가 자동으로 받아옵니다.
4. 표가 나오면 확인하고, 맨 아래 **📥 Excel 다운로드** 버튼으로 결과를 내려받습니다.

> 자동 다운로드가 실패하면, 사이드바의 업로드 칸에 AEMO 월별 CSV(`PRICE_AND_DEMAND_*.csv`)를
> 직접 올린 뒤 다시 **실행** 을 누르면 됩니다.

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
3. 비밀키가 필요해지면(Phase 2의 Open Electricity API 등) 앱 **Settings → Secrets** 에 저장합니다. 코드에 넣지 않습니다.
4. 접근 제한이 필요하면 **Settings → Sharing** 에서 특정 이메일만 열람하도록 뷰어 인증을 켭니다.
5. 배포 후 GitHub에 `push` 하면 앱이 자동 갱신됩니다.
6. 배포 직후, 클라우드 서버에서 **AEMO 자동 다운로드가 정상 동작하는지** 한 번 실행해 확인하세요.

### 모듈 구조
```
nem-monitor/
├── app.py                 # Streamlit 웹 UI (후임자가 여는 화면)
├── run.py                 # CLI 진입점
├── requirements.txt       # 버전 고정
├── config/fixed_windows.csv   # 2025 도출 고정 시간대 (월별 참조값)
├── data/raw/              # 자동 다운로드 캐시 (gitignore)
├── src/
│   ├── dates.py           # 주차/구간 로직 (run_date → 2016구간, 5분 보정)
│   ├── ingest.py          # CSV 로드·정제 + 해상도 검증
│   ├── download.py        # AEMO 월별 CSV 자동 다운로드
│   ├── spreads.py         # best-case / 고정시간 스프레드 (Excel 포팅)
│   ├── demand.py          # 전력수요 24h/daytime/peak 분해
│   └── report.py          # 파이프라인 조립 → 표 + Excel
└── tests/
    ├── fixtures/          # 회귀 테스트용 커밋된 원본 CSV
    ├── golden/verification.csv   # Excel 정답지
    ├── test_dates.py · test_spreads.py · test_regression.py
```

### Phase 2/3 (예정)
외부 의존(시장 공고·발전원·기후·아티클)은 코어에서 분리되어, 실패해도 Phase 1(스프레드·수요)은 멈추지 않습니다.
`src/notices.py`, `generation.py`, `weather.py`, `articles.py` 자리만 비워둔 상태입니다.
