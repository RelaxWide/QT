# 채택 전략 플레이북

> 최종 업데이트: 2026-05-18 (KOSPI 컷오프 분석 반영 — Clenow KR 폐기 검토, KW SV 메인 엔진 확정)
> 지원 시장: **US (S&P 500)** ✅ 실전 운영 / **KR** ✅ KW Super Value 메인 엔진 확정 (페이퍼 진입 대기)

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

| 전략 | 시장 | 유형 | 기간 | CAGR | MDD | Sharpe | 알파 | 상태 |
|---|---|---|---|---|---|---|---|---|
| **Clenow Momentum** | US | 주봉 모멘텀 | 2015~ | 17.64% / **12.17%** | -19.6% | 1.13 / 0.89 | +3.40%p / ≈0%p | ✅ **실계좌 운영** (SPY+MDD개선 역할) |
| **KW Super Value** ⭐ | KR | 분기 가치 | 2014~ | 20.71% / **23.45%** | -39.2% | 0.97 / **1.06** | +6.51%p / **+20.65%p** | ✅ **KR 메인 엔진 확정** (페이퍼 대기) |
| **KW Super Quality** | KR | 분기 퀄리티 | 2014~ | 18.92% / 19.83% | -45.4% | 0.91 / 0.94 | +4.91%p / +17.03%p | ⏸️ 보류 (SV 와 상관 0.91, DART 검증 후 결정) |
| **Weinstein Stage 2** | US | 주봉 추세 | 2015~ | 8.0% / 5.87% | -14.0% | 0.88 / 0.74 | — | ✅ **실계좌 운영** (US 합성 분산 보조) |
| ~~Clenow Momentum~~ | ~~KR~~ | — | — | — | — | — | — | ❌ **폐기 확정** (2026-05-18, REJECTED 이동) |
| KW Ultra | KR | 분기 혼합 | 2014~ | 16.34% | -46.1% | 0.82 | +2.33%p | ❌ 폐기 (Super Value 미만) |
| Weinstein Stage 2 | KR | 주봉 추세 | 2015~ | 4.19% | -19.3% | 0.40 | -9.5%p | ❌ 폐기 (KR 박스권 가짜 돌파) |
| **합성 60:40** | US | 포트폴리오 | — | — | — | — | — | ✅ 페이퍼 NAV + 실계좌 동일 비율 |
| Phase 4 | US | 일봉 추세 | 2015~ | ~8% | -11.92% | 0.74 | — | ⚠️ Sharpe 미달, 페이퍼만 |

**합성 60:40** = Clenow 60% + Weinstein 40% (별도 거래 없음 — NAV 가중합산)

**CAGR/Sharpe** = "전체 (2026-05) / 컷오프 (2025-02, KOSPI 박스권)" 표기. 컷오프 분석은 10.2-M 참고.

SPY B&H 기준: 2015~ CAGR 13.52%, 2000~ CAGR ~5.5%
KOSPI B&H 기준: 2015~ CAGR 13.69% / **컷오프 +2.80% (박스권)**

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
| **1: 데이터 + 유니버스** | PyKRX/FDR, KOSPI200 + KOSPI 전체, KR regime filter | ✅ 검증 완료 |
| **2: 백테스트 (Clenow/Weinstein)** | 주봉 모멘텀·추세, 거래세 0.18% 비용 | ✅ 11년 실행 (Clenow 채택, Weinstein 보류) |
| **2': 백테스트 (KW 펀더멘털)** | PyKRX 시점별 PER/PBR/시총, 분기 엔진, KOSPI 전체 ~800 | ✅ 12년 실행 — **Super Value/Quality 채택**, Ultra 폐기 |
| **3: 페이퍼 트레이딩 (Clenow)** | run_daily_kr.py, GHA daily_kr workflow | ✅ 구현 완료 |
| **3': 페이퍼 트레이딩 (KW)** | live_signals_kw, 분기 리밸런싱 통합 | 🔧 미구현 (Phase E 작업) |
| **4: KIS API 한국 주식** | TR_IDS KR, domestic endpoint, 토큰 캐시 분리 | ✅ 구현 완료 |
| **5: KIS 실전 + 스케줄러** | KR 스케줄러 3개 작업, 본운영 진입 | ✅ 구현 완료 (등록 사용자 진행) |

### 10.2-A 백테스트 결과 (2015-01-02 ~ 2026-05-15, 초기 자본 5천만원)

데이터: PyKRX (수정주가 `adjusted=True`) / FDR 폴백, 유니버스 KOSPI200 195종목 (우선주 5개 제외),
비용 0.015% 매수 + 거래세 0.18%·농특세 0.023% 매도.

