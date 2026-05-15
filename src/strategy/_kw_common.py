"""
KW (강환국 류) 전략 공통 유틸리티.

데이터구조:
    FundamentalSignal — 분기 리밸런싱 신호 (target 종목 + 동일비중)

유틸:
    filter_small_cap()              — 시총 하위 N% 추출
    rebalance_dates_kr_quarterly()  — KR 재무공시 일정 기반 분기 리밸런싱 날짜
    adjust_to_trading_day()         — 영업일 보정
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class FundamentalSignal:
    """분기 리밸런싱 신호. haa_engine 호환 형식."""
    date:    pd.Timestamp           # 리밸런싱 신호 발생일 (영업일 보정 완료)
    weights: dict[str, float]       # ticker → weight (동일비중 1/N)
    scores:  dict[str, float] = field(default_factory=dict)
    universe_size: int = 0          # 진입 가능했던 풀 크기 (감사용)
    strategy: str = "kw"            # 전략 이름 (로그용)


# ── 시총 필터 ─────────────────────────────────────────────────────────────
def filter_small_cap(mcap: pd.Series, pct: float = 0.20) -> list[str]:
    """시총 하위 pct (예: 0.20) 종목 ticker 리스트 반환.
    Hint: mcap 은 panel.loc[date, 'mcap'] 의 ticker→mcap 시리즈."""
    valid = mcap.dropna()
    valid = valid[valid > 0]
    if valid.empty:
        return []
    threshold = valid.quantile(pct)
    return valid[valid <= threshold].index.tolist()


def filter_top_cap(mcap: pd.Series, pct: float = 0.30) -> list[str]:
    """시총 상위 pct 종목. KOSPI200 대형주 효과 검토용."""
    valid = mcap.dropna()
    valid = valid[valid > 0]
    if valid.empty:
        return []
    threshold = valid.quantile(1 - pct)
    return valid[valid >= threshold].index.tolist()


# ── 리밸런싱 날짜 생성 ────────────────────────────────────────────────────
def rebalance_dates_kr_quarterly(
    start,
    end,
    months: list[int] | None = None,
    dom: list[int] | None = None,
) -> list[pd.Timestamp]:
    """KR 분기 리밸런싱 날짜 생성.

    기본: 5/16, 8/16, 11/16 (분기보고서 공시 마감 + 1일), 4/1 (사업보고서 후)
    """
    if months is None:
        months = [5, 8, 11, 4]
    if dom is None:
        dom = [16, 16, 16, 1]

    if end is None or (isinstance(end, float) and pd.isna(end)):
        end = pd.Timestamp.today()
    start = pd.Timestamp(start)
    end   = pd.Timestamp(end)

    dates = []
    for year in range(start.year, end.year + 2):
        for m, d in zip(months, dom):
            try:
                ts = pd.Timestamp(year=year, month=m, day=d)
            except ValueError:
                continue
            if start <= ts <= end:
                dates.append(ts)
    return sorted(dates)


def rebalance_dates_kr_annual(start, end, month: int = 5, day: int = 16) -> list[pd.Timestamp]:
    """KR 연간 리밸런싱 — 5/16 (사업보고서 + 공시 후 안정화)."""
    start = pd.Timestamp(start)
    end   = pd.Timestamp(end)
    out = []
    for y in range(start.year, end.year + 1):
        try:
            ts = pd.Timestamp(year=y, month=month, day=day)
        except ValueError:
            continue
        if start <= ts <= end:
            out.append(ts)
    return out


# ── 영업일 보정 ────────────────────────────────────────────────────────────
def adjust_to_trading_day(date, calendar: pd.DatetimeIndex) -> pd.Timestamp | None:
    """date 이후 첫 영업일. 없으면 None."""
    ts = pd.Timestamp(date)
    future = calendar[calendar >= ts]
    return future[0] if len(future) > 0 else None


def adjust_signals_to_trading(
    signal_dates: list[pd.Timestamp],
    calendar: pd.DatetimeIndex,
) -> list[pd.Timestamp]:
    """리밸런싱 일자들을 영업일로 보정 (각 일자 이후 첫 영업일)."""
    out = []
    seen = set()
    for d in signal_dates:
        adj = adjust_to_trading_day(d, calendar)
        if adj is not None and adj not in seen:
            out.append(adj)
            seen.add(adj)
    return out
