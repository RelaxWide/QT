# 신규 전략 후보 — 병렬 비교용 (2026-04-22)

> **목적**: Phase 4와 **병렬로 돌려가며 상호 비교**할 수 있는 최신 유명 전략 조사.
> 고승률 제약은 해제. 수익원 다양화·포트폴리오 관점 접근.
>
> **Phase 4 baseline**: WR 36.77% / PF 1.83 / Sharpe 0.89 / MDD -11.2%

---

## 0. 이전 탐색의 교훈

| 전략 | 결과 | 실패 이유 |
|---|---|---|
| IBS S&P500 | PF 1.00, Sharpe 0.29 | ETF→개별주 확장 오류 |
| Alvarez MR S&P500 | PF 1.15, Sharpe 0.67 | R:R 역전, 연속손절 22 |
| Faber TAA 5자산 | CAGR 3.4%, Sharpe 0.53 | 2015-2026 기간 효과 (US 독주) |
| 52주 신고가 S&P500 | **PF 2.46, Sharpe 0.95**, WR 26% | WR 제외 전부 통과, Phase 4와 수익원 중복 |

**핵심 원칙 재확인**:
1. ETF 검증 전략은 ETF에 그대로 적용 (개별주 확장 금지)
2. 2015-2026 기간에서는 **US 주식 비중이 높은 전략이 우세**
3. WR보다 **Sharpe·PF**가 총 성과 지표로 신뢰성 높음
4. Phase 4와 **수익원(로직)이 구조적으로 다른 전략**만 보완재 가치

---

## 1. 카테고리별 후보 분류

### A. 평균회귀 — 지수 ETF 한정 (개별주 확장 금지)

Phase 4(추세 돌파)와 **완전 반대 수익원**. 지수 ETF에만 적용.

#### A1. ★ Connors Double Seven (SPY/QQQ/IWM)
- **실측**: 1993년부터 154트레이드, **WR 82.5%, PF 2.58, Sharpe 1.4**, 평균 +1.18%
- **규칙**:
  - 진입: close > MA200 AND close = min(close[-7:])  (7일 최저 종가)
  - 청산: close = max(close[-7:])                       (7일 최고 종가 도달)
- **왜 유망한가**:
  - 우리 IBS 실패가 **개별주 적용**이 원인 → ETF 한정 시 문제 없음
  - 규칙이 3줄. 과적합 위험 극소
  - Phase 4와 완전 반대 신호 (하락 중 매수 vs 상승 추세)
- **구현 난이도**: ★ (1시간)

#### A2. Connors R3 (SPY)
- **실측**: PF **3.37**, CAGR 낮음(2.69%)
- **규칙**: close > MA200 AND RSI(2) 3일 연속 하락 AND 첫날 RSI<60, 마지막날 RSI<10
- **판단**: PF는 극상이나 트레이드 빈도 매우 낮음 → Double Seven의 **백업** 용도

#### A3. Connors RSI(2) < 10 on SPY/QQQ
- **실측**: 2015-2025 out-of-sample **엣지 약화**(HFT 경쟁) 그러나 여전히 프로핏
- **판단**: Double Seven보다 신호 많음. A1 실패 시 차선.

---

### B. 자산 로테이션 — 매크로 레짐

Phase 4와 **타임프레임·유니버스 모두 다름**. 월 단위.

#### B1. ★ Monthly Momentum ETF Rotation (SPY/EEM/TLT)
- **실측**: 1995년부터 **CAGR 11%**, 2022년만 타격
- **2024**: +15.22%, **2025 YTD**: +3.20%
- **규칙**: 매월 말 SPY/EEM/TLT 중 **직전 1개월 수익률 최고** 자산 100% 보유 (1개 롱)
- **왜 유망한가**:
  - 신호 단 3자산. 로테이션 1개월 1회.
  - Phase 4는 개별주 일간, 이것은 매크로 월간 → 거의 무상관
  - Faber TAA보다 단순 (자산 5개 → 3개) 그러면서 모멘텀 추가
- **구현 난이도**: ★ (Faber 엔진 재활용)

#### B2. Risk Parity Dow 30 (20일 변동성 역가중)
- **2024 실측**: 연 15.6%, Sharpe **1.57**, 변동성 9.9% (vs 동일가중 11.5%/Sharpe 1.07)
- **규칙**:
  - Dow 30 편입
  - 매월 각 종목 20일 변동성 기반 역가중 (σ↑ → 비중↓)
- **판단**: 구현 비용 ★★, 지수편입 데이터 필요. B1 통과 시 추후 고려.

---

### C. 패턴 돌파 — Phase 4 보완

Phase 4와 수익원 유사하나 **종목 선별 기준이 근본적으로 다름**.

#### C1. ☆ Minervini VCP (Volatility Contraction Pattern)
- **근거**: Mark Minervini US Investing Championship 2x 우승, 2021년 **감사받은 +334%**
- **규칙 (단순화)**:
  - 수축 단계: 3-4번의 풀백, 각 풀백의 범위가 이전의 50% 이하
  - 볼륨: 풀백 시 감소, 돌파 시 급증
  - 추세: MA50 > MA150 > MA200 (Stage 2)
- **판단**: 규칙 정량화 어려움(시각 패턴). Phase 4와 잠재적 중복.
- **구현 난이도**: ★★★ (VCP 탐지 알고리즘 복잡)

#### C2. NR7 (Narrow Range 7) 돌파
- **SPY 1993-현재**: CAGR 7.1%, MDD 26%, 트레이드당 0.27% → **약한 엣지**
- **판단**: 엣지 너무 약함. **제외**.

---

### D. Gap 관련 전략 (인덱스 ETF)

