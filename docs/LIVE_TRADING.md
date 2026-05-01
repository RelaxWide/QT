# 실전 자동매매 운영 가이드 (KIS API)

## 개요

페이퍼 트레이딩과 병렬로 KIS 모의/실계좌에서 동일 전략을 자동 실행하여 슬리피지·체결 오차를 검증한다.

**목적:** 페이퍼 트레이딩 4주는 거래 수가 50건 미만이라 통계적으로 부족하고, 슬리피지·체결·세금이 가정값이다. 소액 실자본을 병렬 투입하여 실제 체결 품질을 검증한다.

**구조:**
```
QT/
├── paper_trading/       # 기존 유지 (병렬 운영)
├── live_trading/        # KIS API 클라이언트, 주문, 포지션, 리스크
├── scheduler/           # Windows Task Scheduler 스크립트
└── config_live.yaml     # KIS 키·자본 설정 (gitignore, 절대 커밋 금지)
```

---

## 진행 현황

| Phase | 내용 | 상태 |
|---|---|---|
| **A: KIS 클라이언트** | 인증·시세·주문 API 래퍼 | ✅ 완료 |
| **B: 신호→주문 변환** | 페이퍼 신호 → KIS 주문 | ✅ 완료 |
| **C: 포지션 동기화** | KIS 잔고 ↔ 로컬 positions 일치 | ✅ 완료 |
| **D: 리스크 가드** | Kill switch, 손실 한도, 연속손절 차단 | ✅ 완료 |
| **E: 스케줄러** | Windows Task Scheduler 무인 운영 | ✅ 완료 (4개 작업 등록) |
| **F: 모의투자 4주** | 슬리피지·체결 검증 | 진행 중 |
| **G: 실계좌 전환** | Phase F 통과 후 실자본 투입 | 대기 |

---

## 1. 사전 준비

### 1.1 KIS 계좌 및 API 발급

1. 한국투자증권 계좌 개설 (실계좌 + 모의투자 계좌)
2. https://apiportal.koreainvestment.com/ 가입 → "한국투자 OPEN API" 신청
3. **모의투자 앱**과 **실전 앱**을 각각 별도 등록 → AppKey/AppSecret 분리 발급
   - 모의투자 키로 실전 서버 접근 불가, 반대도 불가 (서버가 다름)

### 1.2 설정 파일 작성

```bash
cp config_live.example.yaml config_live.yaml
```

`config_live.yaml` 필수 입력 항목:
```yaml
kis:
  mode: "mock"                    # mock(모의) → prod(실전) 전환 시 변경
  app_key:    "모의투자_AppKey"
  app_secret: "모의투자_AppSecret"
  mock_account_no: "50000000-01"  # 모의투자 계좌번호
  prod_account_no: "44000000-01"  # 실전 계좌번호 (Phase G 시 사용)
```

⚠️ **`config_live.yaml`은 절대 git에 커밋 금지** (`.gitignore`에 등록됨)

### 1.3 의존성 설치

```bash
pip install requests pyyaml
```

---

## 2. Phase A — KIS API 클라이언트 ✅ 완료

**파일:** `live_trading/kis_client.py`

### 구현 내용

- OAuth 토큰 발급/캐시 (`live_trading/.kis_token.json`, 24h TTL, 60분 마진 자동 갱신)
- mock/prod 환경 자동 전환 (base_url, TR_ID, 계좌번호)
- prod 모드 이중 잠금: `KISClient.from_config(allow_prod=True)` 명시 필요
- placeholder 계좌번호 진입 차단
- hashkey 자동 발급 (KIS POST 주문 필수)
- Rate limit 대응: hashkey→주문 사이 0.5초 딜레이
- 전체 API 호출 로깅 (`logs/kis_api_YYYYMMDD.log`)

### KIS API 주의사항

| 항목 | 모의투자 | 실전 |
|---|---|---|
| 서버 | `openapivts.koreainvestment.com:29443` | `openapi.koreainvestment.com:9443` |
| TR_ID 접두사 | `VTTT*`, `VTTS*` | `TTTT*`, `TTTS*` |
| AppKey | 모의 전용 키 | 실전 전용 키 |
| 시세 API (EXCD) | NAS / NYS / AMS (3자리) | 동일 |
| 거래 API (OVRS_EXCG_CD) | NASD / NYSE / AMEX (4자리) | 동일 |

시세 API와 거래 API의 거래소 코드 체계가 다름 — 코드에서 자동 변환 처리됨.

### 검증 명령

```bash
python -m live_trading.kis_client --test-auth
python -m live_trading.kis_client --test-balance
python -m live_trading.kis_client --test-price AAPL
python -m live_trading.kis_client --test-order AAPL 1   # 장중 실행 시 미체결 주문 생성
```

### 실전 전환 방법

config_live.yaml 변경:
```yaml
kis:
  mode: "prod"
  app_key:    "실전_AppKey"
  app_secret: "실전_AppSecret"
  capital:
    total_usd: 2000   # 실제 입금액
```

코드 변경 (스케줄러 진입점):
```python
cli = KISClient.from_config(allow_prod=True)  # allow_prod 명시 필수
```

---

## 3. Phase B — 신호→주문 변환 ✅ 완료

**파일:** `live_trading/orders.py`

페이퍼 트레이딩의 신호 출력을 입력으로 받아 KIS 주문으로 변환한다.

| 전략 | 진입 타이밍 | 주문 방식 |
|---|---|---|
| Phase 4 | 다음 미국장 개장 시 | 전날 종가 +1% LIMIT (MOO 근사) |
| Clenow | 당일 종가 후 즉시 | 종가 ±0.5% LIMIT |
| Weinstein | 당일 종가 후 즉시 | 종가 ±0.5% LIMIT |