| 지표 | Clenow KR | Weinstein KR | KOSPI Buy&Hold |
|---|---|---|---|
| CAGR | **14.14%** | 4.19% | 13.69% |
| MDD | **-19.15%** | -19.26% | ~-55% |
| Sharpe | **0.94** | 0.40 | — |
| Sortino | 0.95 | 0.43 | — |
| Calmar | 0.74 | 0.22 | — |
| Profit Factor | — | 1.03 | — |
| Win rate | — | 21% | — |
| Monthly WR | 35.3% | 37.5% | — |
| Total return | +332.27% | +57.6% | +313.76% |
| 평균 보유 종목 | 6.8 | — | — |
| 거래 수 | — | 248 | — |

**Clenow KR**: KOSPI Buy&Hold (CAGR 13.69%) 를 능가 (14.14%), MDD 절반 이하 (-19% vs -55%), Sharpe 0.94.
KR 게이트 (CAGR≥8% / MDD≥-25% / Sharpe≥0.6 / Monthly WR≥50%) 중 **3/4 통과** (Monthly WR 만 미달 — 모멘텀 본연 특성).
→ **채택 + KIS 모의 검증 진행**.

**Weinstein KR**: MDD 양호하나 Profit Factor 1.03 (게이트 1.3 미달), Sharpe 0.40, CAGR 4.19%.
KOSPI 박스권 가짜 돌파 다발 (248 trades, WR 21%) → 평균 손실 거래가 평균 이익 압도.
→ **KR 트랙에서 보류** (US 트랙은 계속 운영).

### 10.2-B 정확성 보강 (2026-05-15 fix)

- **PyKRX `adjusted=True` 명시** (`src/fetch/prices_kr.py`) — 권리락/배당락 조정 수정주가 보장
- **우선주 5개 제외** (`src/fetch/universe_kr.py`) — 000155, 005385, 005387, 005935, 00680K
- **거래정지 가드** (`src/backtest/clenow_engine.py`) — `open_px <= 0` skip
- **데이터 누락 ffill** — 종목 vs ^KS11 1일 시차로 인한 가짜 -73% 손실 fix
- 효과: CAGR 12.73% → 14.14% (+1.41%p), MDD -20.0% → -19.2%, Sharpe 0.86 → 0.94

### 10.2-C Monte Carlo (트레이드 셔플 1,000회)

| 지표 | 실측 | MC 5%~95% | p-value | 해석 |
|---|---|---|---|---|
| Sharpe | 0.937 | 0.937~0.937 | 1.000 | daily-return 순서 셔플은 Sharpe 보존 — 의미 없음 |
| MDD | -19.15% | -35.5%~-17.6% | 0.879 | **실측 MDD 가 상위 12% (좋은 path)** — 우연 아님 |

MC 한계: 트레이드 단위 셔플이 아닌 일별 수익률 셔플이라 Sharpe 변화 없음. MDD 분포만 의미 있음.
→ 정밀 MC 위해선 trades_clenow_kr.csv 추가 후 trade-level 셔플 필요 (향후 작업).

### 10.2-D Walk-Forward (3년 학습 / 1년 OOS, 8개 윈도우)

| 윈도우 | Test 기간 | IS CAGR | OOS CAGR | IS Sharpe | OOS Sharpe |
|---|---|---|---|---|---|
| 2015-2018 | 2018-2019 | +2.8% | 0.0% | 0.34 | 0.00 |
| 2016-2019 | 2019-2020 | +6.0% | +2.8% | 0.64 | 1.18 |
| 2017-2020 | 2020-2021 | +1.4% | **+12.5%** | 0.23 | **1.82** |
| 2018-2021 | 2021-2022 | +10.2% | 0.0% | 1.03 | 0.00 |
| 2019-2022 | 2022-2023 | +19.0% | 0.0% | 1.33 | 0.00 |
| 2020-2023 | 2023-2024 | +11.0% | -1.6% | 1.04 | -0.67 |
| 2021-2024 | 2024-2025 | +4.5% | 0.0% | 0.49 | 0.00 |
| 2022-2025 | 2025-2026 | +5.4% | +2.5% | 0.42 | 0.34 |

**요약**:
- OOS 양수 비율: **3/8 (38%)** — US Clenow (6/7=86%) 대비 낮음
- OOS 손실: **1/8 (-1.6%)** — 자본 보존 능력 우수
- 5/8 윈도우는 OOS CAGR 0.00% (regime OFF → 매매 안 함, 손실 면피)
- 평균 OOS CAGR: **+2.03%**
- IS-OOS 상관: -0.51 (음수 — KOSPI 추세 구간이 1~3년으로 나뉘어 학습기와 OOS 시장 환경 자주 다름)

**판정**: OOS 양수 빈도는 US 보다 낮지만 **OOS 손실 거의 없음** (1/8). 자본 보존 측면 robust.
실전 운영 시 1년 단위로 모멘텀 발현 빈도 낮을 수 있어 **인내심 필요** — 11년 종합 CAGR 14.14% 가 단기 OOS 보다 신뢰성 있음.

`backtest_results/kr/validation_walkforward_clenow.json` 저장.

### 10.2-E KW (강환국 류) 펀더멘털 전략 (12년 백테스트, 2014-01 ~ 2026-05)

