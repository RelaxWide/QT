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


def session_after(ts) -> pd.Timestamp | None:
    """ts(naive 면 KST) 이후 처음 마감하는 NYSE 세션 — 그 시각에 낸 주문이 체결되는 세션."""
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XNYS")
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Seoul")
        ts = ts.tz_convert("UTC")
        d = pd.Timestamp(ts.tz_convert("America/New_York").date())
        for s in cal.sessions_in_range(d, d + pd.Timedelta(days=15)):
            if cal.session_close(s) > ts:
                return pd.Timestamp(s.date())
    except Exception as e:
        log.warning(f"[freshness] 체결 세션 계산 실패 ({ts!r}): {e}")
    return None


ORDER_CLOSE_BUFFER_MIN = 10   # 마감 직후 잔고 반영 지연 여유


def order_session_closed(submitted_at, now: pd.Timestamp | None = None) -> bool | None:
    """
    주문이 유효했던 세션(접수 이후 처음 마감하는 NYSE 세션)이 이미 끝났는가.
    끝나기 전에는 "미체결" 로 판정하면 안 된다 — 06:30 KST 에 낸 매도는 그날 밤 개장에야 체결된다.
    submitted_at 이 naive 면 KST. 달력을 못 쓰면 None (호출측이 시간 기준으로 폴백).
    """
    try:
        import exchange_calendars as xcals
        cal = xcals.get_calendar("XNYS")
        ts = pd.Timestamp(submitted_at)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Seoul")
        ts = ts.tz_convert("UTC")
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now).tz_convert("UTC")
        d = pd.Timestamp(ts.tz_convert("America/New_York").date())
        for s in cal.sessions_in_range(d, d + pd.Timedelta(days=15)):
            close = cal.session_close(s)
            if close > ts:
                return bool(now >= close + pd.Timedelta(minutes=ORDER_CLOSE_BUFFER_MIN))
    except Exception as e:
        log.warning(f"[freshness] 주문 세션 종료 판정 실패 ({submitted_at!r}): {e}")
    return None


def is_stale(data_today: pd.Timestamp, expected: pd.Timestamp | None) -> bool:
    """기대 세션보다 하루라도 뒤면 정체. 달력이 없으면 3영업일 허용 (예전 기준)."""
    if expected is None:
        return data_today < pd.Timestamp.today().normalize() - pd.offsets.BDay(3)
    return data_today.normalize() < expected
