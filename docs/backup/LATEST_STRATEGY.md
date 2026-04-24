# 신규 전략 탐색 (v1)

> **작성일**: 2026-04-22
> **목적**: 고승률 평균회귀 경로(→ `WIN_RATE_STRATEGY.md`) 탐색 실패 이후, 전문 투자자·최신 연구·커뮤니티에서 **실전 검증된 전략**을 재조사.
> **선정 기준**: 10년+ 실측, 공개 백테스트, Phase 4와 수익원이 다를 것(상관관계 낮음), 게이트 통과 가능성.

---

## 0. 전제 재정비

현재 QT 프로젝트 상태:
- **Phase 4 통과**: WR 36.77% / PF 1.83 / Sharpe 0.89 / MDD -11.2% (추세추종 + 구름 지지 + SPY RS)
- **보완 전략 부재**: Phase 4는 추세장 수익. 횡보·급락후 반등·섹터 로테이션 수익원 없음.

따라서 **Phase 4와 상관관계가 낮은 독립 수익원**이 필요하며, 고승률일 필요는 없다.

---

## 1. 후보 전략 (우선순위순)

### ★ 후보 A: Meb Faber 10-Month TAA (섹터·자산 로테이션)

**한 줄 요약**: 자산별 10개월 이동평균 위에 있으면 보유, 아니면 현금. 월 1회 리밸런싱.

**근거**:
- Meb Faber "A Quantitative Approach to Tactical Asset Allocation" (2006, 누적 다운로드 300k+)
- Allocate Smartly 실시간 추적: **2020-2025 지속적 초과수익**, 2025년 실제 계정 +20.25%
- 장기 실적: 주식 유사 수익, 채권 유사 변동성/MDD

**규칙 (단순 버전, SPY/EFA/GLD/IEF/VNQ 5자산)**:
```
매월 말:
  for asset in universe:
    if close > SMA(10개월):
      hold (1/5 비중)
    else:
      cash
```

**왜 QT에 적합한가**:
- ETF 대상 → 개별주 idiosyncratic risk 없음 (이전 실패의 반면교사)
- **연 거래 ~1.5회** → 슬리피지·세금 영향 극소
- Phase 4와 완전히 다른 수익원 (월간 트렌드 vs 일간 돌파)
- 구현 난이도 ★ (매우 단순)

**예상 성과 (문헌)**: CAGR 9-11% / MDD -15% / Sharpe 0.8

**리스크**: Whipsaw (2015, 2018, 2022). 단일 신호 의존 → 추가 필터(breadth momentum) 검토 필요.