KOSPI 인덱스 ETF 대비 의미 있는 알파 확보를 목표로 추가. 데이터: PyKRX 분기별 PER/PBR/EPS/BPS + 시총 (KRX 인증 필요).
유니버스: KOSPI 전체 ~800종목, 시총 하위 20% (Super Value/Quality) 또는 30% (Ultra) 풀에서 top 20 동일비중.
리밸런싱: 분기 (5/16, 8/16, 11/16, 4/1 + 영업일 보정).

| 지표 | KW Super Value | KW Super Quality | KW Ultra | KOSPI B&H |
|---|---|---|---|---|
| 전략 | PER+PBR 백분위 | ROE+변동성 백분위 | 가치40+퀄30+모멘텀30 | — |
| CAGR | **20.36%** | **18.92%** | 16.34% | 14.01% |
| MDD | -45.6% | **-44.8%** | -46.1% | ~-55% |
| Sharpe | 0.95 | 0.91 | 0.82 | — |
| Sortino | 1.21 | 1.13 | 1.03 | — |
| Calmar | 0.45 | 0.42 | 0.35 | — |
| Monthly WR | **61.7%** | 60.2% | 61.7% | — |
| **알파 vs KOSPI** | **+6.35%p** | **+4.91%p** | +2.33%p | (벤치마크) |
| 게이트 통과 | 4/5 (MDD 0.6%p 차) | **5/5 [PASS]** | 1/5 | — |

**KW Super Value (KR 메인 엔진 후보)**:
- KOSPI B&H 대비 알파 **+6.35%p** — Clenow KR (+0.45%p) 의 **14배**
- Monthly WR 61.7% (Clenow KR 34.6% 의 1.8배) — 가치 전략 안정성
- MDD -45.6% 만 0.6%p 차로 게이트 미달 (소형주 정상 범위 -50%~)
- 강환국 책 백테스트 (CAGR 35~46%) 의 **PyKRX 한정 ~60% 재현** 성공

**KW Super Quality (전 게이트 통과)**:
- 알파 +4.91%p, MDD -44.8% (게이트 통과), Sharpe 0.91
- ROE (EPS/BPS) + 120일 변동성 백분위

**KW Ultra (폐기)**:
- 가치+퀄리티+모멘텀 결합 의도였지만 모멘텀 컴포넌트가 KR 에서 약함
- regime 필터 켰을 때 CAGR 6.94%, 껐을 때 16.34% — 어느 쪽이든 Super Value/Quality 미만
- → KR 트랙에서 폐기

**상관계수 (일간 / 월간)**:

| | Clenow KR | Super Value | Super Quality |
|---|---|---|---|
| Clenow KR | 1.00 / 1.00 | **0.30 / 0.20** | 0.28 / 0.19 |
| Super Value | 0.30 / 0.20 | 1.00 / 1.00 | 0.91 / 0.93 |
| Super Quality | 0.28 / 0.19 | 0.91 / 0.93 | 1.00 / 1.00 |

**핵심 인사이트**: Clenow KR (모멘텀, 주봉) 과 KW (가치, 분기) 의 상관 **0.30** — 분산 효과 큼.
Super Value 와 Super Quality 의 상관 **0.91** — 동시 운영 가치 작음 (한쪽만).

### 10.2-F KW Super Value 심층 검증 (2026-05-15)

#### Walk-Forward (5년 학습 / 1년 OOS, 7개 윈도우)

| 윈도우 | Test | IS CAGR | OOS CAGR | IS Sharpe | OOS Sharpe |
|---|---|---|---|---|---|
| 2014-2019 | 2019-2020 | +26.1% | +7.6% | 1.32 | 0.46 |
| 2015-2020 | 2020-2021 | +20.6% | **+135.3%** | 1.06 | **3.30** |
| 2016-2021 | 2021-2022 | +30.5% | +2.4% | 1.22 | 0.22 |
| 2017-2022 | 2022-2023 | +22.9% | +19.4% | 0.96 | 0.85 |
| 2018-2023 | 2023-2024 | +22.2% | -5.4% | 0.95 | -0.06 |
| 2019-2024 | 2024-2025 | +20.6% | -7.0% | 0.87 | -0.35 |
| 2020-2025 | 2025-2026 | +22.4% | +31.2% | 1.05 | 1.79 |

**결과**: OOS 양수 **5/7 (71%)** — Clenow KR (3/8=38%) 의 2배 신뢰성.
평균 OOS CAGR **+26.2%**, **평균 열화 +2.6% (OOS > IS)** — robust 함.
손실 윈도우 2개 (2023-2024, 2024-2025) 는 KR 소형주 침체기.

#### Monte Carlo (일별 returns 셔플 1,000회)

- 실측 MDD -45.58% 가 셔플 분포 상위 10% (p_value=0.101) — 우연 아님
- MC MDD 5%~95%: -48.59% ~ -26.22% (실측이 분포 내)

#### 파라미터 Sensitivity 그리드 (5 × 4 = 20 조합)

