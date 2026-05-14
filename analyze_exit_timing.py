"""
Phase 4-v2 매도 타이밍 분석

trades CSV의 각 진입 시점을 기준으로,
'진입 후 N일째 종가에 매도'한다고 가정했을 때 R-multiple을 계산.
N=1~30 각각에 대해 평균 R, 승률, 누적수익을 비교.

사용법:
  python analyze_exit_timing.py --tag v3_box_reach3
  python analyze_exit_timing.py --tag v3_box_reach3 --max-bars 40
"""
import argparse
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

matplotlib.rcParams["font.family"] = "DejaVu Sans"

from src.fetch.prices import fetch_all


def analyze(trades_df: pd.DataFrame, price_data: dict, max_bars: int, slippage: float = 0.001):
    """
    각 trade에 대해 N=1..max_bars 일째 종가 청산 시 R-multiple 계산.
    반환: (n_bars, n_trades) 형태 DataFrame, 행=trade, 열=hold_days
    """
    n_trades = len(trades_df)
    r_matrix = np.full((n_trades, max_bars + 1), np.nan)

    for ti, trade in trades_df.iterrows():
        sym = trade["symbol"]
        if sym not in price_data:
            continue
        df = price_data[sym]
        entry_date = pd.Timestamp(trade["entry_date"])
        if entry_date not in df.index:
            continue
        entry_loc = df.index.get_loc(entry_date)
        entry_px = trade["entry_price"]
        stop_px = trade["stop"]
        init_risk = entry_px - stop_px
        if init_risk <= 0:
            continue

        for n in range(1, max_bars + 1):
            exit_loc = entry_loc + n - 1  # n일째 종가 = entry_loc + (n-1) (entry는 1일차)
            if exit_loc >= len(df):
                break
            exit_px = df["close"].iloc[exit_loc] * (1 - slippage)
            r_mult = (exit_px - entry_px) / init_risk
            r_matrix[ti, n] = r_mult

    return r_matrix


