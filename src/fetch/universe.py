import io
import requests
import pandas as pd

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}


def get_sp500_tickers() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    df = pd.read_html(io.StringIO(resp.text))[0]
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()


def get_russell2000_tickers() -> list[str]:
    """
    iShares IWM ETF 구성종목에서 Russell 2000 티커 추출.
    실패 시 Stooq 목록으로 폴백.
    """
    try:
        url = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), skiprows=9)
        tickers = df["Ticker"].dropna().astype(str).tolist()
        tickers = [t.strip() for t in tickers if t.strip() and t != "nan" and t != "-"]
        if len(tickers) > 100:
            return tickers
    except Exception:
        pass

    # 폴백: Wikipedia Russell 1000 목록에서 S&P500 제외
    try:
        sp500 = set(get_sp500_tickers())
        url = "https://en.wikipedia.org/wiki/Russell_1000_Index"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        df = pd.read_html(io.StringIO(resp.text))[0]
        col = [c for c in df.columns if "tick" in c.lower() or "symbol" in c.lower()]
        if col:
            tickers = df[col[0]].dropna().str.replace(".", "-", regex=False).tolist()
            return [t for t in tickers if t not in sp500]
    except Exception:
        pass

    raise RuntimeError("Russell 2000 티커 목록 수집 실패 — 네트워크 확인")
