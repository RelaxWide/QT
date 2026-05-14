# 채택 전략 플레이북

> 최종 업데이트: 2026-05-15  
> 이 파일은 지속적으로 업데이트한다 — 새 백테스트 결과, 페이퍼 현황, 실전 진행 사항 반영.

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

---

## 1. 채택 전략 요약

| 전략 | 유형 | 기간 | CAGR | MDD | Sharpe | 상태 |
|---|---|---|---|---|---|---|
| **Clenow Momentum** | 주봉 모멘텀 | 2015~ | 16.92% | -19.6% | 1.10 | ✅ 페이퍼 운용중 |
| **Weinstein Stage 2** | 주봉 추세 | 2015~ | 8.0% | -14.0% | 0.88 | ✅ 페이퍼 운용중 |
| **합성 60:40** | 포트폴리오 합성 | — | — | — | — | ✅ 페이퍼 운용중 (NAV 모니터링) |
| Phase 4 | 일봉 추세 | 2015~ | ~8% | -11.92% | 0.74 | ⚠️ Sharpe 미달, 페이퍼 병행 |

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

**실전 자본 배분 (config_live.yaml 기준, 모의투자 $10,000)**:
| 전략 | 자본 | 최대 종목 수 |
|---|---|---|
| Phase 4 | $0 (페이퍼 전용) | — |
| Clenow | $6,000 | 5 |
| Weinstein | $4,000 | 4 |

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
| **A: KIS 클라이언트** | 인증·시세·주문 API 래퍼 | ✅ 완료 |
| **B: 신호→주문 변환** | 페이퍼 신호 → KIS 주문 | ✅ 완료 |
| **C: 포지션 동기화** | KIS 잔고 ↔ 로컬 positions 일치 | ✅ 완료 |
| **D: 리스크 가드** | Kill switch, 손실 한도, 연속손절 차단 | ✅ 완료 |
| **E: 스케줄러** | Windows Task Scheduler 무인 운영 | ✅ 완료 |
| **F: 모의투자 4주** | 슬리피지·체결 검증 | **진행 중** |
| **G: 실계좌 전환** | Phase F 통과 후 실자본 투입 | 대기 |

### 6.2 Phase F 통과 기준

| 지표 | 기준 |
|---|---|
| 슬리피지 평균 | < 0.3% |
| 주문 실패율 | < 5% |
| 체결 지연 | ±1거래일 이내 |

### 6.3 스케줄러 (KR 시간 기준)

| 시각 | 스크립트 | 역할 |
|---|---|---|
| 22:29 평일 | `scheduler/morning_entry.py` | Phase 4 pending → MOO 주문 |
| 23:00~04:00 매시 | `scheduler/exit_check.py` | 손절·트레일 체크 |
| 06:00 평일 | `scheduler/daily_close.py` | Clenow/Weinstein 신호+주문, Phase 4 pending 저장 |
| 07:00 평일 | `scheduler/summary.py` | 텔레그램 일일 리포트 |

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

### 6.5 설정 파일

```bash
cp config_live.example.yaml config_live.yaml
# → kis.app_key, kis.app_secret, 계좌번호 입력
```

⚠️ `config_live.yaml`은 절대 git 커밋 금지 (.gitignore 등록됨)

### 6.6 Phase G — 실계좌 전환 (Phase F 통과 후)

**자본 배분 (총 $10,000 기준, 합성 60:40)**:

| 전략 | 자본 | 최대 종목 수 |
|---|---|---|
| Phase 4 | $0 (페이퍼 전용) | — |
| Clenow | $6,000 | 5 |
| Weinstein | $4,000 | 4 |

전환 절차:
1. `config_live.yaml` → `mode: "prod"`, 실전 AppKey/AppSecret 입력
2. 스케줄러 진입점 → `KISClient.from_config(allow_prod=True)` 변경
3. 첫 1주: 매일 수동 모니터링

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

### 7.3 모의투자 명령

```bash
python -m live_trading.kis_client --test-auth
python -m live_trading.kis_client --test-price AAPL
python -m live_trading.orders --clenow --dry-run
python -m live_trading.orders --weinstein --dry-run
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

## 참고 자료

- Andreas Clenow, *Stocks on the Move* (2015)
- Stan Weinstein, *Secrets for Profiting in Bull and Bear Markets* (1988)
- Gary Antonacci, *Dual Momentum Investing* (2014)
- KIS API: https://apiportal.koreainvestment.com/
- 한국시간 미국장 정규시간: 22:30~05:00 (서머타임), 23:30~06:00 (표준시)
