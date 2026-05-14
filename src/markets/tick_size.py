"""
KOSPI 호가 단위 (틱 사이즈) 계산.
KRX 규정에 따라 가격대별 호가 단위 적용. 미준수 시 주문 거부됨.

참고: https://www.krx.co.kr (시장운영>주식시장>매매제도)
"""
from __future__ import annotations


def tick_size_kospi(price: float) -> int:
    """주가별 KOSPI 호가 단위 (KRW)."""
    p = float(price)
    if p < 2000:
        return 1
    if p < 5000:
        return 5
    if p < 20000:
        return 10
    if p < 50000:
        return 50
    if p < 200000:
        return 100
    if p < 500000:
        return 500
    return 1000


def round_to_tick(price: float, market: str = "us") -> float:
    """매매가를 시장 호가단위로 라운딩.

    US 는 0.01 단위, KR 은 가격대별 적용.
    버림(매수) vs 올림(매도) 구분 없이 반올림 — 호출자가 ±0.5% 마진을 이미 적용한 가격이므로.
    """
    if market == "kr":
        tick = tick_size_kospi(price)
        return round(price / tick) * tick
    return round(float(price), 2)


def round_buy_to_tick(price: float, market: str = "us") -> float:
    """매수 LIMIT 가격을 호가단위에 맞춰 내림 (안전 — 너무 비싸게 사지 않도록).

    상한가 검증은 별도. 가격이 0 이하면 그대로 반환."""
    p = float(price)
    if p <= 0:
        return p
    if market == "kr":
        tick = tick_size_kospi(p)
        return (int(p / tick)) * tick
    # US: 0.01 단위 내림
    return int(p * 100) / 100.0


def round_sell_to_tick(price: float, market: str = "us") -> float:
    """매도 LIMIT 가격을 호가단위에 맞춰 올림 (안전 — 너무 싸게 팔지 않도록)."""
    p = float(price)
    if p <= 0:
        return p
    if market == "kr":
        tick = tick_size_kospi(p)
        return (int((p + tick - 1) / tick)) * tick
    return (int(p * 100 + 0.999)) / 100.0
