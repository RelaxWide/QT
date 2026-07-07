"""보유 포지션의 주식분할 자동 보정.

yfinance 가격 데이터는 분할 발생 시 과거 시계열 전체가 소급 조정되지만,
positions_*.json 에 저장된 진입가·주식수·스톱은 분할 전 값 그대로 남는다.
분할일 이후 첫 실행에서 (가격류 ÷ ratio, 주식수 × ratio) 로 보정한다.

사례: 2026-07-02 CRWD 4:1 분할 미보정 → 페이퍼 Clenow NAV 약 $4,500 과소평가.
"""
from __future__ import annotations

import pandas as pd


def _splits_since(symbol: str, after: str) -> float | None:
    """`after`(YYYY-MM-DD) 이후 발생한 분할 비율의 곱.

    분할 없음 → 1.0 / 조회 실패 → None (마커 갱신 금지, 다음 실행에서 재시도).
    """
    try:
        import yfinance as yf
        sp = yf.Ticker(symbol).splits
    except Exception:
        return None
    if sp is None or len(sp) == 0:
        return 1.0
    ratio = 1.0
    for dt, r in sp.items():
        if str(pd.Timestamp(dt).date()) > after and r > 0:
            ratio *= float(r)
    return ratio


def _cutoff(pos) -> str:
    """분할 적용 기준일 — 진입일과 마지막 보정일 중 더 늦은 쪽."""
    return max(pos.entry_date[:10], (pos.split_adjusted_through or "")[:10])


def adjust_simple_positions(positions: dict, today: str) -> list[str]:
    """Clenow/Weinstein SimplePosition dict 를 분할 보정. 변경 내역 반환."""
    changed = []
    for sym, pos in positions.items():
        ratio = _splits_since(sym, _cutoff(pos))
        if ratio is None:
            continue
        if abs(ratio - 1.0) > 1e-9:
            pos.entry_price /= ratio
            pos.shares      *= ratio
            changed.append(f"{sym} x{ratio:g}")
        pos.split_adjusted_through = today
    return changed


def adjust_paper_positions(positions: dict, today: str) -> list[str]:
    """Phase 4 PaperPosition dict 를 분할 보정 (스톱·목표가 포함). 변경 내역 반환."""
    changed = []
    for sym, pos in positions.items():
        ratio = _splits_since(sym, _cutoff(pos))
        if ratio is None:
            continue
        if abs(ratio - 1.0) > 1e-9:
            pos.entry_price      /= ratio
            pos.stop_initial     /= ratio
            pos.stop_current     /= ratio
            pos.targets           = [t / ratio for t in pos.targets]
            pos.shares_total     *= ratio
            pos.shares_remaining *= ratio
            changed.append(f"{sym} x{ratio:g}")
        pos.split_adjusted_through = today
    return changed
