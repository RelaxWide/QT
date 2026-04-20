# Phase 1 실행 스펙 — Breakout Pullback 백테스트

> 목표: 고전 Breakout Pullback 셋업을 S&P 500 10년 데이터로 백테스트하여 **Phase 2~4 성과 비교의 기준선(baseline)**을 확보한다.

---

## 산출물

1. **트레이드 로그** (`backtest_results/phase1_trades.csv`)
   - 컬럼: symbol, entry_date, entry_price, stop, target, exit_date, exit_price, exit_reason, r_multiple, pnl
2. **에쿼티 커브** (`backtest_results/phase1_equity.png` + 데이터 CSV)
3. **성과 리포트** (`backtest_results/phase1_report.md`)
4. **파라미터 감도 분석** (`backtest_results/phase1_sensitivity.md`)

---

## 작업 단계

### Step 1. 데이터 수집 (`src/fetch/`)
- [ ] S&P 500 종목 리스트 확보 (Wikipedia 스크래핑 or 정적 파일)
- [ ] yfinance로 2015-01-01 ~ 실행일 일봉 다운로드
- [ ] 로컬 캐시 (`data/raw/{SYMBOL}.parquet`) — 재실행 빠르게
- [ ] 데이터 위생 검증: 결측, 이상치(±50% 일일 변동), 거래 정지 구간 처리

**입력**: 종목 리스트
**출력**: 종목별 OHLCV DataFrame (인덱스 = datetime, 조정 종가)

### Step 2. 지표 계산 (`src/indicators/`)
- [ ] `donchian_channel(df, period=20)` → upper, lower, middle
- [ ] `atr(df, period=20)` → ATR 시계열
- [ ] `swing_low(df, lookback=10)` → 최근 스윙로우
- [ ] SPY 기준 시장 체제 플래그 (200MA, 50MA, VIX)

**원칙**: 모든 지표는 `t` 시점까지의 데이터만으로 계산 (lookahead bias 방지).

### Step 3. 시그널 생성 (`src/strategy/breakout_pullback.py`)
진입 로직을 함수로 구현:
```python
def detect_breakout_pullback(df, params) -> list[SignalEvent]:
    """
    df: 단일 종목 OHLCV + 지표 부착
    params: DonchianPeriod, AtrPeriod, PullbackAtrMult, PullbackBars, ...
    return: [(entry_date, entry_price, stop, target), ...]
    """
```

**로직 (STRATEGY.md §3.2~3.4 구체화)**:
1. 종가가 직전 N일 최고가 돌파 → "돌파 이벤트" 기록
2. 돌파 이후 `pullback_bars` 내에, 저가가 `breakout_price − 0.5×ATR` 이하로 내려갔다가 종가는 `breakout_price` 위 유지
3. 위 조건 충족 후 첫 양봉 다음 날 시가로 진입
4. stop = `min(돌파 직전 스윙로우, 진입가 − 2×ATR)`
5. target = `진입가 + 3R` (R = 진입가 − stop)

### Step 4. 백테스트 엔진 (`src/backtest/`)
- [ ] `Portfolio` 클래스: 현금, 포지션, 에쿼티 추적
- [ ] `Position` 객체: 진입·분할 매수·분할 청산 관리
- [ ] 일자별 루프:
  1. 시장 체제 필터 체크 (§1.5)
  2. 기존 포지션 손절/익절 체크 (종가 기준)
  3. 신규 시그널 중 포트폴리오 한도(§1.3) 통과한 것만 진입
  4. 서킷 브레이커 체크 (§1.4)
- [ ] 슬리피지 0.1%, 수수료 $0 반영

**중요**: 같은 날 여러 시그널이면 변동성 낮은 순서로 우선 진입 (혹은 랜덤 → 몬테카를로).

### Step 5. 성과 분석 (`src/backtest/metrics.py`)
- [ ] 승률, 평균 R-배수, Profit Factor
- [ ] Sharpe, Sortino, Calmar
- [ ] 최대 낙폭(MDD), MDD 지속 기간
- [ ] 연도별·섹터별 breakdown
- [ ] 몬테카를로: 트레이드 순서 1000회 셔플 → MDD 분포 시각화

### Step 6. 파라미터 감도 분석
- [ ] 고정: 유니버스, 기간, 수수료 구조
- [ ] 변동: Donchian 기간 (10/20/40), ATR 배수 손절 (1.5/2.0/2.5), 되돌림 ATR (0.3/0.5/0.7)
- [ ] 3×3×3 = 27 조합, 각 조합의 핵심 지표 테이블로 정리
- [ ] **경고**: 최적 조합을 실전에 쓰지 말 것. 상위 30% 내에서 **강건한(robust)** 조합을 선택

### Step 7. Walk-Forward Validation
- [ ] 3년 학습 → 1년 검증 롤링 (2015~17 학습 → 2018 검증 → 2016~18 학습 → 2019 검증 …)
- [ ] WFO 성과가 in-sample × 50% 이상이면 통과
- [ ] 미달 시: 셋업 단순화 or 폐기

---

## Phase 1 Exit Criteria (Phase 2 진행 조건)

다음 기준 **모두** 충족 시 Phase 2 진행:
- 샘플 ≥ 100 트레이드
- 승률 ≥ 45%
- Profit Factor ≥ 1.3
- MDD ≤ 15%
- Sharpe ≥ 0.8
- WFO 성과 / In-sample 성과 ≥ 50%

**미충족 시**: 원인 분석 (파라미터 문제? 셋업 자체 문제? 데이터 문제?) → 재설계 또는 Phase 1 셋업 폐기 후 Phase 2 직접 진행.

---

## 구현 메모

- 한국 거주자 미국주식 매매이므로 시간대 차이 고려 (백테스트는 UTC/ET 기준, 실전은 한국 시간 22:30~05:00)
- 데이터는 `yfinance`로 시작하되, 품질 이슈 발견 시 `polygon` 또는 `alpaca` 대체 검토
- 백테스트 엔진은 자체 구현 (학습 목적). 복잡해지면 `vectorbt` 이전 검토 — **Phase 1 통과 후에**
- 코드는 함수형 + dataclass 위주, 클래스 상속 최소화
