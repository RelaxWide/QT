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
) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = ticker.replace("^", "_").replace("/", "_")
    cache_path = CACHE_DIR / f"{safe}.parquet"

    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        # 5 거래일 이내 캐시는 유효 (백테스트에서 하루 차이는 무의미)
        if not cached.empty and cached.index[-1] >= _last_trading_day() - pd.offsets.BDay(5):
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
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for t in tqdm(tickers, desc="Downloading"):
        try:
            df = fetch_prices(t, start, end, refresh)
            if not df.empty and len(df) >= min_bars:
                result[t] = df
        except Exception:
            pass
    return result
