"""
PyKRX 가 2008년 이전 KOSPI 데이터 (가격 + fundamentals) 를 제공하는지 확인.

사용:
    python scripts/check_pykrx_history.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pykrx import stock


TEST_DATES = ["20080102", "20100104", "20120102", "20140102"]
TEST_TICKER = "005930"   # 삼성전자 (가장 오래 상장된 종목 중 하나)


def main():
    print("=" * 80)
    print(f"PyKRX 과거 데이터 가용성 — {TEST_TICKER} (삼성전자)")
    print("=" * 80)

    print("\n[1] OHLCV (가격) 데이터")
    for d in TEST_DATES:
        try:
            df = stock.get_market_ohlcv_by_date(d, d, TEST_TICKER)
            if df.empty:
                print(f"  {d}: 빈 응답 ❌")
            else:
                row = df.iloc[0]
                print(f"  {d}: open={row['시가']:>8,.0f}  close={row['종가']:>8,.0f}  vol={row['거래량']:>12,} ✅")
        except Exception as e:
            print(f"  {d}: 오류 {e} ❌")

    print("\n[2] 펀더멘털 (PER/PBR/EPS/BPS)")
    for d in TEST_DATES:
        try:
            df = stock.get_market_fundamental_by_ticker(d, market="KOSPI")
            if df.empty:
                print(f"  {d}: 빈 응답 ❌")
            else:
                if TEST_TICKER in df.index:
                    row = df.loc[TEST_TICKER]
                    print(f"  {d}: PER={row['PER']:>6.2f}  PBR={row['PBR']:>5.2f}  "
                          f"EPS={row['EPS']:>8,.0f}  BPS={row['BPS']:>8,.0f}  / KOSPI {len(df)}종목 ✅")
                else:
                    print(f"  {d}: 삼성전자 없음 (KOSPI {len(df)}종목)")
        except Exception as e:
            print(f"  {d}: 오류 {e} ❌")

    print("\n[3] 시가총액 (market cap)")
    for d in TEST_DATES:
        try:
            df = stock.get_market_cap_by_ticker(d, market="KOSPI")
            if df.empty:
                print(f"  {d}: 빈 응답 ❌")
            else:
                if TEST_TICKER in df.index:
                    row = df.loc[TEST_TICKER]
                    print(f"  {d}: 시총={row['시가총액']:>17,}  주식수={row['상장주식수']:>13,} / KOSPI {len(df)}종목 ✅")
                else:
                    print(f"  {d}: 삼성전자 없음")
        except Exception as e:
            print(f"  {d}: 오류 {e} ❌")

    print("\n[4] KOSPI 인덱스 (^KS11 ≈ '1001')")
    # PyKRX 는 KOSPI 인덱스 코드 1001
    for d in TEST_DATES:
        try:
            df = stock.get_index_ohlcv_by_date(d, d, "1001")
            if df.empty:
                print(f"  {d}: 빈 응답 ❌")
            else:
                row = df.iloc[0]
                print(f"  {d}: close={row['종가']:>10,.2f}  vol={row['거래량']:>15,} ✅")
        except Exception as e:
            print(f"  {d}: 오류 {e} ❌")

    print()
    print("=" * 80)
    print("결론:")
    print("  모두 ✅ = 2008년부터 전체 KOSPI 백테스트 가능")
    print("  일부 ❌ = 가능한 시작 연도 확인 필요")


if __name__ == "__main__":
    main()
