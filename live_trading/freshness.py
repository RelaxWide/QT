"""
가격 데이터 신선도 판정 — "지금 있어야 할 마지막 거래일" 을 NYSE 휴장 달력으로 계산.

예전엔 오늘 - BDay(3) 보다 오래되면 정체로 봤다. 그러면 1~2 거래일 밀린 데이터로도
신호·매도 지정가가 나갔다 (2026-07 3회 재발). 이제 기대 세션과 정확히 비교한다.

  expected_us_session()  → 장 마감 + 30분이 지난 가장 최근 NYSE 세션 (naive date)
                           달력 로드 실패 시 None → 호출측이 느슨한 fallback 사용
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger("kis")

CLOSE_BUFFER_MIN = 30   # 마감 직후엔 일봉이 아직 확정 안 됐을 수 있다


def expected_us_session(now: pd.Timestamp | None = None) -> pd.Timestamp | None:
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XNYS")
        now = pd.Timestamp.now(tz="UTC") if now is None else now.tz_convert("UTC")
        cutoff = now - pd.Timedelta(minutes=CLOSE_BUFFER_MIN)
        d = pd.Timestamp(cutoff.tz_convert("America/New_York").date())
        for s in reversed(cal.sessions_in_range(d - pd.Timedelta(days=15), d)):
            if cal.session_close(s) <= cutoff:
                return pd.Timestamp(s.date())
    except Exception as e:
        log.warning(f"[freshness] NYSE 달력 계산 실패: {e} — BDay fallback")
    return None


def is_stale(data_today: pd.Timestamp, expected: pd.Timestamp | None) -> bool:
    """기대 세션보다 하루라도 뒤면 정체. 달력이 없으면 3영업일 허용 (예전 기준)."""
    if expected is None:
        return data_today < pd.Timestamp.today().normalize() - pd.offsets.BDay(3)
    return data_today.normalize() < expected