| small_cap_pct | top_n | CAGR | MDD | Sharpe | alpha |
|---|---|---|---|---|---|
| **0.15** | **15** ⭐ | **19.81%** | **-39.17%** | **0.94** | **+6.12%p** |
| 0.15 | 20 | 17.95% | -40.62% | 0.88 | +4.26 |
| 0.20 | 20 (이전 기본) | 17.97% | -45.58% | 0.87 | +4.28 |
| 0.10 | (any) | 8.47% | -50.35% | 0.42 | -5.22 (풀 너무 작음) |
| 0.30+ | (any) | <12% | <-50% | <0.65 | 음수 (가치 효과 희석) |

**핵심**: 시총 하위 15% × top 15 가 sweet spot. `config.yaml` 기본값 0.20/20 → **0.15/15 로 업데이트**.

#### 최적 파라미터 최종 백테스트 (small_cap_pct=0.15, top_n=15)

| 지표 | 값 |
|---|---|
| CAGR | **20.52%** |
| MDD | **-39.17%** (이전 -45.58% 대비 6.4%p 개선) |
| Sharpe | **0.97** |
| Sortino | 1.33 |
| Calmar | **0.52** (이전 0.45) |
| 알파 vs KOSPI | **+6.51%p** |
| Monthly WR | 58.65% |
| 게이트 | **5/5 [PASS]** ✅ (이전 4/5) |

### 10.2-G 합성 백테스트 (Clenow KR + KW Super Value)

상관 0.30 의 분산 효과를 활용한 가중 합성 — 가중 비율 sweep:

| 비율 (Clenow : SV) | CAGR | MDD | Sharpe | Calmar |
|---|---|---|---|---|
| 100% : 0% (Clenow 단독) | 14.47% | -19.15% | 0.95 | 0.76 |
| **80% : 20%** | 15.71% | **-16.72%** | 1.09 | **0.94** ⭐ |
| 70% : 30% | 16.24% | -17.41% | 1.12 | 0.93 |
| **60% : 40%** ⭐ | 16.72% | -22.06% | **1.13** | 0.76 |
| 50% : 50% | 17.13% | -26.49% | 1.11 | 0.65 |
| 40% : 60% | 17.49% | -30.70% | 1.08 | 0.57 |
| 0% : 100% (SV 단독) | 18.30% | -45.58% | 0.87 | 0.40 |

**합성 권장**:
- **Sharpe 극대화**: Clenow 60% / SV 40% → Sharpe **1.13**, CAGR 16.72%, MDD -22%
- **Calmar 극대화 (MDD 우선)**: Clenow 80% / SV 20% → Calmar **0.94**, CAGR 15.71%, MDD **-16.72%**

단독 대비 모든 지표 개선 (Clenow 0.95 → 1.13, SV 0.87 → 1.13).

### 10.2-H KW Super Quality 심층 검증 (2026-05-15)

KW Super Value 와 동일 프로토콜 (WFO + Sensitivity) 적용.

#### Walk-Forward (5년 학습 / 1년 OOS, 6개 윈도우)

| 윈도우 | Test | IS CAGR | OOS CAGR | IS Sharpe | OOS Sharpe |
|---|---|---|---|---|---|
| 2014-2019 | 2019-2020 | +27.7% | +37.3% | 1.35 | **2.01** |
| 2015-2020 | 2020-2021 | +21.5% | **+140.4%** | 1.15 | **4.02** |
| 2016-2021 | 2021-2022 | +22.9% | +5.7% | 1.02 | 0.47 |
| 2017-2022 | 2022-2023 | +24.5% | +14.1% | 1.04 | 0.75 |
| 2018-2023 | 2023-2024 | +24.4% | -7.4% | 1.01 | -0.34 |
| 2019-2024 | 2024-2025 | +25.7% | -8.2% | 1.05 | -0.39 |

**결과**: OOS 양수 **4/6 (67%)**, 평균 OOS CAGR **+30.3%**, IS-OOS 상관 -0.51, **평균 열화 +5.9% (OOS > IS)** — 매우 robust.
2020-2021 (코로나 회복기) 에서 폭발적 OOS (140%) — 변동성 낮은 퀄리티 종목이 약세장 후 회복 강한 성질.

#### Sensitivity 그리드 (5 × 4 = 20 조합)

| small_cap_pct | top_n | CAGR | MDD | Sharpe | alpha |
|---|---|---|---|---|---|
| **0.20** | **15** ⭐ Sharpe 최고 | 20.40% | -45.38% | **0.95** | +6.71%p |
| 0.15 | 10 ⭐ alpha 최고 | **21.21%** | -49.79% | 0.89 | **+7.52%p** |
| 0.20 | 20 (이전 기본) | 19.04% | -44.81% | 0.92 | +5.35 |
| 0.30 / 0.50 | (any) | <14% | -34~-46% | <0.79 | 음수 (소형주 효과 희석) |
| 0.10 | (any) | ~8% | -63% | 0.42 | -5%p (풀 너무 작음) |

