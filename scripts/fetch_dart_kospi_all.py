"""
KOSPI 전종목 × 다년도 DART 재무제표 일괄 fetch.

- 캐시 활용 (이미 있으면 skip)
- 종목·연도 별 parquet 캐시 (`data/raw/kr/dart/{ticker}/{year}_annual.parquet`)
- panel 형태 변환 (`data/raw/kr/dart_panel/{year}.parquet`) — fundamentals_kr 패턴

소요 시간: KOSPI 838종목 × 12년 ≈ 약 5,000 API call. 평균 2초/call = 약 3~4시간.

사용:
    python scripts/fetch_dart_kospi_all.py --start 2013 --end 2024
    python scripts/fetch_dart_kospi_all.py --resume      # 캐시 누락분만
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.dart_kr import fetch_financials, compute_derived_metrics, CACHE_DART


PANEL_DIR = Path("data/raw/kr/dart_panel")


def fetch_year(tickers: list[str], year: int, resume: bool = True) -> pd.DataFrame:
    """단일 연도 KOSPI 전종목 DART fetch — 캐시 우선."""
    from tqdm import tqdm
    rows = []
    n_cached = 0
    n_fetched = 0
    n_failed = 0
    pbar = tqdm(tickers, desc=f"DART {year}", ncols=100,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}")
    for t in pbar:
        cache_path = CACHE_DART / t / f"{year}_annual.parquet"
        if cache_path.exists() and resume:
            try:
                df = pd.read_parquet(cache_path)
                fin = df.iloc[0].to_dict()
                if fin.get("revenue") is not None:
                    n_cached += 1
                    rows.append(fin)
                    pbar.set_postfix({"cached": n_cached, "new": n_fetched, "fail": n_failed})
                    continue
            except Exception:
                pass
        try:
            fin = fetch_financials(t, year, "annual")
            if fin and fin.get("revenue") is not None:
                rows.append(fin)
                n_fetched += 1
            else:
                n_failed += 1
        except Exception:
            n_failed += 1
        pbar.set_postfix({"cached": n_cached, "new": n_fetched, "fail": n_failed})
    pbar.close()
    print(f"  → year={year}: cached={n_cached}  new={n_fetched}  fail={n_failed}  total fetched={len(rows)}")

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).set_index("ticker")

    # 유도 지표 추가 (전년도와 비교)
    derived_rows = []
    for t in df.index:
        fin = df.loc[t].to_dict()
        # 전년도 fetch (캐시만)
        prev_path = CACHE_DART / t / f"{year-1}_annual.parquet"
        fin_prev = None
        if prev_path.exists():
            try:
                fin_prev = pd.read_parquet(prev_path).iloc[0].to_dict()
            except Exception:
                pass
        derived = compute_derived_metrics(fin, fin_prev)
        d_row = {"ticker": t}
        d_row.update({k: v for k, v in fin.items() if k != "ticker"})
        d_row.update(derived)
        derived_rows.append(d_row)
    panel = pd.DataFrame(derived_rows).set_index("ticker")

    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    out = PANEL_DIR / f"{year}.parquet"
    panel.to_parquet(out)
    print(f"  Saved: {out} ({len(panel)} 종목)")
    return panel


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=int, default=2013)
    p.add_argument("--end",   type=int, default=2024)
    p.add_argument("--resume", action="store_true", default=True)
    args = p.parse_args()

    print("=" * 70)
    print(f"DART KOSPI 일괄 fetch: {args.start} ~ {args.end} ({args.end-args.start+1}년)")
    print("=" * 70)

    tickers = get_kospi_all_tickers()
    print(f"KOSPI 종목수: {len(tickers)}")
    print(f"예상 API call: {len(tickers) * (args.end - args.start + 1):,}회")

    summary = []
    for year in range(args.start, args.end + 1):
        df = fetch_year(tickers, year, resume=args.resume)
        summary.append({"year": year, "n_companies": len(df)})

    print("\n=== 연도별 fetch 결과 ===")
    print(pd.DataFrame(summary).to_string(index=False))
    print(f"\nPanel 저장: {PANEL_DIR}/*.parquet")


if __name__ == "__main__":
    main()