def summarize(r_matrix: np.ndarray, max_bars: int):
    """N별 통계 계산"""
    rows = []
    for n in range(1, max_bars + 1):
        col = r_matrix[:, n]
        valid = col[~np.isnan(col)]
        if len(valid) == 0:
            continue
        wins = valid[valid > 0]
        losses = valid[valid <= 0]
        pf_num = wins.sum() if len(wins) > 0 else 0
        pf_den = abs(losses.sum()) if len(losses) > 0 else 0
        pf = pf_num / pf_den if pf_den > 0 else float("inf")
        rows.append({
            "hold_days": n,
            "n_trades":  len(valid),
            "win_rate":  len(wins) / len(valid),
            "avg_r":     valid.mean(),
            "median_r":  np.median(valid),
            "avg_win_r": wins.mean() if len(wins) > 0 else 0,
            "avg_loss_r":losses.mean() if len(losses) > 0 else 0,
            "profit_factor": pf,
            "total_r":   valid.sum(),
            "std_r":     valid.std(),
            "sharpe_proxy": valid.mean() / valid.std() if valid.std() > 0 else 0,
        })
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=None,
                        help="trades CSV 태그 (예: v3_box_reach3)")
    parser.add_argument("--csv", default=None, help="trades CSV 경로 직접 지정")
    parser.add_argument("--max-bars", type=int, default=30,
                        help="최대 보유 일수 (기본 30)")
    parser.add_argument("--slippage", type=float, default=0.001,
                        help="매도 슬리피지 (기본 0.001 = 0.1%)")
    args = parser.parse_args()

    results_dir = Path("backtest_results")
    if args.csv:
        trades_csv = Path(args.csv)
    elif args.tag:
        trades_csv = results_dir / f"phase4_v2_reg10_{args.tag}_trades.csv"
    else:
        candidates = sorted(results_dir.glob("phase4_v2_reg10_*_trades.csv"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            print("trades CSV 없음")
            return
        trades_csv = candidates[0]

    if not trades_csv.exists():
        print(f"파일 없음: {trades_csv}")
        return

    print(f"분석 대상: {trades_csv.name}")
    trades_df = pd.read_csv(trades_csv).reset_index(drop=True)
    print(f"  총 트레이드: {len(trades_df)}건")

    # 가격 데이터 로드
    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    syms  = trades_df["symbol"].unique().tolist()
    print(f"  종목 수: {len(syms)}")
    print("가격 데이터 로드 중...")
    price_data = fetch_all(syms, start, end, min_bars=100)

    # 분석
    print(f"각 N={1}~{args.max_bars}일 보유 시뮬레이션...")
    r_matrix = analyze(trades_df, price_data, args.max_bars, args.slippage)
    summary = summarize(r_matrix, args.max_bars)

    # 출력
    print(f"\n── 매도 타이밍 분석 결과 (slippage={args.slippage*100:.1f}%) ──")
    print(summary.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    # 최적 N 표시
    best_avg = summary.loc[summary["avg_r"].idxmax()]
    best_total = summary.loc[summary["total_r"].idxmax()]
    best_sharpe = summary.loc[summary["sharpe_proxy"].idxmax()]
    print(f"\n── 최적 보유일 ──")
    print(f"  avg_R 최대:        N={int(best_avg['hold_days'])}일  avg_R={best_avg['avg_r']:.3f}  WR={best_avg['win_rate']:.1%}")
    print(f"  total_R 최대:      N={int(best_total['hold_days'])}일  total_R={best_total['total_r']:.1f}")
    print(f"  sharpe_proxy 최대: N={int(best_sharpe['hold_days'])}일  proxy={best_sharpe['sharpe_proxy']:.3f}")

    # CSV 저장
    out_csv = results_dir / f"exit_timing_{trades_csv.stem}.csv"
    summary.to_csv(out_csv, index=False)
    print(f"\n결과 CSV: {out_csv}")

    # 차트
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    ax = axes[0, 0]
    ax.plot(summary["hold_days"], summary["avg_r"], marker="o", label="avg_R")
    ax.plot(summary["hold_days"], summary["median_r"], marker="s", alpha=0.5, label="median_R")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.set_xlabel("Hold days (N)"); ax.set_ylabel("R-multiple")
    ax.set_title("Average R per Hold Days"); ax.legend(); ax.grid(alpha=0.3)

    ax = axes[0, 1]
    ax.plot(summary["hold_days"], summary["total_r"], marker="o", color="purple")
    ax.set_xlabel("Hold days (N)"); ax.set_ylabel("Total R (sum)")
    ax.set_title("Cumulative R per Hold Days"); ax.grid(alpha=0.3)

    ax = axes[1, 0]
    ax.plot(summary["hold_days"], summary["win_rate"], marker="o", color="green")
    ax.axhline(0.5, color="gray", linewidth=0.5)
    ax.set_xlabel("Hold days (N)"); ax.set_ylabel("Win rate")
    ax.set_title("Win Rate per Hold Days"); ax.grid(alpha=0.3)

    ax = axes[1, 1]
    ax.plot(summary["hold_days"], summary["sharpe_proxy"], marker="o", color="orange")
    ax.axhline(0, color="gray", linewidth=0.5)
    ax.set_xlabel("Hold days (N)"); ax.set_ylabel("Mean/Std (Sharpe proxy)")
    ax.set_title("Risk-adjusted Return per Hold Days"); ax.grid(alpha=0.3)

    plt.suptitle(f"Exit Timing Analysis — {trades_csv.stem}", fontsize=11)
    plt.tight_layout()

    out_png = results_dir / f"exit_timing_{trades_csv.stem}.png"
    plt.savefig(out_png, dpi=120, bbox_inches="tight")
    print(f"차트 PNG: {out_png}")
    plt.show()


if __name__ == "__main__":
    main()