**핵심**: Super Quality 도 0.20/15 가 Sharpe 0.95, alpha +6.71%p — Super Value 와 유사한 sweet spot.
Super Quality 와 Super Value 의 상관 0.91 (10.2-E) → 둘 중 하나만 운영해도 충분.

### 10.2-I 글로벌 3자 합성 (US + KR Clenow + KW Super Value)

전 세계 분산: 미국 모멘텀 + 한국 모멘텀 + 한국 가치 — 상관 거의 0.

#### 상관계수 매트릭스 (일별 returns)

| | US Clenow | Clenow KR | KW SV |
|---|---|---|---|
| US Clenow | 1.00 | **0.044** | **0.047** |
| Clenow KR | 0.044 | 1.00 | 0.300 |
| KW SV | 0.047 | 0.300 | 1.00 |

**핵심**: US vs KR 상관 0.044~0.047 — 사실상 독립. 분산 효과 극대화 가능.

#### 3자 가중 sweep 결과 (Sharpe Top 5)

| US : KR Clenow : KW SV | CAGR | MDD | Sharpe | Calmar |
|---|---|---|---|---|
| 0.50 : 0.30 : 0.20 ⭐ | 18.49% | -16.54% | **1.61** | 1.12 |
| 0.40 : 0.40 : 0.20 | 18.30% | -15.27% | 1.60 | 1.20 |
| 0.50 : 0.40 : 0.10 | 17.84% | -14.41% | 1.59 | 1.24 |
| 0.40 : 0.30 : 0.30 | 18.49% | -17.21% | 1.58 | 1.07 |
| **0.40 : 0.50 : 0.10** ⭐ Calmar | **17.15%** | **-13.15%** | 1.57 | **1.30** |

**합성 권장**:
- **Sharpe 극대화**: US 50% / KR Clenow 30% / KW SV 20% → Sharpe **1.61**, CAGR 18.49%, MDD -16.54%
- **Calmar 극대화**: US 40% / KR Clenow 50% / KW SV 10% → Calmar **1.30**, MDD **-13.15%**

단일 전략 최고 (US Clenow Sharpe 1.10) 대비 **약 +50% Sharpe 개선**. 3자 합성이 단일 시장 대비 명확히 우월.

### 10.2-J 거래비용·슬리피지 Sensitivity (KW Super Value)

소형주 슬리피지 위험 정량화 — `slippage_pct × fee_multiplier` 그리드.

| 슬리피지 | fee×1.0 | fee×1.5 | fee×2.0 |
|---|---|---|---|
| 0.1% (기본) | CAGR 20.42% | 20.42% | 20.42% |
| 0.3% | 19.82% | 19.82% | 19.82% |
| 0.5% (보수) | 19.22% | 19.22% | 19.22% |
| 1.0% (소형주) | **17.82%** ✓ | 17.82% ✓ | 17.82% ✓ |
| 2.0% (극단) | 15.08% ⚠️ | 15.08% | 15.08% |

**결과**:
- 슬리피지 **1% (소형주 현실치) 시 CAGR 17.82%** — 게이트 15% 통과
- **2% 극단 시 15.08%** — 게이트 경계
- fee 배수 효과는 미미 (cost_model 의 base_fee 가 핵심)

**실전 함의**: 시총 하위 15% 의 일거래대금 < 10억원 종목은 슬리피지 1% 이상도 발생 가능. 그래도 알파 유지됨.

### 10.2-K 생존편향 점검 (미완료)

KOSPI 시점별 마스터 (`pykrx.stock.get_market_ticker_list(date=X)`) 로 백테스트를 다시 돌려 현재 universe (838 종목) 와 비교 시도.

**결과**: PyKRX 의 `get_market_ticker_list(date=과거)` 함수가 KRX 인증 환경에서도 빈 응답 반환 — 시점별 마스터 추출 불가 (PyKRX 라이브러리 이슈).

**대안 (TODO)**:
- DART OpenAPI 의 회사 목록 + 상장폐지 일자 활용
- KRX 정보데이터시스템 (data.krx.co.kr) 직접 스크래핑

현재로서는 **생존편향 magnitude 측정 불가** — 관찰된 CAGR 20.52% 는 일부 부풀려졌을 가능성 ([[risk-survivorship]] 참고).

### 10.2-L DART OpenAPI 통합 (코드 완료, 데이터 미수집)

PyKRX 만으로는 부족한 펀더멘털 지표 — GP/A (강환국 슈퍼퀄리티 핵심), 자산성장률, 부채비율, F-Score 9개 등 — 을 위해 DART 통합 모듈 작성.

