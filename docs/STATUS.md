# QT 현재 상황 (2026-10-19 기준)

새 세션은 이 파일부터 읽을 것. 전략 규칙·백테스트는 [`ACTIVE_STRATEGIES.md`](ACTIVE_STRATEGIES.md),
폐기 기록·교훈은 [`REJECTED_STRATEGIES.md`](REJECTED_STRATEGIES.md).
(이 파일은 2026-07-17·10-15 핸드오프를 대체한다 — 미해결 항목은 아래 5장으로 이관)

---

## 1. 한눈에 보기

| 시장 | 상태 | 전략 | 계좌 (10/19) |
|---|---|---|---|
| **US** | ✅ 실계좌 운영 | Clenow 60% (최대 5) + Weinstein 40% (최대 4) | 9종목, 평가 $11,927 + 예수금 $3,512 |
| **KR** | ⏸️ 중단 | 없음 (KW SV 중단, 재설계 예정) | 보유 0, 예수금 ₩111.6만 (+ T+2 정산 대기 매도대금) |
| 페이퍼 | GHA `daily.yml` | Phase 4 / Clenow / Weinstein | GHA 소유 — 로컬 수정 금지 |

- US 보유: Clenow DELL·CRL·VEEV·MRNA·MPC / Weinstein ALLE·DHR·AMZN·HCA (KIS 실잔고와 추적 파일 일치 확인)
- KR: `kw_super_value_pct: 0`, `kw_super_value_krw: 0`, `QT_KR_*` 스케줄 4개 Disabled
- Phase 4: 페이퍼 전용 (Sharpe 0.74 < 게이트 0.80)
- 텔레그램: 봇 `@rw_qt_bot` (2026-10-19 재생성), chat_id `8717210516`. 로컬 `config_live.yaml` + GitHub Secrets 둘 다 갱신 완료.
  **봇을 다시 만들거나 Revoke 하지 말 것** — 토큰이 바뀌면 두 곳 모두 다시 바꿔야 함.

---

## 2. 사용자 방향 (2026-10-19 합의)

- **우위는 알파가 아니라 규율(행동 우위)**. 신규 알파 탐색은 자본 5% 이하 파일럿 + 사전 게이트로만.
- **손익비형 OK, 승률 고집 안 함.** 멘탈을 지켜주는 건 "여러 종목 분산 + 지는 종목은 잘리는 구조" (US 경험).
- **US 판정 기준(제안)**: 운영 버그 수정 후 6개월 또는 50거래 시점에 **SPY 보유 대비 위험조정 수익**으로 판정.
  못 이기면 US 자금은 SPY 로, 탐색은 파일럿에서만.
- **KR**: 전략 전면 재설계 후 재개. US 구조 + KR 박스권·소형주 꼬리위험 필터. Clenow KR·Weinstein KR 그대로 이식 금지(이미 폐기).

---

## 3. 2026-10-19 변경 (이번 세션)

### US 체결 확정 (`live_trading/account.py::reconcile_fills`, `orders.py`, `kis_client.py`)
- 접수 ≠ 체결: `pending_buy` / `pending_sell` 상태 → 다음 daily_close 가 KIS 잔고로 확정
- 미체결 매도 자동 재주문 (재시도 -3% LIMIT), 체결가는 `inquire-ccnl` 실체결가
- `trades_live_*.csv` 손익 기록 시작 (그전엔 아무도 안 썼음)
- 기존 8종목 fill_price 를 KIS 평균단가로 교정 (기록이 LIMIT가 = 실체결보다 ~0.5% 높았음)

### 리스크 가드 재작성 (`live_trading/risk_guard.py`)
- 실계좌 NAV(`live_trading/nav_live_{us,kr}.csv`) 기준, 시장별 분리, fail-closed
- 누적 낙폭 **-20%** 신규 매수 중단 + 텔레그램, 당일 -1.5%(평가손익 포함), 연속손실 규칙 삭제
- **입출금 시 반드시** `python -m live_trading.risk_guard --cashflow 금액 --market us`

### KR 중단·청산
- 17종목 청산 (미추적 008870·118000 복구 후 포함, 거래 적은 3종목은 시장가 정정, 008500 사용자 수동)
- `scripts/kr_sell_market.py` (미체결 지정가 → 시장가 정정), `kis_client.get_open_orders_kr / revise_order_to_market_kr`

### 텔레그램 복구
- 원인: 이전 봇 삭제(401) + chat_id 칸에 봇 ID 가 들어가 있었음

---

## 4. 첫 실전 검증 대기 ⚠️ (다음 US daily_close 06:00 KST 이후 확인)

| 항목 | 정상 신호 | 이상 신호 → 조치 |
|---|---|---|
| NAV 첫 기록 | `live_trading/nav_live_us.csv` 생성, 텔레그램 "📉 낙폭 0.00% (한도 -20%)" | "🚨 NAV 기록 실패" → 잔고 API 점검 |
| 체결 확정 | 매매 발생 후 "✅ 체결 확정" | "ℹ️ 체결가 추정" → `get_order_fills_us` 필드명 검증 필요 (실계좌 미검증 코드) |
| 미체결 처리 | "⚠️ 매도 미체결 → 재매도" 후 다음날 해소 | 같은 종목 반복 미체결 → 가격 인하폭 재검토 |

