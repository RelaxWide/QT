# QT — 규칙 기반 주식 자동매매

목표: 장기적으로 SPY 보유를 이기는 것. 못 이기면 SPY 로 간다 — 판정은 숫자로 한다.

> **현재 상황은 [`docs/STATUS.md`](docs/STATUS.md) 부터.** 이 README 는 개요만 다룬다 (2026-11-12 갱신).

---

## 지금 돌아가는 것

| 트랙 | 전략 | 상태 |
|---|---|---|
| US 실계좌 (한국투자증권 API) | 코어 SPY 60% + Clenow 모멘텀 40% (최대 6종목, 변동성 역가중·업종 제한) | ✅ 운영 중 |
| US 페이퍼 (GitHub Actions) | — | ❌ 2026-11-13 종료 (새 전략은 백테스트 → 소액 실계좌로 검증) |
| Weinstein Stage 2 · Phase 4 | — | ❌ 2026-11-12 종료 |
| KR 실계좌 | — | ⏸️ 2026-10-19 중단 (KW Super Value 전량 청산) |

- 판정 기준: 2026-10-19 이후 6개월 또는 50거래 시점에 **SPY 보유 대비 위험조정 수익**.
  계좌 vs SPY 수치는 매일 텔레그램으로 온다.
- 프로젝트는 2026-04 일목균형표 스윙 아이디어에서 시작했고, 약 20개 전략을 백테스트해 대부분 폐기했다.
  채택·폐기 근거는 아래 문서에 있다.

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/STATUS.md`](docs/STATUS.md) | 현재 계좌·최근 변경·확인 대기·다음 할 일 |
| [`docs/ACTIVE_STRATEGIES.md`](docs/ACTIVE_STRATEGIES.md) | 채택 전략 규칙, 백테스트, 운영 인프라·명령 |
| [`docs/REJECTED_STRATEGIES.md`](docs/REJECTED_STRATEGIES.md) | 폐기 전략과 교훈 |
| `docs/backup/` | 초기 설계 문서 (일목 스윙 Phase 1~4 구상) |

## 구조

```
QT/
├── run_daily.py            # (종료) 페이퍼 트레이딩 일일 실행 — 예약 실행 제거됨, 코드만 보존
├── run_*.py, analyze_*.py  # 전략별 백테스트·분석 스크립트
├── src/                    # fetch(가격) · indicators · strategy · backtest · notify(텔레그램)
├── paper_trading/          # 신호 계산 모듈(live_signals 등) + 종료된 페이퍼의 마지막 상태(보존용)
├── live_trading/           # 실계좌: KIS 클라이언트, 주문, 체결 확정, 리스크 가드, NAV·SPY 벤치마크
├── scheduler/              # Windows 작업 스케줄러 진입점 (장마감 처리, 수요일 매수, 일일 요약, git 동기화)
├── scripts/                # 점검·디버그·수동 조치 스크립트
├── backtest_results/       # 백테스트 산출물 (gitignore)
├── config.yaml             # 전략 파라미터 (gitignore, 예시: config.example.yaml)
└── config_live.yaml        # 계좌·자본 배분·리스크 한도·텔레그램 (gitignore, 예시: config_live.example.yaml)
```

## 일일 흐름 (KST)

| 시각 | 작업 |
|---|---|
| 05:40 평일 | 원격 코드 변경 로컬 동기화 (git pull) |
| 06:30 평일 | 장마감 처리 — 체결 확정 → NAV·SPY 기록 → 리스크 가드 → 매도 주문 → (수요일) 매수 후보 저장 → 텔레그램 |
| 07:00 평일 | 일일 요약 텔레그램 |
| 목 00:00 | 수요일 11:00 ET 까지 대기 후 매수 주문 (표준시엔 01:00 KST) |

## 자주 쓰는 명령

```powershell
python -m live_trading.risk_guard --status                      # 낙폭·가드 상태
python -m live_trading.risk_guard --benchmark                   # SPY 대비 성과
python -m live_trading.risk_guard --cashflow 1000 --market us   # 입출금 기록 (입금 +, 출금 -) — 빠뜨리면 손익이 왜곡됨
python scheduler/summary.py --print-only                        # 요약 미리보기 (전송 안 함)
python scheduler/daily_close.py --dry-run                       # 장마감 처리 모의 실행
```

실주문이 나가는 스크립트는 dry-run 으로 먼저 확인한다.
