"""
KR 수급 사건 연구: "기관·외국인 누적 순매수 강도가 높고 주가는 아직 안 오른 종목" 이 이후에 더 오르는가.

매매 규칙을 만들기 전 단계 — 신호가 있는지만 본다 (비용·체결 미반영).
  대상   : 각 기준일의 시가총액 상위 200 (보통주, 관리종목·환기종목·SPAC 제외, 그 시점 기준)
  신호   : 과거 W 거래일 누적 순매수 수량 ÷ 상장주식수   (W = 20, 60 / 주체 = 기관, 외국인, 기관+외국인, 개인)
  결과   : 기준일 다음 날 종가 → H 거래일 뒤 종가 수익률 (H = 20, 60), 같은 날 대상 종목 평균 대비 초과수익
  통계   : 주간 기준일 전체의 평균, t 값은 Newey-West (보유 기간 겹침 보정)

사용: python scripts/kr_flow/event_study.py <flows.parquet> <marcap_cache_dir> [출처 이름]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import TOP_N, eligible, load_marcap  # noqa: E402

WINDOWS = (20, 60)
HORIZONS = (20, 60)
PERIODS = [("전체", "2015-01-01", "2026-12-31"), ("2015~2019", "2015-01-01", "2019-12-31"),
           ("2020~2022", "2020-01-01", "2022-12-31"), ("2023~2026", "2023-01-01", "2026-12-31")]


def build(flows: pd.DataFrame, mc: pd.DataFrame):
    mc = mc.copy()
    mc["elig"] = eligible(mc) & (mc["Volume"] > 0)
    dates = pd.DatetimeIndex(sorted(mc["Date"].unique()))
    piv = lambda col: mc.pivot_table(index="Date", columns="Code", values=col, aggfunc="last").reindex(dates)
    ret = piv("ChangesRatio") / 100.0
    cap = piv("Marcap")
    stocks = piv("Stocks")
    elig = piv("elig").fillna(False).astype(bool)
    f = flows.copy()
    f["date"] = pd.to_datetime(f["date"])
    fp = lambda col: f.pivot_table(index="date", columns="code", values=col, aggfunc="last").reindex(dates)
    inst, frgn, indv = fp("inst"), fp("frgn"), fp("indv")
    return dates, ret, cap, stocks, elig, inst, frgn, indv


def study(flows: pd.DataFrame, mc: pd.DataFrame, label: str) -> str:
    dates, ret, cap, stocks, elig, inst, frgn, indv = build(flows, mc)
    codes_flow = inst.columns
    # 대상: 그 날짜 전체 적격 종목 중 시총 상위 N → 그중 수급 자료가 있는 종목
    cap_e = cap.where(elig)
    rank = cap_e.rank(axis=1, ascending=False, method="first")
    in_top = rank <= TOP_N
    logret = np.log1p(ret.fillna(0.0))
    cum = logret.cumsum()
    out = [f"# KR 수급 사건 연구 — 출처: {label}", ""]
    out.append(f"- 가격·시총: marcap {dates[0].date()}~{dates[-1].date()} / 수급: {flows['date'].min()}~{flows['date'].max()}, "
               f"{flows['code'].nunique()}종목, {len(flows):,}행")

    has_flow = inst.notna() | frgn.notna()
    cover = (in_top.reindex(columns=codes_flow, fill_value=False) & has_flow).sum(axis=1) / in_top.sum(axis=1).clip(lower=1)
    cov = cover[(dates >= "2015-04-01")]
    out.append(f"- 시총 상위 {TOP_N} 중 수급 자료가 있는 비율: 평균 {cov.mean():.1%}, 최소 {cov.min():.1%}")

    sig_src = {"기관": inst, "외국인": frgn, "기관+외국인": inst.add(frgn, fill_value=0), "개인": indv}
    results = []
    for W in WINDOWS:
        past = cum - cum.shift(W)                                    # 과거 W 일 로그수익
        for H in HORIZONS:
            fwd = (cum.shift(-(H + 1)) - cum.shift(-1))              # t+1 종가 → t+1+H 종가
            fwd = np.expm1(fwd)
            for name, q in sig_src.items():
                share = (q / stocks.reindex(columns=q.columns)).fillna(0.0)
                sig = share.rolling(W, min_periods=W).sum()
                valid_days = [d for d in dates[W + 5::5] if d >= pd.Timestamp("2015-04-01") and d <= dates[-(H + 2)]]
                rows = []
                for d in valid_days:
                    m = in_top.loc[d].reindex(sig.columns, fill_value=False) & has_flow.loc[d] & sig.loc[d].notna()
                    cols = m.index[m]
                    if len(cols) < 100:
                        continue
                    s, r, p = sig.loc[d, cols], fwd.loc[d, cols], past.loc[d, cols]
                    ok = r.notna()
                    s, r, p = s[ok], r[ok], p[ok]
                    ex = r - r.mean()
                    qs = pd.qcut(s.rank(method="first"), 5, labels=False)
                    pt = pd.qcut(p.rank(method="first"), 3, labels=False)
                    row = {"date": d, "n": len(s), "ic": s.rank().corr(r.rank())}
                    for k in range(5):
                        row[f"q{k + 1}"] = ex[qs == k].mean()
                    top = qs == 4
                    for k, nm in enumerate(("저", "중", "고")):
                        row[f"top_past_{nm}"] = ex[top & (pt == k)].mean()
                        row[f"all_past_{nm}"] = ex[pt == k].mean()
                    row["top_pastret"] = np.expm1(p[top]).mean()
                    row["bot_pastret"] = np.expm1(p[qs == 0]).mean()
                    row["top_abs"] = r[top].mean()
                    row["univ_abs"] = r.mean()
                    rows.append(row)
                df = pd.DataFrame(rows).set_index("date")
                results.append((W, H, name, df))

    def stat(x: pd.Series, H: int):
        """모든 주간 기준일의 평균과 Newey-West t (보유 기간이 겹치므로 시차 H/5 주까지 자기상관 보정).
        처음엔 겹치지 않는 기준일만 골라 썼는데, 어느 주에서 시작하느냐에 따라 평균이 크게 달라져 버렸다."""
        x = x.dropna().to_numpy(dtype=float)
        n = len(x)
        if n < 20:
            return np.nan, np.nan, n
        L = max(1, H // 5)
        e = x - x.mean()
        var = (e @ e) / n
        for k in range(1, L + 1):
            var += 2 * (1 - k / (L + 1)) * (e[k:] @ e[:-k]) / n
        return x.mean(), x.mean() / np.sqrt(max(var, 1e-18) / n), n

    out += ["", "## 1. 수급 강도 5분위별 이후 초과수익 (전체 기간)", "",
            "초과수익 = 같은 날 대상 종목 평균 대비. q5 = 순매수 강도 최상위. 주간 기준일 전체 평균, t 는 Newey-West (보유 기간 겹침 보정).", "",
            "| 주체 | 누적 W | 보유 H | q1 | q2 | q3 | q4 | q5 | q5−q1 | t(q5−q1) | 순위상관 | 기준일 수 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for W, H, name, df in results:
        ls = df["q5"] - df["q1"]
        m, t, n = stat(ls, H)
        qm = [stat(df[f"q{k}"], H)[0] for k in range(1, 6)]
        out.append(f"| {name} | {W} | {H} | " + " | ".join(f"{v:+.2%}" for v in qm)
                   + f" | {m:+.2%} | {t:+.2f} | {df['ic'].mean():+.3f} | {n} |")

    out += ["", "## 2. 가설 직접 검증 — 수급 강도 최상위(q5) 안에서 '주가가 아직 안 오른' 종목", "",
            "과거 W 일 수익률로 3등분 (저 = 가장 덜 오른 종목). 값은 이후 H 일 초과수익, 괄호는 t.", "",
            "| 주체 | W | H | q5·과거 저 | q5·과거 중 | q5·과거 고 | (참고) 전체·과거 저 | 전체·과거 고 |", "|---|---|---|---|---|---|---|---|"]
    for W, H, name, df in results:
        if name == "개인":
            continue
        cells = []
        for col in ("top_past_저", "top_past_중", "top_past_고", "all_past_저", "all_past_고"):
            m, t, _ = stat(df[col], H)
            cells.append(f"{m:+.2%} ({t:+.1f})")
        out.append(f"| {name} | {W} | {H} | " + " | ".join(cells) + " |")

    out += ["", "## 3. 기간별 — q5−q1 과 'q5·과거 저' 의 초과수익", "",
            "| 주체 | W | H | 지표 | " + " | ".join(p[0] for p in PERIODS) + " |", "|---|---|---|---|" + "---|" * len(PERIODS)]
    for W, H, name, df in results:
        if name == "개인" or (W, H) not in ((20, 20), (60, 60), (60, 20)):
            continue
        for metric, ser in (("q5−q1", df["q5"] - df["q1"]), ("q5·과거 저", df["top_past_저"])):
            cells = []
            for _, a, b in PERIODS:
                m, t, n = stat(ser[(ser.index >= a) & (ser.index <= b)], H)
                cells.append(f"{m:+.2%} ({t:+.1f}, n={n})")
            out.append(f"| {name} | {W} | {H} | {metric} | " + " | ".join(cells) + " |")

    out += ["", "## 4. 점검 — 신호가 만들어지는 동안의 주가 (같은 W 일 수익률)", "",
            "수급이 가격과 같은 방향으로 움직였는지 확인. q5 가 q1 보다 높으면 '사는 동안 이미 올랐다' 는 뜻.", "",
            "| 주체 | W | q1 과거 수익 | q5 과거 수익 |", "|---|---|---|---|"]
    seen = set()
    for W, H, name, df in results:
        if (W, name) in seen:
            continue
        seen.add((W, name))
        out.append(f"| {name} | {W} | {df['bot_pastret'].mean():+.2%} | {df['top_pastret'].mean():+.2%} |")

    out += ["", "## 5. 절대 수익 감각 (기관+외국인, W=60, H=60)", ""]
    for W, H, name, df in results:
        if (W, H, name) == (60, 60, "기관+외국인"):
            x = df.iloc[::12]
            out.append(f"- q5 평균 {x['top_abs'].mean():+.2%} / 대상 전체 평균 {x['univ_abs'].mean():+.2%} (60거래일, 겹치지 않는 {len(x)}회)")
            out.append(f"- q5 가 전체 평균을 이긴 비율 {(x['top_abs'] > x['univ_abs']).mean():.0%}")
    return "\n".join(out) + "\n"


def main():
    flows = pd.read_parquet(sys.argv[1])
    cache = Path(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else "naver"
    mc = load_marcap(range(2014, pd.Timestamp.today().year + 1), cache)
    print(study(flows, mc, label))


if __name__ == "__main__":
    main()