- `order_map.json` — 동일 신호 중복 주문 방지 (idempotent)
- 예산 초과 종목(고가 종목) 자동 스킵

**자본 배분 (모의 $10,000 기준):**

| 전략 | 자본 | 최대 종목 수 | 종목당 |
|---|---|---|---|
| Phase 4 | $3,500 | risk-based | R≈$17.5 |
| Clenow | $3,500 | 5 | $700 |
| Weinstein | $3,000 | 4 | $750 |

**검증:**
```bash
python -m live_trading.orders --phase4 --dry-run
python -m live_trading.orders --clenow --dry-run
python -m live_trading.orders --weinstein --dry-run
```

---

## 4. Phase C — 포지션 동기화 ✅ 완료

**파일:** `live_trading/account.py`, `live_trading/tracker_live.py`

- 실행 시작 시 KIS 잔고 API → 실제 보유 포지션 로드
- 로컬 `positions_live_*.json`과 비교, 불일치 텔레그램 경고
- 체결가 기록: 신호 종가 vs 실제 체결가 → `slippage_log.csv`

**생성 파일:**
| 파일 | 내용 |
|---|---|
| `live_trading/positions_live_phase4.json` | Phase 4 보유 포지션 |
| `live_trading/positions_live_clenow.json` | Clenow 보유 포지션 |
| `live_trading/positions_live_weinstein.json` | Weinstein 보유 포지션 |
| `live_trading/trades_live_*.csv` | 체결가·환율·수수료·KRW 환산 PnL |
| `live_trading/slippage_log.csv` | signal_price vs fill_price diff% |
| `live_trading/order_map.json` | 신호 ID ↔ KIS 주문번호 매핑 |

---

## 5. Phase D — 리스크 가드 ✅ 완료

**파일:** `live_trading/risk_guard.py`

| 조건 | 동작 |
|---|---|
| `live_trading/KILL_SWITCH` 파일 존재 | 모든 신규 주문 즉시 차단 |
| 당일 -1.5% | 신규 진입 차단 (청산만 허용) |
| 누적 낙폭 -10% | 전면 정지 + 텔레그램 경고 |
| 5연속 손실 | 다음 신호 1주일 보류 |
| 단일 주문 > 자본 50% | 주문 거부 (오류 보호) |

**Kill Switch 사용:**
```bash
# 즉시 정지
type nul > live_trading/KILL_SWITCH

# 재개
del live_trading/KILL_SWITCH
```

---

## 6. Phase E — 스케줄러 ✅ 완료

**파일:** `scheduler/*.py`, `scheduler/register_tasks.ps1`

### 일일 스케줄 (KR 시간 기준)

| 시각 | 조건 | 스크립트 | 역할 |
|---|---|---|---|
| 22:29 | 평일 | `morning_entry.py` | Phase 4 pending → MOO 주문 전송 |
| 23:00~04:00 매시 | 평일 | `exit_check.py` | 손절·트레일 체크, 즉시 청산 |
| 06:00 | 평일 | `daily_close.py` | Clenow/Weinstein 신호+주문, Phase 4 익일 pending 저장 |
| 07:00 | 평일 | `summary.py` | 텔레그램 일일 리포트 |

### 등록

```powershell
.\scheduler\register_tasks.ps1
```

등록 확인:
```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -like "QT_*" }
```

---

## 7. Phase F — 모의투자 4주 검증 (진행 중)

KIS 모의계좌로 4주 무인 운영 후 다음 게이트 통과 시 실계좌 전환.

| 지표 | 기준 |
|---|---|
| 슬리피지 평균 | < 0.3% (백테스트 가정 0.1%의 3배 이내) |
| 주문 실패율 | < 5% |
| 체결 지연 | ±1거래일 이내 |

```bash
python -m live_trading.account --report   # 슬리피지 누적 리포트
python -m live_trading.account --sync     # KIS 잔고 동기화
python scheduler/summary.py --print-only  # 일일 요약 콘솔 출력
```

---

## 8. Phase G — 실계좌 전환 (Phase F 통과 후)

**자본 배분 ($2,000 기준):**

| 전략 | 자본 | 최대 종목 수 | 종목당 |
|---|---|---|---|
| Phase 4 | $700 | risk-based | R≈$5 |
| Clenow | $700 | 5 | $140 |
| Weinstein | $600 | 4 | $150 |

**전환 절차:**
1. `config_live.yaml` → `mode: "prod"`, 실전 AppKey/AppSecret 입력
2. 스케줄러 진입점 → `KISClient.from_config(allow_prod=True)` 변경
3. 첫 1주: 매일 수동 모니터링
4. 3개월 후: 자본 증액 검토 ($2k → $5k)

---

## 9. 모니터링 파일

| 파일 | 내용 |
|---|---|
| `logs/kis_api_YYYYMMDD.log` | 모든 KIS API 호출 로그 |
| `live_trading/positions_live_*.json` | 전략별 보유 포지션 |
| `live_trading/trades_live_*.csv` | 체결 기록 (체결가, 환율, 수수료) |
| `live_trading/slippage_log.csv` | 신호가격 vs 실체결가 비교 |
| `live_trading/order_map.json` | 신호 ID ↔ KIS 주문번호 매핑 |

---

## 참고

- KIS API 공식 문서: https://apiportal.koreainvestment.com/
- 한국시간 미국장 정규시간: 22:30~05:00 (서머타임), 23:30~06:00 (표준시)
- 토큰 TTL: 24시간 (코드는 60분 마진 두고 자동 갱신)
- API Rate Limit: 초당 약 20건, hashkey 호출과 주문 사이 0.5초 딜레이 적용
- 양도소득세: 5월 자가 신고 — `trades_live_*.csv` 연간 백업 필수
