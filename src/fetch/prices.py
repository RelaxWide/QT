"""
가격 데이터 fetch — 시장 (US/KR) 통합 진입.

- market="us" (기본): yfinance 사용 (기존 로직 그대로)
- market="kr": src.fetch.prices_kr 로 위임 (PyKRX + FDR 폴백)

기존 호출부 (fetch_prices(ticker, start, end, refresh) 시그니처) 는 무수정.
"""
from pathlib import Path
import pandas as pd
import yfinance as yf
from tqdm import tqdm

CACHE_DIR = Path("data/raw")


def _last_trading_day() -> pd.Timestamp:
    """오늘 또는 가장 최근 거래일 (주말 → 금요일)."""
    today = pd.Timestamp.today().normalize()
    offset = pd.offsets.BDay(0)  # 당일이 영업일이면 그대로, 아니면 직전 영업일
    return (today + offset) if today.weekday() < 5 else today - pd.offsets.BDay(1)


def fetch_prices(
    ticker: str,
    start: str,
    end: str | None = None,
    refresh: bool = False,
    cache_dir: str | Path | None = None,
    market: str = "us",
) -> pd.DataFrame:
    """단일 종목 일봉 OHLCV. market 으로 데이터 소스 분기."""
    if market == "kr":
        from src.fetch.prices_kr import fetch_prices_kr
        return fetch_prices_kr(ticker, start, end, refresh=refresh)

    # US (기본): yfinance
    _cache = Path(cache_dir) if cache_dir else CACHE_DIR
    _cache.mkdir(parents=True, exist_ok=True)
    safe = ticker.replace("^", "_").replace("/", "_")
    cache_path = _cache / f"{safe}.parquet"

    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        # 30 거래일 이내 캐시는 유효 (백테스트 리서치 중에는 최신성 불필요)
        # 실시간 페이퍼/실거래에서는 --refresh 명시
        if not cached.empty and cached.index[-1] >= _last_trading_day() - pd.offsets.BDay(30):
            return cached

    raw = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    if raw.empty:
        return raw

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [c.lower() for c in raw.columns]
    raw.index.name = "date"
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    raw.to_parquet(cache_path)
    return raw


def fetch_all(
    tickers: list[str],
    start: str,
    end: str | None = None,
    min_bars: int = 252,
    refresh: bool = False,
    cache_dir: str | Path | None = None,
    market: str = "us",
) -> dict[str, pd.DataFrame]:
    """다종목 일괄 fetch. market 으로 데이터 소스 분기."""
    if market == "kr":
        from src.fetch.prices_kr import fetch_all_kr
        return fetch_all_kr(tickers, start, end, min_bars=min_bars, refresh=refresh)

    result: dict[str, pd.DataFrame] = {}
    for t in tqdm(tickers, desc="Downloading"):
        try:
            df = fetch_prices(t, start, end, refresh, cache_dir=cache_dir, market="us")
            if not df.empty and len(df) >= min_bars:
                result[t] = df
        except Exception:
            pass
    return result
