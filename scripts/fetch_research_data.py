"""
리서치용 장기 일별 가격 받기 (yfinance, 수정종가 + 원종가).

    python scripts/fetch_research_data.py ^GSPC ^IRX SPY SSO
→ research_data/<티커>.csv  (date, close, adj_close)
GitHub Actions 의 Research Data Fetch 워크플로에서 실행한다 (로컬 리서치 환경에서 Yahoo 접속이 막힐 때).
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

OUT = Path("research_data")


def main(tickers: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    for t in tickers:
        raw = yf.download(t, period="max", auto_adjust=False, progress=False)
        if raw.empty:
            print(f"[FAIL] {t}: 빈 응답")
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = pd.DataFrame({"close": raw["Close"], "adj_close": raw.get("Adj Close", raw["Close"])}).dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        name = t.replace("^", "_")
        df.to_csv(OUT / f"{name}.csv")
        print(f"[OK] {t}: {len(df)}행 {df.index[0].date()} ~ {df.index[-1].date()}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["^GSPC", "^IRX", "SPY", "SSO", "UPRO"])
