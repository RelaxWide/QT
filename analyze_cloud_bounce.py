"""
구름 터치 후 반등 패턴 분석

각 트레이드에서:
  1. 진입 후 언제 senkou_a에 처음 닿는지 (cloud_touch_day)
  2. 터치 이후 몇 거래일 후 고점에 도달하는지
  3. 고점 대비 몇 %나 올라가는지
  4. 고점 후 언제 터치가격 아래로 돌아오는지
→ 분포를 시각화하고 최적 청산 타이밍 추천
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from src.fetch.prices import fetch_all
from src.indicators.ichimoku import ichimoku


def find_cloud_touch(df_sym, ich, entry_date, max_look=20):
    """진입일 이후 가격이 senkou_a에 처음 닿는 날 반환."""
    idx = df_sym.index
    if entry_date not in idx:
        return None, None
    start_i = idx.get_loc(entry_date)
    sa = ich["senkou_a"]

    for i in range(start_i, min(start_i + max_look, len(idx))):
        d = idx[i]
        if d not in sa.index:
            continue
        sa_val = sa.at[d]
        if pd.isna(sa_val):
            continue
        # 종가가 구름 상단 아래로 내려오면 터치로 판단
        if df_sym.at[d, "close"] <= sa_val * 1.002:
            return d, sa_val
    return None, None


def analyze_post_touch(df_sym, touch_date, touch_price, max_bars=30):
    """터치 이후 가격 움직임 분석."""
    idx = df_sym.index
    if touch_date not in idx:
        return None
    ti = idx.get_loc(touch_date)
    post = df_sym.iloc[ti + 1: ti + 1 + max_bars]
    if len(post) < 3:
        return None

    closes = post["close"].values
    highs  = post["high"].values

    # 고점 (high 기준)
    peak_bar  = int(np.argmax(highs))
    peak_pct  = (highs[peak_bar] - touch_price) / touch_price * 100

    # 고점 후 터치가격 아래로 돌아오는 시점
    fall_bar = max_bars
    for j in range(peak_bar + 1, len(closes)):
        if closes[j] < touch_price:
            fall_bar = j
            break

    # 연속 상승일 수 (고점까지)
    up_streak = 0
    for j in range(peak_bar):
        if closes[j + 1] > closes[j]:
            up_streak += 1
        else:
            break

    return {
        "days_to_peak":  peak_bar,
        "peak_pct":      round(peak_pct, 2),
        "fall_bar":      fall_bar,
        "up_streak":     up_streak,
        "bounce_exists": peak_pct > 1.0,  # 1% 이상 반등
    }


def main():
    trades_path = Path("backtest_results/phase4_v2_reg10_trades.csv")
    if not trades_path.exists():
        print("trades CSV 없음 — run_phase4_v2.py 먼저 실행 필요")
        return

    trades = pd.read_csv(trades_path)
    cfg    = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p      = cfg["phase4_v2_anticipatory"]

    syms   = trades["symbol"].unique().tolist()
    start  = cfg["data"]["start_date"]
    end    = cfg["data"]["end_date"]

    print(f"Loading price data for {len(syms)} symbols...")
    price_data = fetch_all(syms, start, end, min_bars=100)

    print("Computing ichimoku...")
    cloud_cache = {}
    for sym, df in price_data.items():
        cloud_cache[sym] = ichimoku(
            df,
            p.get("tenkan_period", 9),
            p.get("kijun_period", 26),
            p.get("senkou_b_period", 52),
            p.get("chikou_offset", 26),
        )

    # ── 분석 루프 ────────────────────────────────────────────────────────
    results = []
    no_touch = 0

    for _, trade in trades.iterrows():
        sym        = trade["symbol"]
        entry_date = pd.Timestamp(trade["entry_date"])
        r_mult     = trade["r_multiple"]

        if sym not in price_data or sym not in cloud_cache:
            continue

        df_sym = price_data[sym]
        ich    = cloud_cache[sym]

        touch_date, touch_price = find_cloud_touch(df_sym, ich, entry_date, max_look=30)
        if touch_date is None:
            no_touch += 1
            continue

        analysis = analyze_post_touch(df_sym, touch_date, touch_price, max_bars=60)
        if analysis is None:
            continue

        analysis["symbol"]        = sym
        analysis["entry_date"]    = entry_date
        analysis["touch_date"]    = touch_date
        analysis["touch_price"]   = touch_price
        analysis["r_multiple"]    = r_mult
        analysis["entry_to_touch"]= df_sym.index.get_loc(touch_date) - df_sym.index.get_loc(entry_date)
        results.append(analysis)

    df_res = pd.DataFrame(results)
    print(f"\n총 {len(trades)} 트레이드 중:")
    print(f"  구름 터치 확인: {len(df_res)}건")
    print(f"  구름 미도달:    {no_touch}건")

    if df_res.empty:
        print("분석할 데이터 없음")
        return

    bounce = df_res[df_res["bounce_exists"]]
    no_bounce = df_res[~df_res["bounce_exists"]]

    print(f"\n반등 발생 (>1%): {len(bounce)}건 ({len(bounce)/len(df_res)*100:.1f}%)")
    print(f"반등 미발생:      {len(no_bounce)}건 ({len(no_bounce)/len(df_res)*100:.1f}%)")

    print(f"\n── 반등 발생 케이스 통계 ──")
    for col, label in [
        ("entry_to_touch", "진입→터치 일수"),
        ("days_to_peak",   "터치→고점 일수"),
        ("peak_pct",       "터치→고점 상승%"),
        ("fall_bar",       "고점→되돌림 일수"),
        ("up_streak",      "연속 상승일 수"),
    ]:
        s = bounce[col]
        print(f"  {label:20s}: 중앙값={s.median():.1f}, 평균={s.mean():.1f}, "
              f"25%={s.quantile(0.25):.1f}, 75%={s.quantile(0.75):.1f}")

    # ── 진입→터치 일수 분포 ─────────────────────────────────────────────
    print(f"\n── 진입→구름터치 일수 분포 ──")
    dist = df_res["entry_to_touch"].value_counts().sort_index()
    for days, cnt in dist.items():
        bar = "█" * int(cnt / len(df_res) * 30)
        print(f"  {days:2d}일: {bar} {cnt}건 ({cnt/len(df_res)*100:.1f}%)")

    # ── 터치→고점 일수 분포 (반등 케이스만) ──────────────────────────────
    print(f"\n── 구름터치→고점 일수 분포 (반등 케이스) ──")
    dist2 = bounce["days_to_peak"].value_counts().sort_index()
    for days, cnt in dist2.items():
        bar = "█" * int(cnt / len(bounce) * 30)
        print(f"  {days:2d}일: {bar} {cnt}건 ({cnt/len(bounce)*100:.1f}%)")

    # ── 상승% 구간별 빈도 ────────────────────────────────────────────────
    print(f"\n── 터치→고점 상승% 분포 (반등 케이스) ──")
    bins = [1, 2, 3, 5, 8, 12, 20, 100]
    labels = ["1-2%","2-3%","3-5%","5-8%","8-12%","12-20%","20%+"]
    bounce["pct_bin"] = pd.cut(bounce["peak_pct"], bins=bins, labels=labels)
    for label, cnt in bounce["pct_bin"].value_counts().sort_index().items():
        bar = "█" * int(cnt / len(bounce) * 30)
        print(f"  {label:7s}: {bar} {cnt}건 ({cnt/len(bounce)*100:.1f}%)")

    # ── 터치 후 N일 수익률 분포 ──────────────────────────────────────────
    print(f"\n-- Touch -> N-day return distribution --")
    for n_days in [3, 5, 7, 10, 15]:
        rets = []
        for _, row in df_res.iterrows():
            sym        = row["symbol"]
            touch_date = row["touch_date"]
            tp         = row["touch_price"]
            if sym not in price_data or touch_date not in price_data[sym].index:
                continue
            df_s = price_data[sym]
            ti   = df_s.index.get_loc(touch_date)
            end_i = ti + n_days
            if end_i >= len(df_s):
                continue
            close_n = df_s["close"].iloc[end_i]
            rets.append((close_n - tp) / tp * 100)
        if not rets:
            continue
        r = pd.Series(rets)
        pos_pct = (r > 0).mean() * 100
        print(f"  {n_days:2d}d: median={r.median():+.1f}%  mean={r.mean():+.1f}%  "
              f">0: {pos_pct:.0f}%  25%={r.quantile(0.25):+.1f}%  75%={r.quantile(0.75):+.1f}%")

    # ── 시각화 ──────────────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    axes[0, 0].hist(df_res["entry_to_touch"], bins=range(0, 22), color="#2196F3",
                    edgecolor="white", alpha=0.8)
    axes[0, 0].set_title("Entry -> Cloud Touch (days)")
    axes[0, 0].set_xlabel("trading days")
    axes[0, 0].axvline(df_res["entry_to_touch"].median(), color="red",
                       linestyle="--", label=f"중앙값 {df_res['entry_to_touch'].median():.0f}일")
    axes[0, 0].legend()

    axes[0, 1].hist(bounce["days_to_peak"], bins=range(0, 22), color="#4CAF50",
                    edgecolor="white", alpha=0.8)
    axes[0, 1].set_title("Cloud Touch -> Peak (days, bounce only)")
    axes[0, 1].set_xlabel("trading days")
    axes[0, 1].axvline(bounce["days_to_peak"].median(), color="red",
                       linestyle="--", label=f"중앙값 {bounce['days_to_peak'].median():.0f}일")
    axes[0, 1].legend()

    axes[1, 0].hist(bounce["peak_pct"].clip(0, 20), bins=20, color="#FF9800",
                    edgecolor="white", alpha=0.8)
    axes[1, 0].set_title("Touch -> Peak gain % (bounce only, 20% cap)")
    axes[1, 0].set_xlabel("%")
    axes[1, 0].axvline(bounce["peak_pct"].median(), color="red",
                       linestyle="--", label=f"중앙값 {bounce['peak_pct'].median():.1f}%")
    axes[1, 0].legend()

    axes[1, 1].hist(bounce["fall_bar"].clip(0, 60), bins=20, color="#9C27B0",
                    edgecolor="white", alpha=0.8)
    axes[1, 1].set_title("Peak -> Price below touch (days)")
    axes[1, 1].set_xlabel("trading days (60d cap)")
    axes[1, 1].axvline(bounce["fall_bar"].median(), color="red",
                       linestyle="--", label=f"중앙값 {bounce['fall_bar'].median():.0f}일")
    axes[1, 1].legend()

    plt.suptitle("Phase 4-v2 Cloud Bounce Pattern Analysis", fontsize=13)
    plt.tight_layout()
    out = Path("backtest_results/cloud_bounce_analysis.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n차트 저장: {out}")
    plt.show()

    # ── 청산 타이밍 권고 ────────────────────────────────────────────────
    med_peak  = bounce["days_to_peak"].median()
    med_gain  = bounce["peak_pct"].median()
    med_fall  = bounce["fall_bar"].median()
    pct_75    = bounce["days_to_peak"].quantile(0.75)

    print(f"\n── 청산 타이밍 권고 ──")
    print(f"  반등 중앙값 고점 도달: {med_peak:.0f}거래일, 중앙 상승폭: {med_gain:.1f}%")
    print(f"  75%가 {pct_75:.0f}거래일 이내에 고점 도달")
    print(f"  고점 후 터치가격 복귀: 중앙 {med_fall:.0f}거래일")
    print(f"\n  → 권고: 터치 후 {int(med_peak)+1}~{int(pct_75)+1}거래일 내 익절 목표가 설정")
    print(f"  → 현재 max_hold_bars=15. "
          f"{'적합' if 15 >= pct_75 + 5 else '너무 길어 단축 권장'}")


if __name__ == "__main__":
    main()
