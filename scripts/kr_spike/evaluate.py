"""spike_oos.parquet 평가 — 모형이 고른 종목의 표본 외 성과.  사용: python scripts/kr_spike/evaluate.py <캐시 폴더>"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ev = pd.read_parquet(Path(sys.argv[1]) / "spike_oos.parquet")
ev["amt60"] = np.exp(ev["lamt60"])
ev["yr"] = ev["date"].dt.year
N = 30


def nw(x, L=12):
    x = np.asarray(x, float); x = x[~np.isnan(x)]; n = len(x)
    if n < 30:
        return np.nan
    e = x - x.mean(); v = e @ e / n
    for k in range(1, L + 1):
        v += 2 * (1 - k / (L + 1)) * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(max(v, 1e-18) / n)


def pick(df, score, n=N, asc=False):
    return df.sort_values(["t", score], ascending=[True, asc]).groupby("t").head(n)


def row(label, sel, univ, cost):
    g = sel.groupby("t")
    per = g[["r_tp50_sl20", "ret60", "r_tp30_sl15"]].mean()
    ub = univ.groupby("t")[["r_tp50_sl20", "ret60"]].mean().reindex(per.index)
    ex = per["r_tp50_sl20"] - ub["r_tp50_sl20"]
    net = per["r_tp50_sl20"] - cost
    return (f"| {label} | {(sel['max60'] >= .5).mean():.0%} | {(sel['min60'] <= -.3).mean():.0%} | {sel['del60'].mean():.1%} | "
            f"{per['ret60'].mean():+.1%} | {sel['ret60'].median():+.1%} | {per['r_tp50_sl20'].mean():+.1%} | {net.mean():+.1%} ({nw(net):+.1f}) | "
            f"{ex.mean():+.1%} ({nw(ex):+.1f}) | {(net > 0).mean():.0%} | {np.exp(sel['amt60']).median() if False else sel['amt60'].median() / 1e8:.1f}억 |")


HEAD = ("| 고르는 방법 (매주 30종목) | +50% 도달 | 도중 -30% | 상장폐지 | 60일 보유 평균 | 중앙값 | 규칙 수익 | 비용 뺀 규칙 수익 (t) | "
        "전체 대비 초과 (t) | 이긴 주 비율 | 거래대금 중앙값 |\n|---|---|---|---|---|---|---|---|---|---|---|")
out = ["# KR 급등 패턴 — 표본 외 평가 (2014-01 ~ 2026-08, 매주 기준)", "",
       f"- 평가 사건 {len(ev):,}건, 기준일 {ev['t'].nunique()}개. 모형은 각 평가 구간 **이전** 자료로만 학습 (60거래일 간격).",
       "- 규칙: 다음 거래일 종가 매수 → 종가 +50% 익절 / -20% 손절 / 60거래일 뒤 청산. 수익은 60거래일(약 3개월) 기준.",
       "- 비용: 왕복 1.0% (거래세·수수료·호가). 거래대금 10억 이상 집단은 0.6%.",
       f"- 전체 종목 기준선: +50% 도달 {(ev['max60'] >= .5).mean():.1%}, 도중 -30% {(ev['min60'] <= -.3).mean():.1%}, "
       f"60일 평균 {ev.groupby('t')['ret60'].mean().mean():+.1%}, 규칙 수익 {ev.groupby('t')['r_tp50_sl20'].mean().mean():+.1%}", ""]
for title, univ, cost in (("전 종목", ev, .01), ("60일 평균 거래대금 3억 이상", ev[ev["amt60"] >= 3e8], .01),
                          ("60일 평균 거래대금 10억 이상", ev[ev["amt60"] >= 1e9], .006)):
    out += [f"## {title} (주당 평균 {univ.groupby('t').size().mean():.0f}종목)", "", HEAD]
    out.append(row("급등 확률 상위 (급등 모형)", pick(univ, "p_spike"), univ, cost))
    out.append(row("기대 초과수익 상위 (수익 모형)", pick(univ, "p_ret"), univ, cost))
    out.append(row("기대 초과수익 하위 (피해야 할 종목)", pick(univ, "p_ret", asc=True), univ, cost))
    mid = univ[univ.groupby("t")["p_spike"].rank(pct=True) >= .8]
    out.append(row("급등 확률 상위 20% 중 수익 모형 상위", pick(mid, "p_ret"), univ, cost))
    out.append(row("(대조) 변동성 가장 낮은 30", pick(univ, "vol250", asc=True), univ, cost))
    out.append(row("(대조) 시총 가장 작은 30", pick(univ, "lcap", asc=True), univ, cost))
    out.append("")

u = ev[ev["amt60"] >= 3e8]
sel = pick(u, "p_ret")
out += ["## 기간별 — 거래대금 3억 이상, 수익 모형 상위 30 (비용 1.0% 뺀 규칙 수익 / 전체 대비 초과)", "",
        "| 구간 | 비용 뺀 규칙 수익 | 전체 대비 초과 (t) | +50% 도달 | 도중 -30% |", "|---|---|---|---|---|"]
for a, b in ((2014, 2015), (2016, 2017), (2018, 2019), (2020, 2021), (2022, 2023), (2024, 2026)):
    s, uu = sel[(sel.yr >= a) & (sel.yr <= b)], u[(u.yr >= a) & (u.yr <= b)]
    per = s.groupby("t")["r_tp50_sl20"].mean()
    ex = per - uu.groupby("t")["r_tp50_sl20"].mean().reindex(per.index)
    out.append(f"| {a}~{b} | {(per - .01).mean():+.1%} | {ex.mean():+.1%} ({nw(ex, 12):+.1f}) | {(s['max60'] >= .5).mean():.0%} | {(s['min60'] <= -.3).mean():.0%} |")

out += ["", "## 모형이 고른 종목의 특징 — 거래대금 3억 이상, 전체 중앙값과 비교", "",
        "| 특징 | 전체 중앙값 | 수익 모형 상위 30 | 급등 모형 상위 30 | 수익 모형 하위 30 |", "|---|---|---|---|---|"]
top, sp, bot = pick(u, "p_ret"), pick(u, "p_spike"), pick(u, "p_ret", asc=True)
lab = {"r20": "최근 20일 수익", "r60": "최근 60일 수익", "r250": "최근 250일 수익", "d_hi250": "52주 고점 대비", "dd5y": "5년 고점 대비",
       "range500": "2년 가격 범위(로그)", "vol250": "1년 일간 변동성", "vc20": "최근 20일 변동성 ÷ 1년", "tv20": "최근 20일 회전율 ÷ 1년",
       "max1d20": "최근 20일 최대 일간 상승", "nbig250": "1년간 +15% 이상 오른 날 수", "upvol60": "60일 상승일 거래량 비중",
       "clv20": "종가의 일중 위치(1=고가)", "uw20": "윗꼬리 길이", "lcap": "시가총액", "lprice": "주가"}
for k, nm in lab.items():
    f = (lambda s: f"{np.exp(s.median()) / 1e8:,.0f}억" ) if k == "lcap" else (lambda s: f"{np.exp(s.median()):,.0f}원") if k == "lprice" \
        else (lambda s: f"{np.expm1(s.median()):+.0%}") if k in ("r20", "r60", "r250", "d_hi250", "dd5y") else (lambda s: f"{s.median():.3f}")
    out.append(f"| {nm} | {f(u[k])} | {f(top[k])} | {f(sp[k])} | {f(bot[k])} |")
print("\n".join(out))