**참고**: [Meb Faber Timing Model](https://mebfaber.com/timing-model/), [Allocate Smartly](https://allocatesmartly.com/)

---

### ★ 후보 B: 52-Week High Breakout with Volume (개별주)

**한 줄 요약**: 52주 신고가 + 20일 평균거래량 150% 이상 돌파 → 매수.

**근거**:
- George & Hwang (2004) "52-Week High and Momentum Investing" — 원논문
- Journal of Financial Markets (2023) 후속 검증: **거래량 ≥ 150% 조건 시 72% 확률로 31일간 평균 +11.4%**
- 2024년 TSLA(9/12, +4.3%/볼륨 +278%), AAPL(3월, 볼륨 +217%) 등 실제 사례

**규칙**:
```
진입: close = max(close[-252:]) AND volume > avg_volume(20) × 1.5
청산: 25% 트레일링 or close < MA200
예외: 1월 진입 금지 (역사적 부진)
```

**왜 QT에 적합한가**:
- Phase 4의 Donchian 돌파와 유사하나 **기간이 52주**로 훨씬 엄격 → 신호 희소, 품질 高
- 거래량 필터가 있어 가짜 돌파 배제
- 예상 WR ~68% (Phase 4의 37%보다 고승률 → 멘탈 보호 보완재)

**리스크**: 신호 빈도 낮음 → 1년에 기회 20-40회 수준. 신호 없을 때 현금 보유.

**구현 난이도**: ★★ (Phase 1 breakout 엔진 재활용 가능)

**참고**: [Quantpedia — 52-Weeks High Effect](https://quantpedia.com/strategies/52-weeks-high-effect-in-stocks), [QuantifiedStrategies backtest](https://www.quantifiedstrategies.com/52-week-high-strategy/)

---

### ☆ 후보 C: Andreas Clenow "Stocks on the Move" (월간 모멘텀 로테이션)

**한 줄 요약**: 지수 상승추세 시 모멘텀 상위 종목 동일비중 보유, 매주 리밸런싱.

**근거**:
- Andreas Clenow, 前 Lynx Capital Partners CIO, 실무자 책 (2015)
- 2009-2024 백테스트: **CAGR 8.79% / MDD -24%** (QuantConnect 포럼)
- 실무자 실제 운용 전력 있음

**규칙**:
```
매주 수요일:
  if SPY > SMA(200):
    rank = top 10% of S&P500 by annualized exp regression slope(90일)
    filter: 개별주 SMA(100) 위 + 90일 내 갭 ≥15% 없음
    position size = (자본 × 0.001) / ATR(20)
  else:
    cash
```

**왜 QT에 적합한가**:
- 수익원이 Phase 4와 유사(모멘텀)하나, **기간 스케일이 다름** (주간 리밸런싱 vs 일간)
- ATR 기반 사이징 → Phase 4와 일관된 리스크 프레임

**리스크**:
- MDD -24%는 우리 게이트(-15%) 초과
- Phase 4와 상관관계가 0.5-0.7 수준일 가능성 → 독립성 약함

**구현 난이도**: ★★★ (종목 풀 랭킹·주간 리밸런싱 엔진 필요)

**참고**: [Stocks on the Move](https://www.amazon.com/Stocks-Move-Beating-Momentum-Strategies/dp/1511466146), [QuantConnect 구현](https://www.quantconnect.com/forum/discussion/10493/)

---

### ☆ 후보 D: Gary Antonacci Dual Momentum GEM (매크로 자산)

**한 줄 요약**: US주식 vs 해외주식 vs 채권 중 최근 12개월 성과 최고를 절대모멘텀 조건 하에 보유.

**근거**:
- Antonacci "Dual Momentum Investing" (2014)
- 1974-현재 백테스트: CAGR 15% / MDD -17%, **연 거래 ~1.5회**
- 저자 웹사이트(optimalmomentum.com)에서 월간 업데이트 공개

**규칙**:
```
매월 말:
  us_ret = SPY 12개월 수익률
  intl_ret = VEU 12개월 수익률
  if max(us_ret, intl_ret) > T-bill(12M):
    hold max of (SPY, VEU)
  else:
    hold AGG (채권)
```

**왜 QT에 적합한가**:
- 극단적으로 단순, 3-4자산만 필요
- Phase 4와 완전 무상관 (매크로 레짐 스위치)
- 탐색비용 최소

**리스크**:
- 최근 10년 성과 둔화 (2015-2025 CAGR ~8%)
- 단일 신호(12개월) 의존

**구현 난이도**: ★ (50줄 이내)

**참고**: [Optimal Momentum](https://www.optimalmomentum.com/), [QuantifiedStrategies](https://www.quantifiedstrategies.com/dual-momentum-trading-strategy/)

---

### ☆ 후보 E: Keller Defensive/Bold Asset Allocation (DAA/BAA)

**한 줄 요약**: "카나리아 자산"(VWO·BND)의 모멘텀이 양수면 공격자산, 음수면 방어자산.

**근거**:
- Wouter Keller, Jan Willem Keuning (2018 DAA, 2022 BAA)
- Allocate Smartly 실시간 추적 중
- MDD 개선 설계 (VAA 진화형)

**왜 QT에 적합한가**:
- **Crash protection 명시 설계** — MDD 게이트 통과 가능성 高
- 월 1회 리밸런싱

**리스크**:
- 파라미터 다수 (T1/T2 임계값, B/O 비중) → 오버피팅 경계 필요

**구현 난이도**: ★★

**참고**: [SSRN BAA paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4166845), [Allocate Smartly](https://allocatesmartly.com/kellers-resilient-asset-allocation/)

---

### ☆ 후보 F: Volatility Risk Premium (VRP, 옵션 매도)

**한 줄 요약**: VIX 내재변동성 > 실현변동성 → SPX 풋 매도 or 델타헤지드 쇼트스트래들.

**근거**:
- CAIA 2024, Hedge Fund Journal 2024: **1990년 이후 평균 VRP +4pt, 2020Q1 이후 +6.5pt**
- Sharpe ~0.7, 전통자산과 낮은 상관

**왜 QT에 적합한가**: Phase 4와 완전 직교(변동성 프리미엄 수확).

**리스크 (매우 큼)**:
- 테일 리스크 극단 (역사적 손실 -800% 사례)
- 옵션 거래 인프라 필요 → **현재 QT 스코프 초과**

**판단**: **유예**. 현물 ETF 포지션이 안정화된 후 재검토.

---

### ☆ 후보 G: ML/Sentiment Hybrid (XGBoost + 뉴스 감성)

**근거**: 2025 hybrid AI trading system 연구, AMD/TSLA/META에서 45% 수익 사례.

**판단**: **유예**. 백테스트 환경 신뢰도 낮음 (과적합 위험), 데이터·인프라 비용 큼. 전통 전략 안정화 후 검토.

---

## 2. 우선순위 매트릭스

| 후보 | 게이트 통과 가능성 | Phase 4 독립성 | 구현 비용 | 데이터 비용 | 종합 |
|---|---|---|---|---|---|
| **A. Faber 10M TAA** | 높음 | 높음 | 매우 낮음 | 0 | **★★★★★** |
| **B. 52W High + Volume** | 높음 | 중간 | 낮음 | 0 | **★★★★☆** |
| D. Dual Momentum GEM | 중간 | 높음 | 매우 낮음 | 0 | ★★★★ |
| C. Clenow Stocks on Move | 낮음 (MDD) | 낮음 | 높음 | 0 | ★★★ |
| E. Keller DAA/BAA | 중간 | 높음 | 중간 | 0 | ★★★ |
| F. VRP (옵션) | — | 최고 | 매우 높음 | 옵션체인 | 유예 |
| G. ML Hybrid | 불확실 | 높음 | 매우 높음 | 뉴스API | 유예 |

---

## 3. 권고

**우선 구현 제안 — 2단계 접근**:

### Step 1: 후보 A (Faber 10M TAA) — 1-2일 작업
ETF 5-7개 대상, 단순·검증·저비용. Phase 4와 완전 독립. 통과 시 포트폴리오 최초의 **매크로 레짐 엔진** 확보.

### Step 2: 후보 B (52W High + Volume) — 3-5일 작업
개별주 활용하되, **Phase 4와 로직 충돌 시 signal merge** 규칙 설계 필요. Phase 1 breakout 엔진 상당 부분 재사용 가능.

Step 1이 게이트 통과 시 Step 2 병행 검증. 둘 다 실패 시 D(GEM)로 후퇴.

---

## 4. 탐색 중 재확인한 원칙

1. **ETF 대상 전략을 먼저**. 개별주는 idiosyncratic risk가 구조적 악화요인.
2. **원전략 실측이 게이트를 충족하는지 먼저 확인**. 원본 MDD > 우리 게이트면 구현해도 통과 불가.
3. **신호 빈도가 낮은 전략을 선호**. 연 수회 신호 전략이 일 단위 전략보다 백테스트 오버피팅 위험 낮음.
4. **Phase 4 상관관계**를 반드시 실측 (포트폴리오 관점). 개별 성과 통과해도 상관 0.9면 가치 없음.

---

## 5. 후속 작업 (대기)

- [ ] 사용자 결정: 구현 후보 선정
- [ ] 선정 전략 규칙 명세 작성 → 코드 구현
- [ ] 백테스트 → 게이트 검증
- [ ] Phase 4와 합성 포트폴리오 상관·효율 분석
- [ ] 통과 시 Paper trading → 실운용

---

## 출처

**서적**:
- Meb Faber, *"A Quantitative Approach to Tactical Asset Allocation"* (SSRN, 2006/2013)
- Andreas Clenow, *"Stocks on the Move"* (2015)
- Gary Antonacci, *"Dual Momentum Investing"* (2014)
- Wouter Keller & Jan Willem Keuning, *"Breadth Momentum and the Canary Universe: DAA"* (SSRN, 2018)
- George & Hwang, *"The 52-Week High and Momentum Investing"* (Journal of Finance, 2004)

**실시간 추적 플랫폼**:
- [Allocate Smartly](https://allocatesmartly.com/) — TAA 전략 실시간 백테스트
- [TuringTrader](https://www.turingtrader.com/) — 전략 포트폴리오
- [Quantpedia](https://quantpedia.com/) — 870+ 전략 DB

**2024-2025 현행 연구**:
- CAIA, "What is the Volatility Risk Premium?" (2024)
- Garfinkel·Hribar·Hsiao, PEAD top-decile SUE hedge portfolio (2024, +5.1%/3mo)
- Gómez-Martínez et al., "How Sentiment Indicators Improve Algo Trading" (SAGE, 2025)