#### D1. ☆ SPY Gap Fill (평균회귀)
- **실측**: S&P 갭 채움 비율 **60%** (최근 6개월), 작은 갭(0-0.19%)은 **89-93%**
- **규칙**: SPY가 전일 종가 대비 -0.15% ~ -0.6% 갭다운 시 시가 진입, 갭의 75% 채움 목표
- **판단**: 엣지 작음. 수수료·슬리피지 후 미미할 가능성.

---

### E. 옵션·선물 (스코프 외)

- **Bull Put Spread 30델타 SPY**: WR 70%, 수익률 +39%. 옵션 인프라 필요.
- **VRP 옵션 매도**: Sharpe 0.7. 테일 리스크 극단.
- **CTA Trend Following 선물**: 2025년 YTD **-9.3%**. 전통 트렌드 부진.

---

## 2. 우선순위 매트릭스

| 후보 | 게이트 통과 확률 | Phase 4 독립성 | 구현 비용 | 추정 기간 | 종합 |
|---|---|---|---|---|---|
| **A1. Connors Double Seven** | 높음 | 최고 | 매우 낮음 | 1h | **★★★★★** |
| **B1. Monthly ETF Rotation** | 중간-높음 | 최고 | 낮음 | 2h | **★★★★★** |
| A3. Connors RSI(2) | 중간 | 최고 | 낮음 | 1h | ★★★★ |
| A2. Connors R3 | 낮음(샘플↓) | 최고 | 낮음 | 1h | ★★★ |
| B2. Risk Parity Dow 30 | 중간 | 높음 | 중간 | 4h | ★★★ |
| C1. Minervini VCP | 불확실 | 낮음 | 높음 | 8h+ | ★★ |
| D1. SPY Gap Fill | 낮음 | 최고 | 낮음 | 1h | ★★ |
| C2. NR7 | 낮음 | 중간 | 낮음 | — | 제외 |
| 옵션·선물 | — | — | 매우 높음 | — | 유예 |

---

## 3. 권고 구현 플랜

### Step 1: **Connors Double Seven** (A1) — 1시간
- SPY + QQQ + IWM 3개 ETF
- 우리의 IBS 엔진 재활용 (close-based exit, 매우 단순)
- 예상: WR 75-85%, Sharpe > 1.0, 트레이드 수 적음(~50-100건)

### Step 2: **Monthly ETF Momentum** (B1) — 2시간
- SPY/EEM/TLT 3자산, 월말 1개월 수익률 랭킹
- Faber 엔진 재활용
- 예상: CAGR 8-12%, MDD -15%, Sharpe 0.8-1.2

### Step 3: 통과 전략을 Phase 4와 **합성 포트폴리오** 시뮬레이션
- 자본 배분: Phase 4 60% + 신규전략 40% (or 50/50)
- 합성 equity curve → Sharpe·MDD 측정
- **목표**: 단독 전략 대비 Sharpe 개선, MDD 감소

---

## 4. 평가 프레임워크 (신규 제안)

기존 WR 중심 게이트는 평균회귀·로테이션 전략에 불리. **전략 유형별 게이트** 재정의:

### Type 1: 평균회귀 (MR) — A 계열
| 기준 | 통과선 |
|---|---|
| 트레이드 수 | ≥ 50 |
| Win rate | ≥ 70% |
| Profit Factor | ≥ 1.8 |
| MDD | ≥ -12% |
| Sharpe | ≥ 1.0 |

### Type 2: 자산 로테이션 — B 계열
| 기준 | 통과선 |
|---|---|
| CAGR | ≥ 7% |
| MDD | ≥ -20% |
| Sharpe | ≥ 0.7 |
| 월별 WR | ≥ 55% |

### Type 3: 추세·돌파 — 기존 유지
| 기준 | 통과선 |
|---|---|
| Win rate | ≥ 35% |
| Profit Factor | ≥ 1.5 |
| MDD | ≥ -15% |
| Sharpe | ≥ 0.8 |

---

## 5. 참고 자료

**연구·백테스트 사이트**:
- [QuantifiedStrategies.com — 200 Trading Strategies](https://www.quantifiedstrategies.com/trading-strategies-free/) — 200+ 전략 백테스트
- [Quantpedia](https://quantpedia.com/) — 870+ 학술·실무 전략 DB
- [Allocate Smartly](https://allocatesmartly.com/) — TAA 전략 실시간 추적

**전략별 출처**:
- [Connors Double Seven](https://www.quantifiedstrategies.com/larry-connors-double-seven-strategy-does-it-still-work/) — WR 82.5%, PF 2.58
- [Connors R3](https://www.quantifiedstrategies.com/larry-connors-r3-strategy/) — PF 3.37
- [Connors RSI(2)](https://www.quantifiedstrategies.com/rsi-2-strategy/) — 2015-2025 out-of-sample
- [Monthly Momentum ETF Rotation](https://www.quantifiedstrategies.com/a-monthly-momentum-strategy-in-etfs/) — CAGR 11%
- [Minervini SEPA/VCP](https://quantstrategy.io/blog/sepa-strategy-explained-mastering-trend-following-with-mark/)
- [SPY Gap Fill Stats](https://tradethatswing.com/sp-500-spy-es-gap-fill-strategy-and-statistics/) — 60% fill rate
- [Risk Parity 2024 Backtest](https://tradewiththepros.com/risk-parity-trading-strategies/) — Sharpe 1.57
- [Pairs Trading Cointegration (2025)](https://link.springer.com/article/10.1057/s41260-025-00416-0)
- [Man Group Trend Following Review](https://www.man.com/insights/is-this-time-different)
- [Top Traders Unplugged Trend Report 2025-04](https://www.toptradersunplugged.com/trend-following-performance-report-april-2025/)