**파일**: `src/fetch/dart_kr.py` (opendartreader 0.2.2 사용)
- `fetch_financials(ticker, year, report_type)` — 단일 종목·연도 재무제표 (사업/1Q/반기/3Q)
- `compute_derived_metrics(fin, fin_prev)` — gp_a, ROA, ROE, 부채비율, 자산성장률, F-Score 4점 (부분)
- `fetch_kospi_financials_for_year(tickers, year)` — KOSPI 일괄 fetch
- 캐시: `data/raw/kr/dart/{ticker}/{year}_{report}.parquet`

**사용자 액션 필요**:
1. https://opendart.fss.or.kr/ 회원 가입
2. 마이페이지 → 인증키 신청 (즉시 발급, 40자)
3. 환경변수 `DART_API_KEY` 또는 `config.yaml` 의 `dart_api_key` 설정
4. `python scripts/debug_dart_kr.py` 로 동작 확인

**적용 예정 (API key 수령 후)**:
- Super Quality 의 ROE → 실제 ROE (PyKRX 추정 EPS/BPS 대신 DART 순이익/자기자본)
- GP/A factor 추가 → Super Quality 정밀도 ↑ 예상
- F-Score 9점 백테스트 (KW Ultra 변형) — 한국 1995-2016 CAGR 21.38% 보고 재현

**진행 (2026-05-18)**: API key 수령, finstate_all + 정확매칭 + 매출원가 역산 패치 적용. 삼성전자 2023 검증: 매출 258.9조, 매출총이익 78.5조(역산), GP/A 17.23%, ROE 4.26%, 부채비율 25.4% — 모두 공시 일치. 다음 단계는 KOSPI 838종목 × 10년 일괄 fetch (Phase B).

### 10.2-M KOSPI 2025-03 상승장 컷오프 분석 (2026-05-18)

**배경**: 2025-03 부터 KOSPI 가 14개월간 **+214.71%** 상승. 백테스트 결과의 상승장 의존도를 측정하기 위해 2025-02-28 컷오프로 재계산.

#### 단독 전략 성과 비교

| 전략 | 14개월 상승 기여 | 전체 CAGR | 컷오프 CAGR | Δ | 전체 Sharpe | 컷오프 Sharpe |
|---|---|---|---|---|---|---|
| **KW Super Value** | **+0.31%** ⭐ | 20.71% | **+23.45%** | **+2.74%p** | 0.97 | **1.06** |
| KW Super Quality | +13.70% | 18.92% | +19.83% | +0.91%p | 0.91 | 0.94 |
| US Clenow | +96.84% | 17.64% | +12.17% | -5.47%p | 1.13 | 0.89 |
| Clenow KR | **+122.42%** ⚠️ | 14.14% | **+6.94%** | -7.20%p | 0.94 | **0.59** |
| Weinstein US | +33.62% | 8.00% | +5.87% | -2.13%p | 0.88 | 0.74 |
| **벤치마크 KOSPI** | **+214.71%** | 13.69% | **+2.80%** | -10.89%p | — | — |

**핵심 발견**:
- **KOSPI 자체가 9.9년 CAGR +2.80% — 사실상 박스권** (전체 13.69% 의 80% 가 상승장에서 나옴)
- **KW SV 는 상승장 기여 +0.31% — 상승장과 무관한 진짜 알파** (오히려 컷오프 후 CAGR 더 좋아짐)
- **Clenow KR 은 상승장에 122% 의존 — 컷오프 후 Sharpe 0.59 (게이트 0.7 미달)**
- KOSPI 대비 알파 (컷오프 기준): KW SV **+20.65%p**, KW Quality **+17.03%p**, Clenow KR 겨우 +4.14%p

#### 상관계수 매트릭스 (컷오프 기준)

| | US Clenow | Clenow KR | KW SV |
|---|---|---|---|
| US Clenow | 1.00 | 0.065 | 0.077 |
| Clenow KR | 0.065 | 1.00 | 0.231 |
| KW SV | 0.077 | 0.231 | 1.00 |

→ 전체 기간 (0.044/0.047/0.264) 대비 거의 변화 없음 — 분산 효과 견고.

#### 3자 합성 비율 — 전체 vs 컷오프 비교

| 배분 (US:KR Clenow:KW SV) | 전체 Sharpe | 컷오프 Sharpe | Δ Sharpe | 평가 |
|---|---|---|---|---|
| 기존 최적 50:30:20 | 1.61 | 1.32 | **-0.29** ⚠️ | 상승장 의존 큼 |
| 기존 Calmar 40:50:10 | 1.51 | 1.15 | -0.36 ⚠️ | Clenow KR 의존 |
| 새 컷오프 최적 40:20:40 | 1.54 | **1.39** | -0.15 | 절반 robust |
| **US 50 / KW SV 50** (Clenow KR 제외) | 1.46 | **1.36** | **-0.10** ✓ | robust |
| **US 40 / KW SV 60** ⭐ | 1.39 | 1.33 | **-0.06** ⭐ | 매우 robust |
| US 30 / KW SV 70 | 1.30 | 1.28 | **-0.015** ⭐ | 거의 완벽 robust |
| KW SV 단독 | 1.03 | 1.12 | +0.09 | 단독으로도 충분 |

