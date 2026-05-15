"""
KR 백테스트 진입 0건 원인 진단.

체크 항목:
1. KOSPI200 + ^KS11 데이터 로드 성공률
2. ^KS11 인덱스 vs 종목 인덱스 정합 (Wednesday 비율, 미정합 종목 수)
3. compute_scores 가 특정 일자에 빈 dict 반환하는지
4. 백테스트 엔진의 weekly_dates 추출이 정상인지
5. 첫 weekly_date 에서 점수 산출 가능 종목이 있는지
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yaml

from src.fetch.universe import get_kospi200_tickers
from src.fetch.prices import fetch_all
from src.strategy.clenow_momentum import compute_scores
from src.markets import get_profile


def main():
    profile = get_profile("kr")
    cfg     = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    cl_p = dict(cfg.get("clenow_strategy", {}))
    cl_p["min_price"]    = profile.min_price
    cl_p["index_ticker"] = profile.index_ticker

    print("=" * 70)
    print("1. 데이터 로드")
    print("=" * 70)
    tickers = [profile.index_ticker] + get_kospi200_tickers()
    print(f"   요청 종목 수: {len(tickers)} (인덱스 ^KS11 포함)")
    t0 = time.time()
    price_data = fetch_all(
        tickers, cfg["data"]["start_date"], cfg["data"]["end_date"],
        min_bars=cl_p["reg_lookback"] + cl_p["ma100_period"] + 10,
        market="kr",
    )
    print(f"   로드 성공: {len(price_data)} 종목 ({time.time()-t0:.1f}s)")
    print(f"   ^KS11 포함: {profile.index_ticker in price_data}")
    if profile.index_ticker not in price_data:
        print(f"   [STOP] ^KS11 데이터 없음 - 백테스트 불가")
        return

    print()
    print("=" * 70)
    print("2. ^KS11 vs 종목 인덱스 정합 검사")
    print("=" * 70)
    idx_df = price_data[profile.index_ticker]
    idx_dates = set(idx_df.index)
    print(f"   ^KS11 기간: {idx_df.index[0].date()} ~ {idx_df.index[-1].date()} ({len(idx_df)} bars)")

    # weekday 분포
    wd_counts = pd.Series([d.weekday() for d in idx_df.index]).value_counts().sort_index()
    print(f"   ^KS11 요일 분포 (월=0..일=6):")
    print(f"     {dict(wd_counts)}")

    # 종목 표본
    sample_syms = [s for s in list(price_data)[:8] if s != profile.index_ticker]
    for sym in sample_syms:
        df = price_data[sym]
        sym_dates = set(df.index)
        common = idx_dates & sym_dates
        only_sym = sym_dates - idx_dates
        only_idx = idx_dates - sym_dates
        print(f"   {sym}: total={len(df)}, common with ^KS11={len(common)}, "
              f"only_sym={len(only_sym)}, only_idx={len(only_idx)}")

    print()
    print("=" * 70)
    print("3. 백테스트 엔진의 weekly_dates 추출")
    print("=" * 70)
    all_dates = sorted(idx_df.index)
    weekly_dates = []
    seen_weeks = set()
    for d in all_dates:
        wk = (d.year, d.isocalendar()[1])
        if d.weekday() == 2:
            weekly_dates.append(d)
            seen_weeks.add(wk)
        elif d == all_dates[-1] and wk not in seen_weeks:
            weekly_dates.append(d)
    print(f"   수요일 개수: {len(weekly_dates)} / 전체 거래일 {len(all_dates)}")
    if weekly_dates:
        print(f"   첫 수요일: {weekly_dates[0].date()}")
        print(f"   마지막 수요일: {weekly_dates[-1].date()}")
    else:
        print(f"   [STOP] 수요일 0건 - 백테스트 불가 (요일 분포 확인 필요)")
        return

    print()
    print("=" * 70)
    print("4. compute_scores 샘플 호출 (앞 / 중간 / 끝 수요일 3건)")
    print("=" * 70)
    samples = [weekly_dates[len(weekly_dates)//3],
               weekly_dates[len(weekly_dates)//2],
               weekly_dates[-2] if len(weekly_dates) > 1 else weekly_dates[-1]]
    for date in samples:
        scores = compute_scores(price_data, date, cl_p)
        top5 = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print(f"   {date.date()}: 종목 {len(scores)}개 / 상위 5: {top5}")

    print()
    print("=" * 70)
    print("5. 첫 수요일 진입 직후 매수 흐름 점검")
    print("=" * 70)
    first_wed = weekly_dates[len(weekly_dates)//3]
    scores = compute_scores(price_data, first_wed, cl_p)
    if not scores:
        print(f"   [PROBLEM] {first_wed.date()} compute_scores 빈 dict — 진입 후보 없음")
    else:
        top = sorted(scores, key=lambda s: scores[s], reverse=True)[:20]
        # 다음 거래일(exec_date) 의 open 가격 확인
        date_idx = {d: i for i, d in enumerate(all_dates)}
        i = date_idx[first_wed]
        exec_date = None
        for off in range(1, 6):
            if i + off < len(all_dates):
                exec_date = all_dates[i + off]
                break
        print(f"   exec_date (다음 거래일): {exec_date.date() if exec_date else None}")
        n_ok = 0
        for sym in top:
            df_sym = price_data.get(sym)
            if df_sym is None or exec_date not in df_sym.index:
                continue
            open_px = df_sym.loc[exec_date, "open"]
            if pd.isna(open_px) or open_px <= 0:
                continue
            n_ok += 1
        print(f"   진입 가능 종목 (open>0): {n_ok}/{len(top)}")


if __name__ == "__main__":
    main()
