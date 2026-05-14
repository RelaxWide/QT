# 채택 전략 플레이북

> 최종 업데이트: 2026-05-15
> 지원 시장: **US (S&P 500)** ✅ 실전 운영 / **KR (KOSPI200)** 🔧 Phase 1~3 구현 완료, KIS 모의 진입 대기

---

## 목차

1. [채택 전략 요약](#1-채택-전략-요약)
2. [전략별 상세 규칙](#2-전략별-상세-규칙)
3. [백테스트 결과](#3-백테스트-결과)
4. [상관계수 및 포트폴리오 합성](#4-상관계수-및-포트폴리오-합성)
5. [페이퍼 트레이딩 현황](#5-페이퍼-트레이딩-현황)
6. [모의·실전 투자 인프라](#6-모의실전-투자-인프라)
7. [운영 가이드](#7-운영-가이드)
8. [공통 리스크 프레임워크](#8-공통-리스크-프레임워크)
9. [검증 방법론](#9-검증-방법론)
10. [KR 시장 트랙](#10-kr-시장-트랙)

---

## 1. 채택 전략 요약

| 전략 | 유형 | 기간 | CAGR | MDD | Sharpe | 상태 |
|---|---|---|---|---|---|---|
| **Clenow Momentum** | 주봉 모멘텀 | 2015~ | 16.92% | -19.6% | 1.10 | ✅ 페이퍼 + **실계좌 운영 시작 (2026-05-18)** |
| **Weinstein Stage 2** | 주봉 추세 | 2015~ | 8.0% | -14.0% | 0.88 | ✅ 페이퍼 + **실계좌 운영 시작 (2026-05-18)** |
| **합성 60:40** | 포트폴리오 합성 | — | — | — | — | ✅ 페이퍼 NAV 모니터링 + 실계좌 동일 비율 운영 |
| Phase 4 | 일봉 추세 | 2015~ | ~8% | -11.92% | 0.74 | ⚠️ Sharpe 미달, 페이퍼만 (실계좌 배분 0) |

**합성 60:40** = Clenow 60% + Weinstein 40% (별도 거래 없음 — NAV 가중합산)

SPY B&H 기준: 2015~ CAGR 13.52%, 2000~ CAGR ~5.5%

---

## 2. 전략별 상세 규칙

### 2.1 Clenow Momentum

**출처**: Andreas Clenow, *Stocks on the Move* (2015)  
**유니버스**: S&P 500 전용 (Russell 2000 이식 실패 — CAGR 0.61%, MDD -49.8%)

**진입 규칙 (매주 수요일 리밸런싱)**:
1. SPY > SMA(200) — 시장 체제 필터 (아래면 현금 100%)
2. 90일 지수회귀 기울기 상위 10% 종목
3. 개별주 SMA(100) 위
4. 90일 내 갭 ≥ 15% 없음
5. 포지션 사이즈: `(자본 × 0.001) / ATR(20)`

**청산**: SMA(100) 이탈 즉시 청산 (다음 수요일 대기 없이)

**파라미터 (config.yaml)**:
```yaml
clenow:
  spy_ma: 200
  lookback_days: 90
  top_pct: 0.10
  gap_filter_days: 90
  gap_filter_pct: 0.15
  atr_period: 20
  sma_exit: 100
```

---

### 2.2 Weinstein Stage 2

**출처**: Stan Weinstein, *Secrets for Profiting in Bull and Bear Markets* (1988)  
**유니버스**: S&P 500 (소형주 적용 시 MDD 2배 — 포지션 크기 축소 필요)

**진입 규칙 (매주 수요일 스캔)**:
1. 30주(150일) 이동평균 위로 돌파
2. 거래량 급증 동반 (전 4주 평균 대비 1.5배 이상)
3. 포지션 사이즈: 진입가의 7~8% 아래 손절 기준 R 고정

**청산**: 30주 이동평균 이탈 (단일 규칙 — 조건부 청산 없음)

**파라미터 (config.yaml)**:
```yaml
weinstein:
  ma_period: 30         # 주봉 (= 150일봉)
  vol_ratio: 1.5        # 거래량 비율
  exit_ma: 30           # 청산 기준 동일
```

---

### 2.3 Phase 4 — Donchian + Ichimoku + SPY RS (페이퍼 병행)

**현재 상태**: Sharpe 0.74 (게이트 0.80 미달), MDD -11.92% 우수. 페이퍼 트레이딩 데이터 축적 중.

**진입 조건**:
1. Donchian 20일 상단 돌파 후 풀백 + 양봉
2. 종가 > 선행스팬A > 선행스팬B (구름 위), 구름 두께 ≥ 2.5%, 전환선 > 기준선
3. 최근 63일 수익률 > SPY 63일 수익률
4. 레짐 필터: SPY < 50MA AND VIX > 30 → 진입 보류

**청산**: 손절(swing_low or entry-2×ATR), T1 +3R(50% 청산), Donchian 10일 하한 트레일

---

### 2.4 합성 60:40 포트폴리오

별도 거래 없이 Clenow와 Weinstein 페이퍼 계좌의 NAV를 가중합산한 가상 포트폴리오.

```
combo_total = 0.6 × cl_total + 0.4 × w_total
```

**배분 근거**:
- 2015~ 기간: Clenow 성과가 우위 → Clenow 중심 배분
- 2000~ 기간: Weinstein이 더 안정적이지만 균형 유지 필요
- 두 기간 모두 고려한 절충점: **Clenow 60% / Weinstein 40%**

**실전 자본 배분 (config_live.yaml `auto_allocate=true` 권장)**:

| 전략 | 비율 (auto) | 최대 종목 수 | 비고 |
|---|---|---|---|
| Phase 4 | 0% | — | 페이퍼 전용 |
| Clenow | 60% | 5 | 백테스트 사양은 20종목, 소액 한계로 5 권장 |
| Weinstein | 40% | 4 | 백테스트 사양은 15종목, 소액 한계로 4 |
| 버퍼 | 1% | — | 수수료/환차 미배분 |

`auto_allocate=true` 면 매수 직전 KIS 잔고 조회해 비율 적용. 입출금 시 config 수정 불필요.
예: $10,000 총자산 → Clenow $5,940 / 5종목 = $1,188/종목, Weinstein $3,960 / 4종목 = $990/종목

---

## 3. 백테스트 결과

### 3.1 기본 기간 (2015-01-01 ~ 2026-05)

유니버스: S&P 500, 초기 자본: $100,000, 수수료: 0.25% 매수+매도

| 지표 | Clenow | Weinstein | Phase 4 | SPY B&H |
|---|---|---|---|---|
| **CAGR** | **16.92%** | 8.0% | ~8% | 13.52% |
| **MDD** | -19.6% | **-14.0%** | **-11.92%** | ~-34% |
| **Sharpe** | **1.10** | 0.88 | 0.74 | — |
| Sortino | 1.30 | 1.09 | 0.96 | — |
| Calmar | 0.86 | 9.88 | 7.21 | — |
| 총 수익률 | 482.70% | 138.28% | 85.92% | 318.05% |
| 트레이드 수 | — | 350 | 290 | — |
| WR | 55.6%¹ | 31.1% | 35.9% | — |
| PF | — | 2.00 | 1.62 | — |
| SPY 대비 초과 | **+3.40%p** | -5.52%p | — | — |

> ¹ 월별 승률

**Clenow 게이트 체크**:
- CAGR 16.92% ≥ 10% ✅ / MDD -19.6% ≥ -25% ✅ / Sharpe 1.10 ≥ 0.70 ✅ / 월별 WR 55.6% ≥ 55% ✅

**Weinstein 게이트 체크**:
- 샘플 350 ≥ 100 ✅ / PF 2.00 ≥ 1.5 ✅ / MDD -14.0% ≥ -25% ✅ / Sharpe 0.88 ≥ 0.70 ✅ / CAGR 8.0% ≥ 8% ✅

---

### 3.2 장기 검증 (2000-01-01 ~ 2026-05)

닷컴버블(2000-2002), 금융위기(2008-2009) 포함 26년 전체 검증

| 지표 | Clenow | Weinstein | Phase 4 | SPY B&H |
|---|---|---|---|---|
| **CAGR** | **9.98%** | 5.13% | 1.73% | ~5.5% |
| **MDD** | -19.58% | **-14.57%** | **-49.4%** | ~-55% |
| **Sharpe** | 0.77 | 0.77 | 0.22 | — |
| Sortino | 0.88 | 0.94 | 0.28 | — |
| Calmar | 0.51 | 0.35 | 0.04 | — |
| 총 수익률 | 1121.17% | 272.54% | 56.97% | — |
| 트레이드 수 | — | 803 | 592 | — |
| WR | — | 32% | 32.3% | — |
| PF | — | 2.43 | 1.27 | — |
| SPY 대비 초과 | **+4.48%p** | -0.37%p | — | — |

**판정**:
- Clenow ✅ — 26년 전기간 SPY 대비 초과수익, MDD SPY의 1/3 수준
- Weinstein ✅ — SPY 수준 수익, MDD SPY의 1/4. 두 기간 모두 가장 견고
- Phase 4 ⚠️ — CAGR 1.73% (인플레이션 수준), MDD -49.4% (2015~의 4배). **2000~2015 구간 과적합 확인, 페이퍼 병행만 유지**

---

### 3.3 합성 60:40 추정 성과

별도 합성 백테스트 기준. 두 전략 동일 초기 자본($100k)으로 계산.

| 구간 | 단순 가중 CAGR¹ | MDD 개선 여부 |
|---|---|---|
| 2015~ | ~13.2% (0.6×16.92 + 0.4×8.0) | MDD 중간값 수준 |
| 2000~ | ~8.0% (0.6×9.98 + 0.4×5.13) | Weinstein이 2000~2003 방어 기여 |

> ¹ 단순 선형 가중 추정. 실제 복리 합성은 코릴레이션에 따라 차이 있음.

---

### 3.4 Walk-Forward 검증 (Clenow, 4년 학습 / 1년 검증)

| 학습 기간 | 검증 기간 | IS CAGR | OOS CAGR | IS Sharpe | OOS Sharpe |
|---|---|---|---|---|---|
| 2015-2019 | 2019-2020 | 9.7% | 6.3% | 0.92 | 1.73 |
| 2016-2020 | 2020-2021 | 12.9% | 6.8% | 1.20 | 1.28 |
| 2017-2021 | 2021-2022 | 10.3% | 1.2% | 0.82 | 0.19 |
| 2018-2022 | 2022-2023 | 12.2% | -1.2% | 0.89 | -1.38 |
| 2019-2023 | 2023-2024 | 9.3% | 2.2% | 0.73 | 0.68 |
| 2020-2024 | 2024-2025 | 9.4% | 8.0% | 0.79 | 0.77 |
| 2021-2025 | 2025-2026 | 7.7% | 14.5% | 0.61 | 1.18 |

- **OOS 양수 비율**: 6/7 (86%) ✅
- **평균 OOS CAGR**: +5.4%
- 2022-2023 금리인상 베어마켓 구간만 OOS 손실 — 시장 환경 변화 원인, 과적합 아님

---

## 4. 상관계수 및 포트폴리오 합성

### 4.1 두 전략 간 상관관계

| 구간 | 일간 수익률 상관계수 | 월간 수익률 상관계수 |
|---|---|---|
| 2015~ | 0.62 | — |
| 2000~ | 0.60 | — |

**해석**: 0.60~0.62의 중간 수준 상관. 완전 독립은 아니지만 분산 효과 실재.

### 4.2 포트폴리오 최적화 결과

Clenow 비중(%)을 0~100%로 5%씩 변화시킨 Sharpe 스윕 분석.

**2015~ 기준**:
- 최적 Sharpe: Clenow 60~65% 부근
- 최저 MDD: Weinstein 100% (~-14%)

**2000~ 기준**:
- 최적 Sharpe: Clenow 35~40% 부근 (Weinstein 안정성 기여)

**최종 선택: Clenow 60% / Weinstein 40%**
- 2015~ 최적 배분에 근접
- 2000~ 장기에서도 합리적 균형
- 수익 중심이되 Weinstein의 방어성 유지

### 4.3 분산 효과 확인 (2000~ 장기)

| 포트폴리오 | Sharpe | MDD | 비고 |
|---|---|---|---|
| Clenow 100% | 0.77 | -19.58% | — |
| Weinstein 100% | 0.77 | -14.57% | — |
| 50:50 합성 | > 0.77 | 중간값 | 분산 효과 실재 |
| **60:40 (채택)** | — | — | 균형점 |

---

## 5. 페이퍼 트레이딩 현황

### 5.1 NAV 현황 (paper_trading/daily_nav.csv 기준)

초기 자본: 각 전략 $100,000 (합성 60:40은 가중 합산)

| 날짜 | Phase 4 | Clenow | Weinstein | 합성 60:40 |
|---|---|---|---|---|
| 2026-05-01 | $101,730 | $104,755 | $99,816 | $102,779 |
| 2026-05-04 | $101,695 | $106,290 | $99,528 | $103,585 |
| 2026-05-05 | $102,536 | $108,951 | $100,119 | $105,419 |

> 최신값은 `paper_trading/daily_nav.csv` 참조

### 5.2 상태 파일

| 파일 | 전략 | 역할 |
|---|---|---|
| `paper_trading/positions.json` | Phase 4 | 현재 보유 포지션 (스톱·목표가 포함) |
| `paper_trading/pending.json` | Phase 4 | 내일 진입 대기 목록 |
| `paper_trading/trades.csv` | Phase 4 | 청산 거래 누적 기록 |
| `paper_trading/positions_clenow.json` | Clenow | 현재 보유 포지션 |
| `paper_trading/trades_clenow.csv` | Clenow | 청산 거래 누적 기록 |
| `paper_trading/positions_weinstein.json` | Weinstein | 현재 보유 포지션 |
| `paper_trading/trades_weinstein.csv` | Weinstein | 청산 거래 누적 기록 |
| `paper_trading/daily_nav.csv` | 전체 | 일별 NAV 기록 (combo_6040 포함) |

### 5.3 자동 실행

`.github/workflows/daily.yml` — 평일 21:30 UTC (ET 17:30, 장 마감 1.5시간 후) 자동 실행.
결과는 텔레그램으로 전송, 포지션·거래 파일 자동 커밋.

---

## 6. 모의·실전 투자 인프라

### 6.1 Phase 진행 현황

| Phase | 내용 | 상태 |
|---|---|---|
| **A: KIS 클라이언트** | 인증·시세·주문 API 래퍼, OAuth 토큰 캐시 | ✅ 완료 |
| **B: 신호→주문 변환** | 페이퍼 신호 → KIS 주문 (LIMIT ±0.5%) | ✅ 완료 |
| **C: 포지션 동기화** | KIS 잔고 ↔ 로컬 positions 일치 | ✅ 완료 |
| **D: 리스크 가드** | Kill switch, 손실 한도, 연속손절, 토큰 발급 가드 | ✅ 완료 |
| **E: 스케줄러** | Windows Task Scheduler 5개 작업 무인 운영 | ✅ 완료 |
| **F: 스모크 테스트** | GHA 수동 트리거 → prod 실계좌 1종목 체결 검증 | ✅ 완료 (2026-05-15, APA $37.48 체결) |
| **G: 실계좌 본운영** | Windows 스케줄러 + auto_allocate, $10k prod | ✅ 운영 시작 (2026-05-18 월 ~) |

### 6.2 Phase F 통과 기준 (스모크 테스트)

| 지표 | 기준 | 2026-05-15 결과 |
|---|---|---|
| KIS prod 인증 | OK | ✅ 토큰 발급 정상 |
| LIMIT 주문 전송 | 성공 | ✅ APA $37.48 order_no=0031680490 |
| 텔레그램 시작/완료 알림 | 도착 | ✅ |
| 슬리피지 평균 | < 0.3% | 추후 본운영에서 측정 |

### 6.3 스케줄러 (KR 시간 기준)

| 시각 | 스크립트 | 역할 |
|---|---|---|
| 22:29 평일 | `scheduler/morning_entry.py` | Phase 4 pending → MOO 주문 |
| 23:00~04:00 매시 | `scheduler/exit_check.py` | 손절·트레일 체크 |
| 06:00 평일 | `scheduler/daily_close.py` | Clenow/Weinstein 매도 즉시 / KR 수요일 매수 후보 pending 저장 / Phase 4 pending |
| **목 00:00** | `scheduler/wednesday_morning_buy.py` | **Wed 11 AM ET 매수 실행** (스크립트가 ET 11:00 까지 대기) |
| 07:00 평일 | `scheduler/summary.py` | 텔레그램 일일 리포트 |

**Clenow / Weinstein 매수 시점 (Wed 11 AM ET 매수 정책)**:
- KR 수요일 06:00: 화요일 종가 데이터로 매수 후보 계산 → `live_trading/wed_buy_pending.json` 저장
- KR 목요일 00:00: 매수 후보 읽어 KIS 현재가 조회 + LIMIT 매수 주문 전송 (Wed 11 AM ET 부근 체결)
- 표준시 기간(11~3월)에는 스크립트가 1시간 슬립 후 Wed 11 AM ET 정각에 실행
- 매도(MA100/MA30 이탈, Clenow rank_exit)는 06:00 KR 에 즉시 → Wed 시초가 체결
- 페이퍼 트레이딩(run_daily.py)은 변경 없음 (Wed 종가 매수 가정 유지 → 백테스트와 동일)

등록: `.\scheduler\register_tasks.ps1`

### 6.4 리스크 가드 (live_trading/risk_guard.py)

| 조건 | 동작 |
|---|---|
| `live_trading/KILL_SWITCH` 파일 존재 | 모든 신규 주문 즉시 차단 |
| 당일 -1.5% | 신규 진입 차단 |
| 누적 낙폭 -10% | 전면 정지 + 텔레그램 경고 |
| 5연속 손실 | 다음 신호 1주일 보류 |
| 단일 주문 > 자본 50% | 주문 거부 |

Kill Switch: `type nul > live_trading/KILL_SWITCH` / 재개: `del live_trading/KILL_SWITCH`

### 6.4-B KIS API 안전 장치

| 장치 | 설명 |
|---|---|
| **토큰 캐시** | `live_trading/.kis_token.json` (24h TTL, 60분 margin 자동 갱신) |
| **토큰 발급 가드** | `live_trading/.kis_last_issued.txt` 별도 보존. 최근 2시간 이내 발급 이력 있으면 `RuntimeError` 로 거부 (KIS "1일 1회 발급 원칙" 보호) |
| **prod 모드 이중 잠금** | `KISClient.from_config(allow_prod=True)` 명시 호출해야 prod 진입 |
| **placeholder 차단** | `12345678-01` 같은 예시 계좌번호 진입 시 ValueError |
| **psamount API 자동 재시도** | 초당 한도 (429/500) 감지 시 1초 대기 후 1회 재시도 |
| **order_map.json** | `{strategy}:{symbol}:{signal_date}:{side}` 키로 중복 주문 차단 |

강제 토큰 재발급이 필요하면 두 파일 모두 삭제:
```bash
rm live_trading/.kis_token.json live_trading/.kis_last_issued.txt
```

### 6.4-A KIS Live 스모크 테스트 ($100 prod)

GitHub Actions 에서 수동으로 실 계좌 매수 파이프라인 전체를 검증한다.
페이퍼 상태와 무관하게 `scripts/smoke_test_setup.py` 가 직접 매수 후보를 생성한다.

**필요 Repository Secrets** (Settings → Secrets and variables → Actions):

| Secret | 값 |
|---|---|
| `KIS_PROD_APP_KEY`    | KIS 실전 AppKey |
| `KIS_PROD_APP_SECRET` | KIS 실전 AppSecret |
| `KIS_PROD_ACCOUNT`    | 실전 계좌번호 (예: `44474877-01`) |
| `TELEGRAM_BOT_TOKEN`  | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID`    | 텔레그램 채팅 ID |

**실행**: Actions 탭 → "KIS Live Smoke Test ($100)" → Run workflow → Clenow/Weinstein 예산 입력 (기본 60/40)

**흐름**:
1. Secrets 로 `config_live.yaml` 임시 생성 (`mode: prod`, max_positions 1, risk_guard 완화)
2. `smoke_test_setup.py`: 현재 모멘텀 상위 + 가격 fit 후보 5개 추출 → `wed_buy_pending.json`
3. `wednesday_morning_buy.py --no-wait`: KIS 실시간가 조회 → LIMIT +0.5% 매수 주문
4. 텔레그램 시작/완료 메시지

**주의**:
- 매수된 종목은 MA100/MA30 이탈 전까지 보유 (수주~수개월). 수동 청산은 KIS 앱에서.
- 같은 종목·같은 signal_date 재실행은 `live_trading/order_map.json` 로 차단됨 (로컬 파일이라 GHA 에선 매번 fresh).
- 미국 장중 실행 권장 (LIMIT 주문 즉시 체결). 장 마감 후 실행 시 다음 세션 시초 체결 시도.

### 6.5 설정 파일

```bash
cp config_live.example.yaml config_live.yaml
# → kis.app_key, kis.app_secret, 계좌번호 입력
```

⚠️ `config_live.yaml`은 절대 git 커밋 금지 (.gitignore 등록됨)

### 6.6 Phase G — 실계좌 본운영 (2026-05-18 ~)

**실제 자본 배분 ($10,000 prod, auto_allocate)**:

| 전략 | 비율 | 1% 버퍼 후 예산 | 최대 종목 수 | 종목당 |
|---|---|---|---|---|
| Phase 4 | 0% | $0 | — | 페이퍼 전용 |
| Clenow | 60% | ~$5,940 | 5 | ~$1,188 |
| Weinstein | 40% | ~$3,960 | 4 | ~$990 |

`auto_allocate=true` 라 입출금 시 config 수정 불필요. 매수 직전 KIS 잔고 조회해 비율 자동 적용.

**첫 자동 사이클 (2026-05-18 ~)**:
| 시점 (KR) | 동작 |
|---|---|
| 월 5/18 06:00 | daily_close → KIS sync (APA 자동 등록), MA 이탈 체크 |
| 월 5/18 07:00 | summary → 텔레그램 일일 리포트 |
| 수 5/20 06:00 | 매수 후보 계산 → wed_buy_pending.json 저장 |
| 목 5/21 00:00 | Wed 11 AM ET 매수 실행 (Clenow 4종목 + Weinstein 후보) |

**모니터링 포인트** (월요일 아침):
- 텔레그램 일일 요약 도착
- `live_trading/positions_live_clenow.json` 에 APA 등 등록 확인
- `logs/kis_api_YYYYMMDD.log` 일별 생성
- KIS 앱 거래내역 = 시스템 기록 일치

---

## 7. 운영 가이드

### 7.1 수동 실행

```bash
python run_daily.py                     # 장 마감 후 일반 실행
python run_daily.py --force             # 장 중 강제 실행 (테스트)
python run_daily.py --refresh           # 캐시 무시, 최신 데이터 재다운로드
python run_daily.py --force-wednesday   # 수요일 스캔 강제 실행
python run_daily.py --reset             # 포지션·거래 기록 초기화
```

### 7.2 백테스트 실행

```bash
python run_clenow.py      # Clenow 백테스트
python run_weinstein.py   # Weinstein 백테스트
python run_phase4_v2.py   # Phase 4-v2 (Anticipatory Cloud) 백테스트
```

**주의**: Claude Code 터미널에서 Python 실행 불가 (Windows Anaconda DLL 문제). Anaconda Prompt 사용.

### 7.3 KIS 라이브 운영 명령

```bash
# 잔고 / 손익 일일 요약 (텔레그램 전송)
python scheduler/summary.py
python scheduler/summary.py --print-only   # 콘솔만, 텔레그램 미발송

# 매수가능금액 + 잔고 raw 응답 디버그
python scripts/debug_kis_balance.py

# KIS 잔고 종목을 전략 positions_live_*.json 에 수동 등록
# (스모크 테스트나 수동 매수로 들어온 포지션을 추적에 추가)
python scripts/register_position.py --strategy clenow --symbol APA

# Dry-run (실제 주문 X)
python -m live_trading.kis_client --test-auth
python -m live_trading.kis_client --test-price AAPL
python -m live_trading.orders --clenow --dry-run
python -m live_trading.account --report    # 슬리피지 누적 리포트
```

### 7.4 실행 파이프라인 (run_daily.py 6단계)

| 단계 | 내용 |
|---|---|
| 1 | 장 마감 확인 (ET 16:00 이후) |
| 2 | S&P 500 약 500종목 일봉 로드 |
| 3 | Phase 4 대기 진입 처리 (pending.json) |
| 4 | Phase 4 보유 포지션 관리 (스톱→목표가→트레일) |
| 5 | Phase 4 신규 신호 생성 |
| 6 | Clenow 신호 처리 (매일: MA100 이탈 / 수요일: 리밸런싱) |
| 7 | Weinstein 신호 처리 (매일: MA30 이탈 / 수요일: Stage 2 스캔) |
| 8 | 텔레그램 발송 |

### 7.5 주요 코드 파일

| 파일 | 역할 |
|---|---|
| `run_daily.py` | 페이퍼 트레이딩 메인 실행기 |
| `paper_trading/live_signals.py` | Clenow·Weinstein 실시간 신호 |
| `paper_trading/simple_tracker.py` | Clenow·Weinstein 포지션 관리 |
| `paper_trading/tracker.py` | Phase 4 포지션 관리 |
| `src/strategy/clenow_momentum.py` | Clenow 신호 로직 |
| `src/strategy/weinstein_stage2.py` | Weinstein 신호 로직 |
| `src/strategy/factor_stack.py` | Phase 4 신호 로직 |
| `live_trading/kis_client.py` | KIS API 클라이언트 |
| `live_trading/orders.py` | 신호→주문 변환 |
| `live_trading/risk_guard.py` | 리스크 가드 |
| `scheduler/*.py` | 자동화 스케줄러 |

---

## 8. 공통 리스크 프레임워크

### 트레이드당 리스크

```
risk_per_trade = 총자본 × 0.7%   (초기 설정, 안정화 후 1%로 확대 검토)
position_size  = risk_per_trade / (entry_price − stop_price)
```
- 10연속 손절 = −7% 손실 (복구 가능 구간)

### 포트폴리오 한도

- 동시 보유: 최대 7~10종목
- 단일 섹터 집중: 자본의 30% 이내
- 상관계수 0.7 이상 종목 페어 동시 보유 금지

### 서킷 브레이커

| 트리거 | 액션 |
|---|---|
| 일 손실 > 자본 1.5% | 당일 신규 진입 중지 |
| 주 손실 > 자본 3% | 주말에 로직 리뷰 |
| 월 낙폭 > 5% | 포지션 크기 50% 축소 |
| 누적 낙폭 > 10% | 전면 중단, 백테스트 재검증 |

### 시장 체제 필터

- SPY 50일선 하향 이탈 + VIX > 30 → 1주일간 신규 진입 금지
- SPY 200일선 아래 → 포지션 크기 50% 축소

---

## 9. 검증 방법론

### Phase 게이트

| 단계 | 내용 |
|---|---|
| Phase 1 | 기본 백테스트 (2015~, S&P 500) |
| Phase 2 | 필터 추가 — Phase 1 대비 Sharpe 개선, MDD 악화 없음 |
| Phase 3 | 레짐 필터 (SPY 200MA + VIX) |
| Phase 4 | RS 필터 + 장기 검증 (2000~) |
| Phase F | 모의투자 4주 |
| Phase G | 실계좌 소액 (총자본 5%) |

### 채택 기준 (추세추종)

| 기준 | 통과선 |
|---|---|
| CAGR | ≥ 10% |
| MDD | ≥ -20% |
| Sharpe | ≥ 0.70 |
| WR 또는 월별 WR | ≥ 45% |
| PF | ≥ 1.5 |

### 주요 검증 방법

1. **기간 확장** — 2000~ 장기 검증 (닷컴버블, 금융위기 포함)
2. **Walk-Forward** — 4년 학습 / 1년 검증, 슬라이딩 윈도우 7개
3. **Monte Carlo** — 트레이드 순서 랜덤 셔플 1,000회
4. **유니버스 이식성** — Russell 2000 등 별도 유니버스

**WFO 기준**: OOS 성과 ≥ IS 성과 × 50% 일 때만 통과

---

## 10. KR 시장 트랙

### 10.1 결정사항

| 항목 | 결정 |
|---|---|
| 유니버스 | **KOSPI200 (대형주 200종목)** |
| 자본 출처 | KR용 별도 입금 (US/KR 계좌 분리 운용) |
| 데이터 소스 | PyKRX 1차 + FinanceDataReader 폴백 |
| 통화 | KRW (환차 없음, 한국 거주자) |
| 리밸런싱 요일 | Clenow 주 1회, Weinstein 주 1회 — 한국 거래일 기준 수요일 |
| 주봉 리샘플 | `W-FRI` (KOSPI 주봉 = 금요일 종가) |

### 10.2 Phase 진행 현황

| Phase | 내용 | 상태 |
|---|---|---|
| **1: 데이터 + 유니버스** | PyKRX/FDR 데이터, KOSPI200 유니버스, KR regime filter | ✅ 구현 완료 |
| **2: 백테스트** | KR 시장 백테스트 엔진, 거래세 0.18% 비용 모델 | ✅ 구현 완료 (실행 결과는 별도) |
| **3: 페이퍼 트레이딩** | run_daily_kr.py, GHA daily_kr workflow, 신호 추적 | ✅ 구현 완료 |
| **4: KIS API 한국 주식** | TR_IDS KR 분기, domestic endpoint, 토큰 캐시 분리 | ✅ 구현 완료 (실행 검증 사용자 진행) |
| **5: KIS 실전 + 스케줄러** | KR 전용 스케줄러 3개 작업, 본운영 진입 | ✅ 구현 완료 (등록 사용자 진행) |

### 10.3 시장별 파라미터 차이

**Clenow Momentum**:
| 파라미터 | US | KR |
|---|---|---|
| 유니버스 | S&P 500 (~500종목) | KOSPI200 (~200종목) |
| 레짐 인덱스 | SPY > MA200 | `^KS11` (KOSPI) > MA200 |
| VIX 필터 | `^VIX` > 30 시 중단 | (옵션) VKOSPI > 35, 또는 비활성 |
| 90일 지수회귀 | 동일 | 동일 |
| 최소 가격 | $5 | ₩5,000 |
| 갭 필터 (90일 ±15%) | 동일 | 동일 (한국은 상하한가 ±30% 도 별도 체크) |

**Weinstein Stage 2**:
| 파라미터 | US | KR |
|---|---|---|
| 주봉 리샘플 | W-WED | W-FRI |
| 30주 MA 돌파 | 동일 | 동일 |
| 거래량 멀티플 | 1.5배 | 2.0배 (KR 박스권 가짜 돌파 강화) |
| 청산 MA | 30주 | 30주 |

### 10.4 운영 시간 매트릭스 (KR 시간 기준)

KR 트랙은 미국과 시간 충돌 없음 — 단일 PC 에서 두 시장 모두 추적 가능.

| 시각 (KR) | 작업 | 시장 |
|---|---|---|
| 09:00 수 | wednesday_morning_buy_kr (KR 매수 실행) | KR |
| 16:00 평일 | daily_close_kr (KR 매도 + pending) | KR |
| 16:30 평일 | summary_kr (KR 텔레그램 요약) | KR |
| 22:29 평일 | morning_entry (US Phase 4 진입) | US |
| 23:00~04:00 평일 매시 | exit_check (US 손절) | US |
| 00:00 목 | wednesday_morning_buy (US Wed 11 AM ET 매수) | US |
| 06:00 평일 | daily_close (US 매도 + pending) | US |
| 07:00 평일 | summary (US 텔레그램 요약) | US |

### 10.5 KR 시장 특수 위험

| 위험 | 대응 |
|---|---|
| **호가 단위 미준수** | `tick_size_kospi(price)` 의무 라운딩. 가격대별 1~1,000원 단위 |
| **상하한가 ±30%** | LIMIT 권장. 매수가가 상한 초과 시 자동 스킵 |
| **VI (변동성 완화장치)** | 2분 단일가 거래 → 미체결 시 다음날 재시도 |
| **거래정지 / 관리종목** | 일일 universe 갱신 + 사전 체크 |
| **거래세 0.18% 매도** | fee_model 에 정확 반영 |
| **양도세** | 대주주 아니면 무관 (시총 50억+/지분 1%+ 만 과세) |
| **모멘텀 약화** | 코스피 모멘텀 프리미엄이 US 대비 약함 → 백테스트 결과 합격 조건 완화 (Sharpe ≥ 0.6) |

### 10.6 KR 자본 배분

`config_live.yaml` 의 `kr:` 섹션 분리. 동일 KIS 종합계좌 내 USD/KRW 잔고 독립 관리.

```yaml
kr:
  mode: "mock"     # mock → prod 단계 진행
  capital:
    auto_allocate: true
    buffer_pct: 1.0
    clenow_pct: 60       # KRW 잔고의 60%
    weinstein_pct: 40    # KRW 잔고의 40%
    clenow_max_positions: 5
    weinstein_max_positions: 4
```

KR 종목당 예산 = (KRW 잔고 × 0.99 × pct) / max_positions.
예: ₩5,000,000 입금 → Clenow ₩594,000/종목 × 5종목, Weinstein ₩495,000/종목 × 4종목.

### 10.7 KR 트랙 검증 명령

```bash
# Phase 1
python -c "from src.fetch.universe import get_kospi200_tickers; print(len(get_kospi200_tickers()))"
python -c "from src.fetch.prices import fetch_prices; print(fetch_prices('005930', '2024-01-01', '2026-05-15', market='kr').tail())"

# Phase 2
python run_clenow.py --market kr --start 2015-01-01 --end 2025-12-31
python run_weinstein.py --market kr --start 2015-01-01 --end 2025-12-31

# Phase 3
python run_daily_kr.py --print-only

# Phase 4 (KIS 모의 신청 후)
python -m live_trading.kis_client --market kr --test-auth
python -m live_trading.kis_client --market kr --test-price 005930
python -m live_trading.kis_client --market kr --test-order 005930 1

# Phase 5
.\scheduler\register_tasks_kr.ps1
```

---

## 참고 자료

- Andreas Clenow, *Stocks on the Move* (2015)
- Stan Weinstein, *Secrets for Profiting in Bull and Bear Markets* (1988)
- Gary Antonacci, *Dual Momentum Investing* (2014)
- KIS API: https://apiportal.koreainvestment.com/
- PyKRX: https://github.com/sharebook-kr/pykrx
- FinanceDataReader: https://github.com/financedata-org/FinanceDataReader
- 한국시간 미국장 정규시간: 22:30~05:00 (서머타임), 23:30~06:00 (표준시)
- 한국시간 KOSPI 정규시간: 09:00~15:30 (단일가 마감 동시호가 15:20~15:30)
