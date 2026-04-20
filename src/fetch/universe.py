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
