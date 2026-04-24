# 고승률 전략 탐색 — 최종 결론 (v3)

> **상태**: 2026-04-22 — 탐색 종료. 개별주 평균회귀는 본 프로젝트의 게이트를 통과하지 못함.
> 이후 탐색은 `LATEST_STRATEGY.md`로 이관.

---

## 0. 결론 요약

| 경로 | WR | PF | MDD | Sharpe | 게이트 |
|---|---|---|---|---|---|
| Phase 4 (참고, 추세) | 36.77% | 1.83 | -11.2% | 0.89 | ✅ 통과 |
| IBS on S&P500 | 55.8% | 1.00 | -15.7% | 0.29 | ❌ |
| Alvarez MR on S&P500 (기본) | 61.4% | 1.11 | -22.1% | 0.72 | ❌ |
| Alvarez MR + RSI(2) exit + SPY>MA200 레짐 | 65.2% | 1.15 | -21.9% | 0.67 | ❌ |

**핵심 발견**: 3가지 서로 다른 평균회귀 변형을 S&P500 개별주에 적용했으나 어느 것도 `PF ≥ 1.3`, `MDD ≤ -15%`, `Sharpe ≥ 0.8` 기준을 동시에 통과하지 못했다. 파라미터 조정이 아닌 **구조적 한계**다.

---

## 1. 시도 이력

### 1.1 IBS (Internal Bar Strength) on S&P500

문헌 근거: Alexander(2013), QuantConnect 등. 단, 원 검증은 **SPY/QQQ/IWM 3개 ETF**였음.

- 진입: IBS < 0.20, close > MA200, 5일 저점 갱신, SPY > MA50 레짐
- 청산: close > 신호일 고가 → 다음날 시가 (close-based)
- 결과: WR 55.8%, PF 1.00, Sharpe 0.29 — `time exit 57%`, 엣지 소실

### 1.2 Alvarez Mean Reversion on S&P500

문헌 근거: Cesar Alvarez (AlvarezQuantTrading.com). Russell 1000 Monte Carlo 22% CAGR / 21% MDD.

- 진입: 3일 연속 lower lows, close > MA100, RSI(2) < 20, 지정가 = prev_close - 0.5×ATR(10)
- 청산 v1: close > prev_close → 당일 종가
- 청산 v2: RSI(2) > 70 → 당일 종가
- 손절: entry - 2.5×ATR(10)
- 레짐: SPY > MA200

결과 (v2, 최종):
- 4,443 거래, WR 65.2%, PF 1.15, MDD -22%, Sharpe 0.67
- avg_win_r **0.43** vs avg_loss_r **-0.69** (R:R 역전)
- `max_losing_streak 22` (레짐 필터에도 불구)

### 1.3 실패의 구조적 원인

**(a) 개별주 idiosyncratic risk**
ETF는 구성종목 쇼크가 분산되어 평균회귀 가정이 유지된다. 개별주는 어닝·M&A·애널리스트 하향 등 단일 이벤트가 mean reversion을 깨고 계속 하락시킨다. 그래서 `avg_loss_r > avg_win_r` 구조가 고착된다.

**(b) 연속 손실의 집중**
레짐 필터(SPY > MA200)로도 max_losing_streak이 22. 지수가 상승추세 중에도 개별주는 동조 하락하는 변동성 구간이 있고, 해당 구간에 포지션이 집중되면 연속 손절이 발생.

**(c) 원전략 자체가 우리 게이트 미달**
Alvarez 원본 실측: CAGR 22% / MDD 21% / Calmar ~1.05. 우리 게이트(MDD ≤ 15%)는 **원전략이 이미 넘지 못하는 수준**이다. 개별주로 확장·적용하면서 추가 악화.

---

## 2. 남긴 자산

### 2.1 재사용 가능한 코드

| 파일 | 설명 | 재사용성 |
|---|---|---|
| `src/indicators/ibs.py` | IBS 지표 계산 | ★★★ 독립 유틸 |
| `src/indicators/rsi.py` | RSI 지표 (Wilder EMA) | ★★★ |
| `src/indicators/atr.py` | ATR | ★★★ |
| `src/strategy/ibs_mean_reversion.py` | IBS 시그널 생성 | ★★ ETF용으로 재활용 가능 |
| `src/strategy/alvarez_mean_reversion.py` | Alvarez 시그널 | ★★ 동일 |
| `src/backtest/ibs_engine.py` | close-based exit 엔진 | ★★★ 다른 MR 전략에 재사용 |
| `src/backtest/alvarez_engine.py` | 지정가 체결 + RSI exit 엔진 | ★★★ 동일 |
| `run_ibs.py`, `run_alvarez.py` | 러너 | ★ 레퍼런스용 |

### 2.2 유지된 인프라 가치
- **지정가 체결 로직** (bar low ≤ limit): 다른 전략에서 슬리피지 통제에 유용
- **close-based exit 프레임워크**: intraday fill 대비 현실적인 체결 모델
- **레짐 필터 + max_losing_streak 측정**: 게이트 검증 표준화

---

## 3. 교훈 (다음 탐색 반영)

1. **원전략의 실측 성과가 우리 게이트를 충족하는지 먼저 확인한다.** 원본이 CAGR 22%/MDD 21%면 개별 구현이 그것보다 좋아질 가능성은 희박하다.
2. **ETF 검증 전략을 개별주로 확장하지 않는다.** 이슈의 분산 구조가 완전히 다르다.
3. **파라미터 조정 3회 이상 실패 시 즉시 중단.** 구조 문제는 튜닝으로 해결되지 않는다.
4. **고승률 단일 전략보다 저승률·고기대값 전략(Phase 4)이 총 성과에서 우세할 수 있다.** WR은 멘탈 보호 지표일 뿐 수익 지표가 아니다.

---

## 4. 향후

- 신규 전략 후보 탐색은 `docs/LATEST_STRATEGY.md`로 이관.
- 현재 운영 가능 전략: **Phase 4 단독** (모든 게이트 통과).
- 구현된 IBS/Alvarez 코드는 삭제하지 않음 — 향후 ETF 대상 변형에 재활용.
