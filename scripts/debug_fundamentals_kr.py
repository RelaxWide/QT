"""
KR 펀더멘털 데이터 fetch 검증.

KRX_ID/KRX_PW 환경변수가 설정되어 있으면 정상 동작.
미설정 시 빈 응답 안내 후 종료.

사용:
    python scripts/debug_fundamentals_kr.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.fetch.fundamentals_kr import (
    fetch_pykrx_fundamentals,
    fetch_market_cap_kr,
    build_fundamentals_panel,
    derive_value_factors,
    derive_quality_factors_pykrx_only,
)


def main():
    has_krx_auth = bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))
    print(f"KRX_ID set: {bool(os.getenv('KRX_ID'))}, KRX_PW set: {bool(os.getenv('KRX_PW'))}")

    if not has_krx_auth:
        print("[WARN] KRX_ID/KRX_PW 미설정 - PyKRX fundamental API 동작 안 함")
        print("       https://data.krx.co.kr/ 회원 가입 후 환경변수 설정")
        print()

    print("=" * 70)
    print("1. fetch_pykrx_fundamentals (2025-05-02 KOSPI 전체)")
    print("=" * 70)
    df = fetch_pykrx_fundamentals("20250502")
    print(f"   shape: {df.shape}")
    if not df.empty:
        print(f"   cols: {df.columns.tolist()}")
        print(df.head(3))

    print()
    print("=" * 70)
    print("2. fetch_market_cap_kr (2025-05-02 KOSPI 전체)")
    print("=" * 70)
    df2 = fetch_market_cap_kr("20250502")
    print(f"   shape: {df2.shape}")
    if not df2.empty:
        print(f"   cols: {df2.columns.tolist()}")
        print(df2.head(3))

    print()
    print("=" * 70)
    print("3. build_fundamentals_panel (3개 분기, 3개 종목)")
    print("=" * 70)
    dates = ["20240515", "20240815", "20241115"]
    tickers = ["005930", "000660", "035420"]
    panel = build_fundamentals_panel(dates, tickers)
    print(f"   panel 종목 수: {len(panel)}")
    for t in tickers:
        if t in panel:
            print(f"\n   {t}:")
            print(panel[t])

    if not panel:
        print("[STOP] panel 빈 dict - KRX 인증 필요")
        return

    print()
    print("=" * 70)
    print("4. derive_value_factors (2024-08-15)")
    print("=" * 70)
    val = derive_value_factors(panel, pd.Timestamp("20240815"))
    print(f"   shape: {val.shape}")
    print(val.head())


if __name__ == "__main__":
    main()
