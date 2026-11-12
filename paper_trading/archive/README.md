# 종료된 페이퍼 전략 기록 (2026-11-12)

Phase 4 와 Weinstein 페이퍼 트레이딩을 2026-11-12 에 종료했다. 종료 시점 상태 그대로 보존한다 (열린 포지션은 청산 기록 없이 동결).

| 파일 | 내용 |
|---|---|
| `positions.json`, `pending.json`, `trades.csv` | Phase 4 — 종료 시 보유 9종목, 청산 28건 |
| `positions_weinstein.json`, `trades_weinstein.csv` | Weinstein — 종료 시 보유 1종목(CNC), 청산 1건 |
| `daily_nav_3strategy.csv` | 3전략 NAV 전체 이력 (2026-05-01 ~ 2026-11-10). 마지막 값: Phase 4 $95,534 / Weinstein $101,552 / Clenow $105,443 |

폐기 사유는 `docs/REJECTED_STRATEGIES.md` 7-H, 7-I. Clenow 페이퍼는 `paper_trading/daily_nav.csv` 에서 계속된다.
원래 경로(`paper_trading/positions.json` 등)의 파일은 삭제했다.
