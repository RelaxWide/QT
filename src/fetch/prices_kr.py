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
        # adjusted=True 명시: 권리락/배당락 조정된 수정주가
        # (PyKRX 기본값도 True 지만 safety 차원에서 명시)
        df = _kx.get_market_ohlcv(s, e, ticker, adjusted=True)
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


def _fetch_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    """yfinance 폴백 — PyKRX/FDR 가 2015년 이전 데이터 미제공 시.

    한국주식 yahoo suffix:
      - KOSPI: 6자리코드.KS (예: 005930.KS)
      - KOSDAQ: 6자리코드.KQ (예: 035420.KQ)
      - KOSPI 지수: ^KS11 (그대로)
    """
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()

    if ticker.startswith("^"):
        yf_ticker = ticker
    elif len(ticker) == 6 and ticker.isdigit():
        yf_ticker = f"{ticker}.KS"
    else:
        yf_ticker = ticker

    end_str = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(yf_ticker, start=start, end=end_str, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
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
            # 캐시 유효 조건: ① 최신 (마지막 30 영업일 이내) ② 요청 시작점까지 커버
            start_ts = pd.Timestamp(start)
            fresh_enough = cached.index[-1] >= _last_trading_day() - pd.offsets.BDay(30)
            covers_start = cached.index[0] <= start_ts + pd.offsets.BDay(5)  # 5일 여유 (영업일 휴장)
            if not cached.empty and fresh_enough and covers_start:
                return cached
            # 시작점 못 커버 (예: 2008부터 필요한데 캐시는 2015 부터) → 다시 fetch
        except Exception:
            pass

    df: pd.DataFrame = pd.DataFrame()
    last_err: Exception | None = None
    # 지수(^로 시작)는 FDR 우선 — PyKRX 의 KRX 인증이 깨지면 짧은 응답 반환되는 경우 있음
    # yfinance 는 2015년 이전 데이터를 위한 최종 폴백
    is_index = ticker.startswith("^") or ticker in INDEX_TICKERS
    if is_index:
        fns = (_fetch_fdr, _fetch_pykrx, _fetch_yfinance)
    else:
        fns = (_fetch_pykrx, _fetch_fdr, _fetch_yfinance)
    for fn in fns:
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


def _yfinance_batch(
    missing_tickers: list[str],
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    """yfinance 배치 다운로드 — 한 번에 다수 종목 fetch (단일 종목 호출 대비 5~10배 빠름)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    if not missing_tickers:
        return {}

    yf_tickers = []
    yf_to_orig = {}
    for t in missing_tickers:
        if t.startswith("^"):
            yf_t = t
        elif len(t) == 6 and t.isdigit():
            yf_t = f"{t}.KS"
        else:
            yf_t = t
        yf_tickers.append(yf_t)
        yf_to_orig[yf_t] = t

    end_str = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"  yfinance batch: {len(yf_tickers)} 종목 download ...")
    df_all = yf.download(yf_tickers, start=start, end=end_str, progress=True,
                          auto_adjust=False, threads=True, group_by="ticker")
    if df_all is None or df_all.empty:
        return {}

    result = {}
    for yf_t in yf_tickers:
        try:
            if isinstance(df_all.columns, pd.MultiIndex):
                sub = df_all[yf_t]
            else:
                sub = df_all
            if sub.empty or sub["Close"].dropna().empty:
                continue
            norm = _normalize_columns(sub.dropna(how="all"))
            if not norm.empty:
                result[yf_to_orig[yf_t]] = norm
        except KeyError:
            continue
        except Exception:
            continue
    return result


def fetch_all_kr(
    tickers: list[str],
    start: str,
    end: str | None = None,
    min_bars: int = 252,
    refresh: bool = False,
    use_yf_batch: bool = True,
) -> dict[str, pd.DataFrame]:
    """KR 다종목 일괄 조회. min_bars 미만은 제외 (신규상장).

    start < 2015: yfinance batch 우선 (PyKRX/FDR 가 2015년 이전 데이터 미제공).
    start ≥ 2015: PyKRX/FDR 개별 호출 (기존 방식).
    인덱스(^*) 는 항상 fetch_prices_kr 사용 — FDR/PyKRX 가 더 정확.
    """
    from tqdm import tqdm
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    result: dict[str, pd.DataFrame] = {}
    start_ts = pd.Timestamp(start)
    # yfinance batch 모드 트리거:
    #   1) start < 2015 (PyKRX/FDR 가 데이터 미제공)
    #   2) start < 2022 (PyKRX 가 일부 종목에서 timeout 으로 hang — 캐시 우선 + batch 폴백이 안전)
    needs_yf_batch = use_yf_batch and start_ts < pd.Timestamp("2022-01-01")

    if needs_yf_batch:
        # 인덱스는 PyKRX/FDR (yfinance 인덱스는 데이터 품질 떨어짐)
        index_tickers = [t for t in tickers if t.startswith("^")]
        stock_tickers = [t for t in tickers if not t.startswith("^")]
        for t in tqdm(index_tickers, desc="KR Index"):
            try:
                df = fetch_prices_kr(t, start, end, refresh)
                if not df.empty and len(df) >= min_bars:
                    result[t] = df
            except Exception:
                pass

        # 개별 종목: 캐시 hit 먼저 확인 → 누락분만 yfinance batch
        missing = []
        for t in tqdm(stock_tickers, desc="Cache check"):
            safe = t.replace("^", "_").replace("/", "_")
            cache_path = CACHE_DIR_KR / f"{safe}.parquet"
            if cache_path.exists() and not refresh:
                try:
                    cached = pd.read_parquet(cache_path)
                    if not cached.empty and cached.index[0] <= start_ts + pd.offsets.BDay(20) and len(cached) >= min_bars:
                        result[t] = cached
                        continue
                except Exception:
                    pass
            missing.append(t)

        if missing:
            print(f"  캐시 미커버 {len(missing)}종목 → yfinance batch 다운로드 (한 번에)")
            yf_result = _yfinance_batch(missing, start, end)
            for t, df in yf_result.items():
                if len(df) >= min_bars:
                    safe = t.replace("^", "_").replace("/", "_")
                    cache_path = CACHE_DIR_KR / f"{safe}.parquet"
                    df.to_parquet(cache_path)
                    result[t] = df
            print(f"  yfinance: {len(yf_result)}/{len(missing)} 종목 받음")
        return result

    # start ≥ 2015: 기존 방식 (PyKRX/FDR 개별)
    for t in tqdm(tickers, desc="KR Downloading"):
        try:
            df = fetch_prices_kr(t, start, end, refresh)
            if not df.empty and len(df) >= min_bars:
                result[t] = df
        except Exception:
            pass
    return result
