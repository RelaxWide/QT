"""
PyKRX / FDR / yfinance 의 한국 OHLCV 데이터 한계선 (시작 가능 연도) 찾기.

각 년도별로 짧은 범위 (한 달) 호출해서 빈 응답인지 확인.

사용:
    python scripts/check_ohlcv_boundary.py
"""
from __future__ import annotations

TEST_TICKER = "005930"  # 삼성전자
YEARS = [2008, 2010, 2012, 2014, 2015, 2018, 2020]


def check_pykrx():
    print("\n[1] PyKRX get_market_ohlcv_by_date — 범위 (1월)")
    from pykrx import stock
    for y in YEARS:
        try:
            df = stock.get_market_ohlcv_by_date(f"{y}0102", f"{y}0131", TEST_TICKER)
            if df.empty:
                print(f"  {y}-01: ❌ 빈 응답")
            else:
                print(f"  {y}-01: ✅ {df.shape[0]} 일, 시작 close={df.iloc[0]['종가']:>9,.0f}")
        except Exception as e:
            print(f"  {y}-01: ⚠️ {e}")


def check_fdr():
    print("\n[2] FinanceDataReader DataReader")
    import FinanceDataReader as fdr
    for y in YEARS:
        try:
            df = fdr.DataReader(TEST_TICKER, f"{y}-01-01", f"{y}-01-31")
            if df.empty:
                print(f"  {y}-01: ❌ 빈 응답")
            else:
                print(f"  {y}-01: ✅ {df.shape[0]} 일, 시작 close={df.iloc[0]['Close']:>9,.0f}")
        except Exception as e:
            print(f"  {y}-01: ⚠️ {e}")


def check_yfinance():
    print("\n[3] yfinance (Yahoo Finance)")
    try:
        import yfinance as yf
    except ImportError:
        print("  ⚠️ yfinance 미설치 — 'pip install yfinance' 필요")
        return
    yf_ticker = TEST_TICKER + ".KS"   # 한국주식 yahoo suffix
    for y in YEARS:
        try:
            df = yf.download(yf_ticker, start=f"{y}-01-01", end=f"{y}-02-01", progress=False, auto_adjust=False)
            if df.empty:
                print(f"  {y}-01: ❌ 빈 응답")
            else:
                close = df["Close"].iloc[0]
                if hasattr(close, "item"):
                    close = close.item()
                print(f"  {y}-01: ✅ {df.shape[0]} 일, 시작 close={close:>9,.0f}")
        except Exception as e:
            print(f"  {y}-01: ⚠️ {e}")


if __name__ == "__main__":
    print("=" * 70)
    print(f"OHLCV 한계선 탐색 — {TEST_TICKER} (삼성전자)")
    print("=" * 70)
    check_pykrx()
    check_fdr()
    check_yfinance()
    print()
    print("=" * 70)
    print("결론: 가장 이른 ✅ 연도 = 백테스트 가능 시작 연도")
