"""
한국 주식 일봉 OHLCV 조회. PyKRX 1차, FinanceDataReader 폴백.

캐시: data/raw/kr/{TICKER}.parquet (US 와 별도 디렉토리)
컬럼: open, high, low, close, volume (소문자 표준화)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.fetch.prices import _last_trading_day   # 동일 함수 재사용

CACHE_DIR_KR = Path("data/raw/kr")
INDEX_TICKERS = {                                  # PyKRX 지수 코드 매핑
    "^KS11":   ("INDEX", "1001"),   # KOSPI Composite
    "^KS200":  ("INDEX", "1028"),   # KOSPI 200
    "^VKOSPI": ("INDEX", "1228"),   # VKOSPI 변동성 지수
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼 표준화 — open/high/low/close/volume 소문자."""
    rename_map = {
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
        "시가": "open", "고가": "high", "저가": "low",
        "종가": "close", "거래량": "volume",
    }
    df = df.rename(columns=rename_map)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "date"
    return df


def _fetch_pykrx(ticker: str, start: str, end: str) -> pd.DataFrame:
    from pykrx import stock as _kx

    s = pd.Timestamp(start).strftime("%Y%m%d")
    e = pd.Timestamp(end).strftime("%Y%m%d")

    # 지수 vs 일반 종목 분기
    if ticker in INDEX_TICKERS:
        _, idx_code = INDEX_TICKERS[ticker]
        df = _kx.get_index_ohlcv(s, e, idx_code)
    else:
        df = _kx.get_market_ohlcv(s, e, ticker)
    if df is None or df.empty:
        return pd.DataFrame()
    return _normalize_columns(df)


def _fetch_fdr(ticker: str, start: str, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr

    # FDR 은 ticker 그대로 — KOSPI 종목은 6자리 코드, 지수는 KS11/KS200
    fdr_ticker = ticker
    if ticker == "^KS11":
        fdr_ticker = "KS11"
    elif ticker == "^KS200":
        fdr_ticker = "KS200"
    elif ticker == "^VKOSPI":
        fdr_ticker = "VKOSPI"   # FDR 에 없을 수 있음 — 폴백 실패 허용

    df = fdr.DataReader(fdr_ticker, start, end)
    if df is None or df.empty:
        return pd.DataFrame()
    return _normalize_columns(df)


def fetch_prices_kr(
    ticker: str,
    start: str,
    end: str | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """KR 단일 종목 일봉 조회. PyKRX → FDR 폴백, parquet 캐시."""
    CACHE_DIR_KR.mkdir(parents=True, exist_ok=True)
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    safe = ticker.replace("^", "_").replace("/", "_")
    cache_path = CACHE_DIR_KR / f"{safe}.parquet"

    if cache_path.exists() and not refresh:
        try:
            cached = pd.read_parquet(cache_path)
            if not cached.empty and cached.index[-1] >= _last_trading_day() - pd.offsets.BDay(30):
                return cached
        except Exception:
            pass

    df: pd.DataFrame = pd.DataFrame()
    last_err: Exception | None = None
    for fn in (_fetch_pykrx, _fetch_fdr):
        try:
            df = fn(ticker, start, end)
            if not df.empty:
                break
        except Exception as e:
            last_err = e
            continue

    if df.empty:
        if last_err is not None:
            raise RuntimeError(f"KR price fetch 실패 ({ticker}): {last_err}")
        return df

    df.to_parquet(cache_path)
    return df


def fetch_all_kr(
    tickers: list[str],
    start: str,
    end: str | None = None,
    min_bars: int = 252,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """KR 다종목 일괄 조회. min_bars 미만은 제외 (신규상장)."""
    from tqdm import tqdm
    result: dict[str, pd.DataFrame] = {}
    for t in tqdm(tickers, desc="KR Downloading"):
        try:
            df = fetch_prices_kr(t, start, end, refresh)
            if not df.empty and len(df) >= min_bars:
                result[t] = df
        except Exception:
            pass
    return result