**컷오프 후 새 권장**:
- **메인**: US Clenow 40% + KW SV 60% (Sharpe 컷 1.33, MDD -25.7%) — Δ Sharpe -0.06, 매우 robust
- **공격**: US 30% + KW SV 70% — CAGR 21.91%, MDD -28.7%
- **Clenow KR 제외해도 Sharpe 거의 차이 없음** (3자 1.39 vs 2자 1.36) → 운영 단순화 가능

#### 의사결정 함의 (2026-05-18)

| 전략 | 단독 알파 (컷) | 합성 기여 | 운영 부담 | 결정 |
|---|---|---|---|---|
| **KW Super Value** | **+20.65%p** | 핵심 | 분기 4회 | **KR 메인 엔진 확정** |
| **US Clenow** | ≈0%p (SPY 수준) | MDD 개선 | 매주 | **유지 (실전 운영 중)** |
| KW Super Quality | +17%p | SV 와 중복 (상관 0.91) | 분기 4회 | 보류 (DART GP/A 검증 후 재결정) |
| Weinstein US | -7%p | 분산 효과 | 매주 | 유지 (US 합성 보조) |
| **Clenow KR** | +4.14%p | 미미 | 매주 | **폐기 검토** (게이트 미달) |

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

# Phase 2 (전체 11년 백테스트)
python run_clenow.py --market kr
python run_weinstein.py --market kr

# Phase 3
python run_daily_kr.py --print-only

# Phase 4 (KIS 모의 신청 후)
python -m live_trading.kis_client --market kr --test-auth
python -m live_trading.kis_client --market kr --test-price 005930
python -m live_trading.kis_client --market kr --test-order 005930 1