첫 NAV 기록 전까지 가드는 "NAV 이력 없음"으로 통과한다.

---

## 5. 미해결 / 다음 할 일

| # | 항목 | 우선 | 메모 |
|---|---|---|---|
| 1 | ~~로컬 git 동기화 깨짐~~ ✅ 10/19 해결 | — | 로컬 daily_close 가 GHA 소유 `paper_trading/pending.json` 을 덮어써서 `QT_GitSync` 가 조용히 실패하던 문제. Phase 4 실계좌 pending 을 `live_trading/pending_phase4_live.json` 으로 분리(배분 0 이면 아예 안 씀), `QT_GitSync` 는 `scheduler/git_sync.py` 경유로 바꿔 실패 시 텔레그램. **사용자 조치 필요: QT_GitSync 재등록** (7장) |
| 2 | 데이터 정체 가드 | ⚠️ | 현재 `BDay(3)` 초과 시 재다운로드. 1거래일 기준 + refresh 실패 시 신호 생성 중단으로 강화 (7월 3회 재발 이력) |
| 3 | 텔레그램 메시지 정확성 | ⚠️ | 헤더 날짜=데이터일(실행일 별도 표기 필요), stale 데이터에서 "매도: 없음"이 정상처럼 보임, "KIS N종목 추적" 카운트가 미할당 포함, T+2 미정산 미표기 |
| 4 | `summary.py` 07:00 스케줄 | 보통 | 7월 감사 때 로그 자체가 없었음 — 등록 여부 확인 |
| 5 | `kw_sv_morning_sell.py` dry-run 도 텔레그램 발송 | 낮음 | 10/19 모의실행이 "매도 완료 16건"으로 발송됨 |
| 6 | KR 매도 "접수=삭제" 버그 | KR 재개 전 필수 | `orders._update_live_positions` KR 분기. US reconcile 구조 이식 |
| 7 | KR 새 전략 설계 | 다음 과제 | 2장 방향 참고. 거래정지·급락 사전 필터 포함 |
| 8 | 미구현 서킷브레이커 | 낮음 | 월 낙폭 5% 시 포지션 50% 축소 (ACTIVE 8장) |
| 9 | 후보 전략 | 파일럿 | Nick Radge 눌림목(독립 재현 CAGR 22%/MDD 21%, 단 단기 평균회귀 계열 "구조적 불가" 판정과 겹칠 위험) |

**종결 처리된 이전 항목**: MTS -3.63% KR 불일치 조사·KW SV NAV 기록·KR 거래정지 처리·KR 매수 누락 3종목
(→ KR 청산으로 무의미), US 미추적 ON/ORLY/MU(10/1 수동 정리), US 체결확인·체결가 기록·텔레그램 토큰(10/19 해결).

---

## 6. 실적 기록 (10/12 평가)

| 트랙 | 알파 | 리스크 | 운영 | 멘탈 | 종합 |
|---|---|---|---|---|---|
| US Clenow | 45 | 55 | 40 | 35 | 44 (D) |
| US Weinstein | 60 | 70 | 45 | 65 | 60 (C) |
| KR KW SV | 35 | 30 | 30 | 25 | 30 (D-) → 중단 |
| 포트폴리오 | | | | | 42 (D+) |

- 실현: Clenow 19건 승률 36.8%(순 +2.44%), Weinstein 5건 +15.77%(표본 부족), KR 1차 분기 -16.68%
- US 라이브는 6~7월 페이퍼 대비 약 -6%p — 원인은 알파가 아니라 실행 인프라(가격 데이터 동결, 미체결 방치, 왕복 매매)
- 시장 레짐(10/12): KOSPI 1년 +110% → 3개월 -25.8%, SPY 1년 +16.9%

---

## 7. 운영 메모

- Python: PowerShell `& "C:\Users\Quiruri\Anaconda3\envs\qt\python.exe" ...` / Claude `!` 명령은 `/c/Users/Quiruri/Anaconda3/envs/qt/python.exe`
- 실주문 스크립트는 Claude 가 직접 실행하지 않음 — 미리보기/dry-run 후 사용자가 `!` 로 실행
- 소유권: 페이퍼 상태 파일 = GHA, 실계좌 신호 기준 = `live_trading/positions_live_*.json` (gitignore, 로컬 전용)
- 스케줄 등록/변경은 사용자가 직접 (`scheduler/register_tasks*.ps1`)
- 커밋하면 바로 푸시 (안 하면 `QT_GitSync` ff-only 실패 → 이제 텔레그램 "🚨 로컬 git 동기화 실패"로 알려줌)
- 로컬 스크립트는 `paper_trading/` 의 GHA 소유 파일(positions*.json, pending.json, trades*.csv, daily_nav.csv, daily_report.md)에 쓰지 말 것.
  실계좌 상태는 전부 `live_trading/` 아래 (gitignore)
- QT_GitSync 재등록 (10/19 변경 후 1회): `.\scheduler\register_tasks.ps1` 재실행 — US 작업 전부 같은 설정으로 덮어씀
