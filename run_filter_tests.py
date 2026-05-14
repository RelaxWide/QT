"""
Phase 4-v3 필터 일괄 백테스트.

사용법:
  py -3.13 run_filter_tests.py              # RS Line 완화 변형 탐색 (단기, config.yaml)
  py -3.13 run_filter_tests.py --mode fip   # FIP top30 구간·연도별 검증
  py -3.13 run_filter_tests.py --dry-run    # 실제 실행 없이 커맨드만 출력

베이스라인: Trend template + Pullback dry-up (1.0x) + ATR contraction (0.9) + 52w high rank 30%
"""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


# ── 베이스라인 (best v3 후보) ───────────────────────────────────────────────
BASE_ARGS = [
    "--use-trend-template",
    "--breakout-volume-mult", "1.2",
    "--pullback-volume-max-ratio", "1.0",
    "--max-recent-atr-to-long-atr-ratio", "0.9",
    "--use-52w-high-rank",
    "--high52-rank-top-pct", "30",
]

# ── RS Line 재설계 변형 ──────────────────────────────────────────────────────
# 핵심 문제: Phase4는 풀백 진입 전략 → 신호 발생 시 RS Line은 돌파 고점 대비 하락 상태.
# "현재 RS가 52주 최고점" 요구는 구조적으로 불가능 → 0 signals 원인.
#
# 올바른 접근 3가지:
#   Mode A: RS Line이 자신의 MA 위 (추세 중)
#   Mode B: RS Line이 N일 전보다 높음 (기울기 양수)
#   Mode C at_breakout: 돌파 바 시점의 RS Line이 N주 신고점 (원래 개념에 충실)
RS_VARIANTS: list[tuple[str, str, list[str]]] = [
    (
        "f0_baseline",
        "베이스라인 (비교 기준)",
        [],
    ),
    # Mode A: RS Line > 자신의 N일 MA
    (
        "f1_rs_ma20",
        "[A] RS Line > 20일 MA",
        ["--use-rs-line-new-high", "--rs-line-above-ma-period", "20"],
    ),
    (
        "f1_rs_ma10",
        "[A] RS Line > 10일 MA",
        ["--use-rs-line-new-high", "--rs-line-above-ma-period", "10"],
    ),
    # Mode B: RS Line 기울기 양수
    (
        "f1_rs_slope20",
        "[B] RS Line 기울기 양수 / 20일",
        ["--use-rs-line-new-high", "--rs-line-slope-positive-days", "20"],
    ),
    (
        "f1_rs_slope10",
        "[B] RS Line 기울기 양수 / 10일",
        ["--use-rs-line-new-high", "--rs-line-slope-positive-days", "10"],
    ),
    # Mode C at_breakout: 돌파 바 기준으로 RS 신고점 확인
    (
        "f1_rs_bo_63d",
        "[C] 돌파 바 RS 63일 신고점",
        ["--use-rs-line-new-high", "--rs-line-check-at-breakout",
         "--rs-line-period", "63"],
    ),
    (
        "f1_rs_bo_63d_99",
        "[C] 돌파 바 RS 63일 신고점 99%",
        ["--use-rs-line-new-high", "--rs-line-check-at-breakout",
         "--rs-line-period", "63", "--rs-line-near-high-ratio", "0.99"],
    ),
    (
        "f1_rs_bo_252d_99",
        "[C] 돌파 바 RS 252일 신고점 99%",
        ["--use-rs-line-new-high", "--rs-line-check-at-breakout",
         "--rs-line-near-high-ratio", "0.99"],
    ),
]

# ── FIP top30 구간·연도별 검증 ────────────────────────────────────────────────
FIP_BEST_ARGS = ["--use-fip-filter", "--fip-min-rank", "0.70"]

FIP_PERIODS: list[tuple[str, str, str, str]] = [
    ("fip_p2000_2009", "2000-2009", "2000-01-01", "2009-12-31"),
    ("fip_p2010_2019", "2010-2019", "2010-01-01", "2019-12-31"),
    ("fip_p2020_2026", "2020-2026", "2020-01-01", "2026-12-31"),
]