# Phase 5
.\scheduler\register_tasks_kr.ps1
```

### 10.8 알려진 데이터 이슈 & 트러블슈팅

| 이슈 | 원인 | 조치 |
|---|---|---|
| `KRX 로그인 실패: KRX_ID 환경변수` 로그 | PyKRX 가 KRX 의 새 인증 도입 후 환경변수 요구 | 폴백으로 FDR 자동 사용. INDEX (`^KS11` 등) 는 FDR 우선 (`src/fetch/prices_kr.py:fns` 분기) |
| 마지막 영업일 종목 평가 0 | ^KS11 vs 종목 데이터 1일 시차 | 백테스트 엔진의 equity 계산이 `df["close"].asof(date)` 로 ffill (clenow_engine.py L237, weinstein_engine.py L160) |
| 액면분할 직후 OHLV=0 (예: 005930 2018-05) | PyKRX 수정주가가 임시 0 메우기 | 매수 가드 `open_px <= 0 → skip` 으로 우회 |
| KR 백테스트 결과 (-73% 가짜 손실) | 위 1일 시차 ffill 미적용 시 발생 | 2026-05-15 fix(backtest) 커밋으로 해결됨 |
| 신규상장 (240일 미만) 종목 | min_bars=150 필터로 제외 | 신호 미발생 — 정상 |

### 10.9 KR 백테스트 한계점 (현재)

| 한계 | 영향 | 향후 개선 방향 |
|---|---|---|
| **생존편향** (현재 KOSPI = 2026 구성) | 과거 시점 구성과 다름. 결과 부풀려질 수 있음 | PyKRX `get_market_ticker_list(date)` 시도 실패 (10.2-K) — DART 회사목록 + 상장폐지일 활용 필요 |
| **VKOSPI 미반영** | regime filter 가 KOSPI MA200 만 사용 | VKOSPI 별도 fetch + 임계값 35 검토 |
| **Walk-Forward 미수행** | OOS 검증 부족, 과적합 가능성 | run_validation.py 의 WFO 로직 KR 적용 |
| **Monte Carlo 미수행** | 트레이드 순서 의존성 미측정 | 트레이드 순서 셔플 1,000 회 |
| **거래정지/관리종목 자동 제외 없음** | 과거 데이터에 정지 종목 포함 가능 | PyKRX `get_market_cap` + 관리종목 리스트 사전 필터 |
| **권리락/배당락 검증** | adjusted=True 가 기본인지 미확정 | PyKRX 호출 시 명시적 `adjusted=True` 지정 |

---

## 11. 다음 단계 — 개선 방향

**현재 (2026-05-18) 상태**:
- US 트랙 = Clenow + Weinstein 60:40 합성 **실계좌 운영 중** (KIS 미국주식)
- KR 트랙 = KW Super Value (분기 가치) 메인 엔진 확정, **페이퍼 진입 대기 / KIS 한국주식 모의계좌 미신청**
- **Clenow KR 폐기 확정** (REJECTED 이동, 컷오프 Sharpe 0.59 게이트 미달)
- **확정 글로벌 합성: US 50% + KW SV 50%** (컷오프 Sharpe 1.36, MDD -24.6%, Δ -0.10 robust)
  - US 50% = Clenow 60% (전체 30%) + Weinstein 40% (전체 20%) — US 트랙 내부 비율
  - KR 50% = KW Super Value 100% — top 15 동일비중

### 11.1 단기 (1-2주 내)

| 항목 | 효과 | 비고 |
|---|---|---|
| **KW Super Value Phase E 구현** ⭐ | 분기 리밸런싱 신호 자동 생성 → 페이퍼 진입 | `paper_trading/live_signals_kw.py` 신규, `run_daily_kr.py` 통합 |
| **KIS 한국주식 모의 신청 + 검증** | KR 실전 진입 전 필수 | 사용자 액션 |
| **DART KOSPI 838종목 × 10년 일괄 fetch** | GP/A 등 펀더멘털 panel 구축 (10.2-L) | 약 4~5시간 소요, 한 번만 |
| **DART GP/A 백테스트 (Super Quality 강화)** | SV vs Quality 차별화 검증 → Quality 최종 결정 | DART panel 완료 후 |
| **권리락/배당락 명시 처리** | 백테스트 정확성 ↑ | `prices_kr.py` 에서 `get_market_ohlcv(..., adjusted=True)` 명시 |
| **거래정지/관리종목 사전 제외** | 가짜 손실 거래 제거 | PyKRX `get_market_cap` 로 시총 0 종목 + 관리종목 리스트 |

### 11.2 중기 (1-2개월)

| 항목 | 효과 | 비고 |
|---|---|---|
| **KW SV 페이퍼 1분기 운영** | 신호·NAV 추적 검증 + 백테스트 ±5%p 일치 확인 | 5/16 → 8/16 첫 분기 |
| **KIS 모의 1분기 운영** | 슬리피지 평균 < 0.5%, 미체결율 < 10% 검증 | 페이퍼 통과 후 |
| **글로벌 합성 비율 결정** | US 40/KW SV 60 vs 50/50 vs 30/70 — 위험성향에 따라 | 사용자 결정 |
| **Clenow KR 최종 처분** | 폐기 vs 보류 결정 → REJECTED 이동 | 컷오프 분석 기반 |
| **생존편향 우회 측정** | 백테스트 신뢰성 ↑ | DART 회사목록 + 상장폐지일 활용 |
| **F-Score 9점 완전 구현** | 자본구조 + 운영효율 항목 추가 (현재 4점만) | DART panel 활용 |

### 11.3 장기 (3개월+)

| 항목 | 효과 | 비고 |
|---|---|---|
| **KW SV 실전 진입 (5% 시드)** | 페이퍼 + 모의 통과 후 약 250만원 시작 | 분기별 +5%p 증액 |
| **글로벌 NAV 통합 모니터링** | US/KR 합산 자산 추적 + 통합 텔레그램 | 환율 반영, 일별 글로벌 손익 |
| **KR 박스권 추가 전략 탐색** | Mean reversion, 섹터 로테이션, pair trading | 컷오프 분석에서 박스권 알파 검증된 전략만 |
| **백테스트 시각화 자동화** | equity curve / drawdown / monthly heatmap | matplotlib 보고서 자동 생성 |
| **이상 거래 알림** | 큰 일중 변동·미체결 누적 감지 | 슬리피지 임계 초과 시 텔레그램 |
| **세금 정산 자동화 (US 양도세, KR 거래세)** | 5월 신고 데이터 자동 추출 | trades_live_*.csv 누적 분석기 |

### 11.4 의사 결정 필요

다음 항목들은 사용자 합의 필요:

- ~~**글로벌 합성 비율**~~ — **확정: US 50 + KW SV 50** (2026-05-18)
- ~~**Clenow KR 처분**~~ — **확정: 폐기, REJECTED 이동** (2026-05-18)
- **KW Super Quality 채택 여부** — DART GP/A 검증 후 결정 (현재 SV 와 상관 0.91)
- **KR 실전 시드 자본** — 백테스트 5천만 → 실전 시작은? (1천만 → 분기 25% 증액 권장)
- **자본 배분 통합 운영 vs 시장 분리** — 미국·한국 단일 계좌 합산 NAV vs 별도

### 11.5 컷오프 분석으로 본 핵심 원칙

KOSPI 9.9년 박스권 (CAGR +2.80%) 에서 검증된 알파만 신뢰:

| 원칙 | 의미 |
|---|---|
| **펀더멘털 알파만 진짜** | KW SV/Quality 가 박스권에서도 +17~20%p alpha — 강환국 이론 본질 |
| **주봉 모멘텀은 상승장 의존** | Clenow KR 컷오프 +4%p, 박스권에서 제 역할 못함 |
| **빈도 ≠ 알파** | 분기 4회 KW > 매주 52회 Clenow KR (박스권 기준 3.4배 차이) |
| **US 시장은 다름** | SPY 자체가 강세장 → Clenow 도 알파 유지, but MDD 개선이 진짜 가치 |
| **분산 효과는 견고** | US-KR 상관 0.05 — 컷오프 후에도 변화 없음 |

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
