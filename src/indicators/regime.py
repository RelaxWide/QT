"""
시장 체제 (regime) 필터.

US: SPY 50MA/200MA + ^VIX
KR: ^KS11 50MA/200MA + (옵션) VKOSPI

trade_ok = 단기 추세 위 + 변동성 임계 이하
size_factor = 장기 추세 위면 1.0, 아래면 0.5 (포지션 축소)
"""
import pandas as pd
from src.fetch.prices import fetch_prices


def compute_regime(
    start: str,
    end: str | None = None,
    ma_short: int = 50,
    ma_long: int = 200,
    vix_threshold: float = 30.0,
    market: str = "us",
) -> pd.DataFrame:
    """시장 체제 시그널.

    market="us" → SPY + ^VIX (기존 동작)
    market="kr" → ^KS11 + (옵션) ^VKOSPI. VKOSPI 미가용 시 vix=0 처리.
    """
    if market == "kr":
        index_ticker = "^KS11"
        vix_ticker = "^VKOSPI"
        vix_threshold_eff = vix_threshold if vix_threshold != 30.0 else 35.0
    else:
        index_ticker = "SPY"
        vix_ticker = "^VIX"
        vix_threshold_eff = vix_threshold

    idx = fetch_prices(index_ticker, start, end, market=market)
    if idx is None or idx.empty:
        raise RuntimeError(f"regime: {index_ticker} 데이터 비어있음")

    cl = idx["close"]
    regime = pd.DataFrame(index=idx.index)
    regime[f"{market}_above_short_ma"] = cl > cl.rolling(ma_short).mean()
    regime[f"{market}_above_long_ma"]  = cl > cl.rolling(ma_long).mean()
    # 기존 코드 호환을 위해 spy_above_50ma / spy_above_200ma alias 도 유지
    regime["spy_above_50ma"]  = regime[f"{market}_above_short_ma"]
    regime["spy_above_200ma"] = regime[f"{market}_above_long_ma"]

    try:
        vix = fetch_prices(vix_ticker, start, end, market=market)
        vix_series = vix["close"].reindex(idx.index, method="ffill")
    except Exception:
        # VIX/VKOSPI 미가용 시 0 처리 → 변동성 필터 비활성화 (MA 만 사용)
        vix_series = pd.Series(0.0, index=idx.index)

    regime["vix"] = vix_series
    regime["trade_ok"] = regime[f"{market}_above_short_ma"] & (vix_series <= vix_threshold_eff)
    regime["size_factor"] = regime[f"{market}_above_long_ma"].map({True: 1.0, False: 0.5})
    return regime