BASE_PERIODS: list[tuple[str, str, str, str]] = [
    ("base_p2000_2009", "2000-2009", "2000-01-01", "2009-12-31"),
    ("base_p2010_2019", "2010-2019", "2010-01-01", "2019-12-31"),
    ("base_p2020_2026", "2020-2026", "2020-01-01", "2026-12-31"),
]


# ── 보고서 파싱 (핵심 지표 섹션만 읽음) ────────────────────────────────────
_MD_ROW = re.compile(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|")

_METRIC_MAP = {
    "총 트레이드 수": "trades",
    "승률": "wr",
    "Profit Factor": "pf",
    "평균 R": "avg_r",
    "총 수익률": "ret",
    "최대 낙폭": "mdd",
    "Sharpe": "sharpe",
}


def _parse_report(path: Path) -> dict:
    result: dict = {}
    in_metrics = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if "## 핵심 지표" in line:
            in_metrics = True
            continue
        if line.startswith("## ") and in_metrics:
            break  # 게이트 체크 섹션 시작 → 중단
        if not in_metrics:
            continue
        # 정확히 2개 열인 행만 허용 (| key | value | 형태)
        if line.count("|") != 3:
            continue
        m = _MD_ROW.match(line)
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        key = _METRIC_MAP.get(label)
        if key:
            num = re.sub(r"[%R\s]", "", value)
            try:
                result[key] = float(num)
            except ValueError:
                result[key] = value
    return result


def _find_report(tag: str) -> Path | None:
    candidates = list(Path("backtest_results").glob(f"phase4_v3_{tag}_report.md"))
    return candidates[0] if candidates else None


# ── 거래 CSV 연도별 집계 ────────────────────────────────────────────────────
def _yearly_summary(tag: str) -> dict[int, dict]:
    candidates = list(Path("backtest_results").glob(f"phase4_v3_{tag}_trades.csv"))
    if not candidates:
        return {}
    rows = list(csv.DictReader(candidates[0].open(encoding="utf-8")))
    by_year: dict[int, list] = defaultdict(list)
    for row in rows:
        try:
            yr = int(row["entry_date"][:4])
            by_year[yr].append(float(row["r_multiple"]))
        except (KeyError, ValueError):
            pass
    result = {}
    for yr, rs in sorted(by_year.items()):
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")
        result[yr] = {
            "trades": len(rs),
            "wr": len(wins) / len(rs) if rs else 0,
            "avg_r": sum(rs) / len(rs) if rs else 0,
            "pf": pf,
        }
    return result


# ── 실행 헬퍼 ────────────────────────────────────────────────────────────────
def _run(tag: str, extra_args: list[str], config: str, dry_run: bool,
         start_date: str | None = None, end_date: str | None = None) -> bool:
    cmd = [sys.executable, "run_phase4_v3.py", "--config", config, "--tag", tag]
    cmd += BASE_ARGS + extra_args
    if start_date:
        cmd += ["--start-date", start_date]
    if end_date:
        cmd += ["--end-date", end_date]
    print("  $ " + " ".join(cmd[2:]))  # python 경로 생략
    if dry_run:
        return False
    return subprocess.run(cmd).returncode == 0


# ── 출력 헬퍼 ────────────────────────────────────────────────────────────────
def _table(rows: list[tuple], header: tuple) -> None:
    col_w = [max(len(str(r[i])) for r in [header] + rows) + 2 for i in range(len(header))]

    def fmt(row):
        return " | ".join(str(v).ljust(w) for v, w in zip(row, col_w))

    sep = "-+-".join("-" * w for w in col_w)
    print(fmt(header))
    print(sep)
    for r in rows:
        print(fmt(r))


def _fmt(m: dict) -> tuple:
    return (
        int(m.get("trades", -1)),
        f"{m.get('wr', 0):.1f}",
        f"{m.get('pf', 0):.4f}",
        f"{m.get('avg_r', 0):.4f}",
        f"{m.get('ret', 0):.1f}",
        f"{m.get('mdd', 0):.2f}",
        f"{m.get('sharpe', 0):.4f}",
    )


# ── 모드: RS Line 완화 탐색 ──────────────────────────────────────────────────
def run_rs_mode(config: str, dry_run: bool) -> None:
    print(f"\n{'='*70}")
    print(f"[모드] RS Line 완화 변형 탐색 | {config}")
    print(f"{'='*70}")

    rows = []
    for tag, desc, extra in RS_VARIANTS:
        print(f"\n[{tag}] {desc}")
        _run(tag, extra, config, dry_run)
        report = _find_report(tag)
        if report and report.exists():
            m = _parse_report(report)
            rows.append((tag, desc[:38]) + _fmt(m))

    if rows:
        print(f"\n\n{'='*70}")
        print("RS Line 변형 비교표")
        print(f"{'='*70}")
        _table(rows, ("태그", "설명", "거래수", "WR%", "PF", "avgR", "수익%", "MDD%", "Sharpe"))
        print()


# ── 모드: FIP top30 구간·연도별 검증 ─────────────────────────────────────────
def run_fip_mode(dry_run: bool) -> None:
    config = "config_extended.yaml"
    print(f"\n{'='*70}")
    print(f"[모드] FIP top30 구간·연도별 검증 | {config}")
    print(f"{'='*70}")

    # 구간별 — 베이스라인 vs FIP top30
    period_rows = []
    for (btag, blabel, bstart, bend), (ftag, flabel, fstart, fend) in zip(
        BASE_PERIODS, FIP_PERIODS
    ):
        assert blabel == flabel
        print(f"\n[{blabel}] 베이스라인")
        _run(btag, [], config, dry_run, bstart, bend)
        print(f"[{blabel}] FIP top30")
        _run(ftag, FIP_BEST_ARGS, config, dry_run, fstart, fend)

        bm = _parse_report(_find_report(btag)) if _find_report(btag) else {}
        fm = _parse_report(_find_report(ftag)) if _find_report(ftag) else {}

        if bm:
            period_rows.append(("baseline", blabel) + _fmt(bm))
        if fm:
            period_rows.append(("FIP top30", flabel) + _fmt(fm))

    if period_rows:
        print(f"\n\n{'='*70}")
        print("구간별 비교 (Baseline vs FIP top30)")
        print(f"{'='*70}")
        _table(period_rows,
               ("전략", "구간", "거래수", "WR%", "PF", "avgR", "수익%", "MDD%", "Sharpe"))
        print()

    # 연도별 — 전체 구간에서 FIP top30 연도별 추이
    # (f4_fip_filter_top30은 이미 이전에 생성된 경우 재사용, 없으면 재실행)
    print(f"\n[전체 구간 FIP top30 연도별 추이]")
    full_tag = "f4_fip_filter_top30_full"
    _run(full_tag, FIP_BEST_ARGS, config, dry_run)
    base_full_tag = "f0_baseline_full"
    _run(base_full_tag, [], config, dry_run)

    fy = _yearly_summary(full_tag)
    by = _yearly_summary(base_full_tag)

    if fy or by:
        yr_rows = []
        all_years = sorted(set(fy) | set(by))
        for yr in all_years:
            b = by.get(yr, {})
            f = fy.get(yr, {})
            yr_rows.append((
                yr,
                b.get("trades", "-"), f"{b.get('wr', 0)*100:.0f}", f"{b.get('pf', 0):.2f}",
                f.get("trades", "-"), f"{f.get('wr', 0)*100:.0f}", f"{f.get('pf', 0):.2f}",
                "+" if f.get("pf", 0) > b.get("pf", 0) else "-" if f and b else "?",
            ))
        print(f"\n연도별 — Baseline vs FIP top30")
        _table(yr_rows,
               ("연도", "B거래", "BWR%", "BPF", "F거래", "FWR%", "FPF", "개선?"))
        print()


# ── 메인 ────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rs", "fip"], default="rs",
                    help="rs: RS Line 완화 탐색 (default) | fip: FIP top30 구간 검증")
    ap.add_argument("--short", action="store_true",
                    help="단기 구간 (config.yaml, 2015~). rs 모드에서만 적용.")
    ap.add_argument("--dry-run", action="store_true",
                    help="커맨드만 출력, 실제 실행 안 함")
    args = ap.parse_args()

    if args.mode == "rs":
        config = "config.yaml" if args.short else "config_extended.yaml"
        run_rs_mode(config, args.dry_run)
    else:
        run_fip_mode(args.dry_run)


if __name__ == "__main__":
    main()
