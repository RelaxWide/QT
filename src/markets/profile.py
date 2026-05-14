"""
MarketProfile — 시장별 메타데이터 + 동작 분기 키.

사용:
    from src.markets import get_profile
    p = get_profile("kr")
    p.index_ticker     # '^KS11'
    p.fee_model.buy    # 0.00015
    p.tick_size_fn(50000)  # 50
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.markets.tick_size import tick_size_kospi


@dataclass(frozen=True)
class FeeModel:
    """매매 비용 모델 (수익률 차감 비율)."""
    buy:  float  # 매수 수수료율
    sell: float  # 매도 수수료율 (거래세 포함)
    description: str = ""


@dataclass(frozen=True)
class MarketProfile:
    code:            str               # "us" | "kr"
    name:            str               # 표시명
    currency:        str               # "USD" | "KRW"
    currency_symbol: str               # "$" | "₩"
    index_ticker:    str               # SPY | ^KS11 (벤치마크/regime용)
    vix_ticker:      str | None        # ^VIX | None (KR 은 VKOSPI 가 yfinance 미지원이라 PyKRX 별도 처리)
    calendar_freq:   str               # "W-WED" | "W-FRI" (주봉 리샘플 기준)
    trading_hours_kst: tuple[str, str] # ("22:30", "05:00") | ("09:00", "15:30")
    fee_model:       FeeModel
    tick_size_fn:    Callable[[float], float]  # 호가단위 함수
    min_price:       float             # 최소 가격 필터 (US $5, KR ₩5,000)
    universe_fn_key: str               # "sp500" | "kospi200" — universe loader 분기 키
    kis_tr_key:      str               # KIS TR_IDS dict 의 키 prefix ("us" → mock_us/prod_us)


_US_PROFILE = MarketProfile(
    code="us",
    name="United States (S&P 500)",
    currency="USD",
    currency_symbol="$",
    index_ticker="SPY",
    vix_ticker="^VIX",
    calendar_freq="W-WED",
    trading_hours_kst=("22:30", "05:00"),     # DST 기준
    fee_model=FeeModel(buy=0.0025, sell=0.0025, description="키움 0.25% 매수/매도"),
    tick_size_fn=lambda p: 0.01,
    min_price=5.0,
    universe_fn_key="sp500",
    kis_tr_key="us",
)


_KR_PROFILE = MarketProfile(
    code="kr",
    name="Korea (KOSPI 200)",
    currency="KRW",
    currency_symbol="₩",
    index_ticker="^KS11",                      # yfinance KOSPI Composite
    vix_ticker=None,                           # VKOSPI 는 PyKRX 로 별도 (선택)
    calendar_freq="W-FRI",                     # 한국 주봉은 금요일 종가
    trading_hours_kst=("09:00", "15:30"),
    fee_model=FeeModel(
        buy=0.00015,                           # 증권사 0.015%
        sell=0.00015 + 0.0018 + 0.00023,      # 0.015% + 거래세 0.18% + 농특세 0.023%
        description="증권사 0.015% + 매도 거래세 0.18% + 농특세 0.023%",
    ),
    tick_size_fn=tick_size_kospi,
    min_price=5000.0,
    universe_fn_key="kospi200",
    kis_tr_key="kr",
)


_PROFILES = {"us": _US_PROFILE, "kr": _KR_PROFILE}


def get_profile(market: str) -> MarketProfile:
    """시장 코드로 프로파일 조회. 'us' | 'kr' 지원."""
    m = market.lower()
    if m not in _PROFILES:
        raise ValueError(f"Unknown market: {market!r}. Use 'us' or 'kr'.")
    return _PROFILES[m]
