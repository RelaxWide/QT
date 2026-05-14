"""
Phase 4-v2 트레이드 차트 시각화

매수/매도 시점, 일목구름, 손절선을 차트로 표현.
사용법: python visualize_phase4_v2.py
"""
import random
from pathlib import Path

import matplotlib
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
import yaml

matplotlib.rcParams["font.family"] = "DejaVu Sans"

from src.fetch.prices import fetch_all
from src.indicators.ichimoku import ichimoku


def plot_trade(ax, df, trade, cloud_df, pre_bars=25, post_bars=30):
    entry_dt = pd.Timestamp(trade["entry_date"])
    exit_dt  = pd.Timestamp(trade["exit_date"])

    if entry_dt not in df.index or exit_dt not in df.index:
        return False

    entry_loc = df.index.get_loc(entry_dt)
    exit_loc  = df.index.get_loc(exit_dt)

    start_loc = max(0, entry_loc - pre_bars)
    end_loc   = min(len(df) - 1, exit_loc + post_bars)
    sub       = df.iloc[start_loc: end_loc + 1]
    sub_cloud = cloud_df.reindex(sub.index)

    x = range(len(sub))
    dates = sub.index

    # ── 캔들스틱 ────────────────────────────────────────────────────────
    for xi, (_, row) in enumerate(sub.iterrows()):
        color = "#26a69a" if row["close"] >= row["open"] else "#ef5350"
        ax.plot([xi, xi], [row["low"], row["high"]], color=color, linewidth=0.8)
        ax.add_patch(plt.Rectangle(
            (xi - 0.3, min(row["open"], row["close"])),
            0.6, abs(row["close"] - row["open"]),
            color=color, zorder=2,
        ))

    # ── 구름 ────────────────────────────────────────────────────────────
    sa = sub_cloud["senkou_a"].values
    sb = sub_cloud["senkou_b"].values
    for xi in range(len(sub) - 1):
        if pd.isna(sa[xi]) or pd.isna(sb[xi]):
            continue
        top = max(sa[xi], sb[xi])
        bot = min(sa[xi], sb[xi])
        color = "#ffcc8033" if sa[xi] >= sb[xi] else "#ef535033"
        ax.fill_between([xi, xi + 1], [bot, bot], [top, top], color=color, linewidth=0)

    # 구름 경계선
    ax.plot(x, sa, color="#ff9900", linewidth=0.8, alpha=0.7)
    ax.plot(x, sb, color="#26c6da", linewidth=0.8, alpha=0.7)

    # ── 손절선 ──────────────────────────────────────────────────────────
    stop_val = trade["stop"]
    ax.axhline(stop_val, color="#ef5350", linestyle="--", linewidth=0.8, alpha=0.7, label=f"Stop {stop_val:.2f}")

    # ── 매수 마커 ────────────────────────────────────────────────────────
    entry_xi = entry_loc - start_loc
    ax.annotate("BUY", xy=(entry_xi, trade["entry_price"]),
                xytext=(entry_xi, trade["entry_price"] * 0.97),
                arrowprops=dict(arrowstyle="->", color="#26a69a", lw=1.5),
                color="#26a69a", fontsize=7, fontweight="bold", ha="center")

    # ── 매도 마커 ────────────────────────────────────────────────────────
    exit_xi   = exit_loc - start_loc
    exit_price = trade["exit_price"]
    reason    = trade["exit_reason"]
    r_mult    = trade["r_multiple"]
    color_exit = "#26a69a" if r_mult > 0 else "#ef5350"

    ax.annotate(f"SELL\n{reason}\n{r_mult:+.2f}R",
                xy=(exit_xi, exit_price),
                xytext=(exit_xi, exit_price * 1.03),
                arrowprops=dict(arrowstyle="->", color=color_exit, lw=1.5),
                color=color_exit, fontsize=7, fontweight="bold", ha="center")

    # ── 진입~청산 구간 음영 ──────────────────────────────────────────────
    ax.axvspan(entry_xi, exit_xi, alpha=0.07,
               color="#26a69a" if r_mult > 0 else "#ef5350")

    # ── x축 날짜 레이블 ──────────────────────────────────────────────────
    step = max(1, len(sub) // 8)
    ax.set_xticks(list(range(0, len(sub), step)))
    ax.set_xticklabels(
        [dates[i].strftime("%m/%d") for i in range(0, len(sub), step)],
        fontsize=7, rotation=30,
    )

    sym    = trade["symbol"]
    ed     = entry_dt.strftime("%Y-%m-%d")
    xd     = exit_dt.strftime("%Y-%m-%d")
    title_color = "#26a69a" if r_mult > 0 else "#ef5350"
    ax.set_title(f"{sym}  |  {ed} → {xd}  |  {reason}  |  {r_mult:+.2f}R",
                 fontsize=8, color=title_color)
    ax.grid(alpha=0.2)
    ax.yaxis.set_tick_params(labelsize=7)
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=None,
                        help="실험 태그 (예: t30_t1_thick3_gap15). 없으면 가장 최근 파일 사용")
    parser.add_argument("--csv", default=None, help="직접 CSV 경로 지정")
    parser.add_argument("--n", type=int, default=10, help="시각화할 트레이드 수 (기본 10)")
    args = parser.parse_args()

    results_dir = Path("backtest_results")
    if args.csv:
        trades_csv = Path(args.csv)
    elif args.tag:
        trades_csv = results_dir / f"phase4_v2_reg10_{args.tag}_trades.csv"
    else:
        # 가장 최근 trades CSV 자동 선택
        candidates = sorted(results_dir.glob("phase4_v2_reg10_*_trades.csv"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print("trades CSV 없음 — 먼저 run_phase4_v2.py 실행 필요")
            return
        trades_csv = candidates[0]

    if not trades_csv.exists():
        print(f"파일 없음: {trades_csv}")
        print("사용 가능한 파일:")
        for f in sorted(results_dir.glob("phase4_v2_reg10_*_trades.csv")):
            print(f"  --tag {f.stem.removeprefix('phase4_v2_reg10_').removesuffix('_trades')}")
        return

    print(f"시각화 대상: {trades_csv.name}")
    trades_df = pd.read_csv(trades_csv)

    trades_df = pd.read_csv(trades_csv)

    # 다양한 결과 섞어서 10개 선택 (win/loss, 여러 exit reason)
    wins   = trades_df[trades_df["r_multiple"] > 0]
    losses = trades_df[trades_df["r_multiple"] <= 0]
    cloud_exits = trades_df[trades_df["exit_reason"].str.contains("cloud")]
    time_stops  = trades_df[trades_df["exit_reason"] == "time_stop"]
    target_hits = trades_df[trades_df["exit_reason"].str.contains("t1|t2")]

    n_total = args.n
    n_wins   = max(1, n_total // 3)
    n_losses = max(1, n_total // 3)
    n_cloud  = max(1, n_total // 5)
    n_time   = max(1, n_total // 10)
    n_target = max(1, n_total // 10)

    picks = []
    for grp, n in [(wins, n_wins), (losses, n_losses), (cloud_exits, n_cloud),
                   (time_stops, n_time), (target_hits, n_target)]:
        sample = grp.sample(min(n, len(grp)), random_state=42)
        picks.append(sample)
    selected = pd.concat(picks).drop_duplicates().head(n_total)

    # 가격 데이터 로드
    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    syms  = selected["symbol"].unique().tolist()
    print(f"Loading price data for: {syms}")
    price_data = fetch_all(syms, start, end, min_bars=100)

    # 구름 계산
    p = cfg["phase4_v2_anticipatory"]
    cloud_data = {}
    for sym, df in price_data.items():
        ich = ichimoku(df,
                       p.get("tenkan_period", 9),
                       p.get("kijun_period", 26),
                       p.get("senkou_b_period", 52),
                       p.get("chikou_offset", 26))
        cloud_data[sym] = ich[["senkou_a", "senkou_b"]]

    # ── 플롯 ────────────────────────────────────────────────────────────
    n_trades = len(selected)
    n_cols   = 2
    n_rows   = (n_trades + 1) // n_cols
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(16, n_rows * 4))
    axes = axes.flatten()

    plotted = 0
    for _, trade in selected.iterrows():
        sym = trade["symbol"]
        if sym not in price_data or sym not in cloud_data:
            continue
        ax = axes[plotted]
        ok = plot_trade(ax, price_data[sym], trade, cloud_data[sym])
        if ok:
            plotted += 1

    # 빈 axes 숨기기
    for i in range(plotted, len(axes)):
        axes[i].set_visible(False)

    # 범례
    legend_items = [
        mpatches.Patch(color="#ff990066", label="상승 구름 (sa>sb)"),
        mpatches.Patch(color="#ef535066", label="하락 구름 (sb>sa)"),
        mpatches.Patch(color="#ff9900", label="senkou_a (구름 상단)"),
        mpatches.Patch(color="#26c6da", label="senkou_b (구름 하단)"),
    ]
    fig.legend(handles=legend_items, loc="lower center", ncol=4, fontsize=8, framealpha=0.8)

    plt.suptitle("Phase 4-v2 트레이드 시각화 (상승=녹색, 하락=적색)", fontsize=11, y=1.01)
    plt.tight_layout(rect=[0, 0.03, 1, 1])

    out = Path("backtest_results/phase4_v2_trades_chart.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"차트 저장: {out}")
    plt.show()


if __name__ == "__main__":
    main()
