"""base_study.py 가 만든 사건 표로 결과 표를 낸다.  사용: python scripts/kr_base/base_report.py <캐시 폴더>"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ev = pd.read_parquet(Path(sys.argv[1]) / "base_events.parquet")
H = 250
out = []
P = out.append


def nw(x, L=12):
    x = np.asarray(x, float); x = x[~np.isnan(x)]; n = len(x)
    if n < 24:
        return np.nan
    e = x - x.mean(); v = e @ e / n
    for k in range(1, L + 1):
        v += 2 * (1 - k / (L + 1)) * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(max(v, 1e-18) / n)


base_m = ev.groupby("t")[[f"ret{H}", "ret120"]].mean()


def line(name, m, h=H):
    g = ev[m]
    if len(g) < 200:
        return f"| {name} | {len(g):,} | 표본 부족 |" + " |" * 10
    per = g.groupby("t")[f"ret{h}"].mean()
    ex = (per - base_m[f"ret{h}"].reindex(per.index))
    n_per = g.groupby("t").size()
    return (f"| {name} | {len(g):,} | {n_per.mean():.0f} | {g[f'ret{h}'].mean():+.1%} | {g[f'ret{h}'].median():+.1%} | "
            f"{ex.mean():+.1%} ({nw(ex):+.1f}) | {(g[f'max{h}'] >= 0.5).mean():.0%} | {(g[f'max{h}'] >= 1.0).mean():.0%} | "
            f"{(g[f'ret{h}'] <= -0.3).mean():.0%} | {(g[f'min{h}'] <= -0.3).mean():.0%} | {g[f'del{h}'].mean():.1%} | "
            f"{g[f'tp{h}'].mean():+.1%} |")


HEAD = ("| 집단 | 사건 수 | 월평균 종목 | 평균 수익 | 중앙값 | 전체 대비 초과 (t) | +50% 도달 | +100% 도달 | 끝에 -30% 이하 | "
        "도중 -30% 이하 | 상장폐지 | +50% 익절 규칙 평균 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
ALL = pd.Series(True, index=ev.index)
r, dd, vr, pos = ev["range"], ev["dd5"], ev["vr"], ev["pos"]

P("# KR 장기 횡보(바닥) 종목 사건 연구\n")
P(f"- 사건 {len(ev):,}건 = 매월 말 기준, 거래 가능한 KOSPI·KOSDAQ 보통주 전부 (우선주·스팩·리츠 제외). "
  f"{ev['date'].min().date()} ~ {ev['date'].max().date()}, 기준일 {ev['t'].nunique()}개, 종목 {ev['code'].nunique()}개")
P("- 진입: 기준일 다음 거래일 종가. 보유 250거래일(약 1년). 상장폐지 종목은 마지막 가격으로 계산. 비용 미반영.")
P("- '전체 대비 초과' = 같은 달 전체 종목 평균과의 차이를 월별로 낸 뒤 평균, t 는 Newey-West (12개월 겹침 보정).")
P("- '+50% 익절 규칙' = 종가가 +50% 이상인 첫날 매도, 못 닿으면 250거래일 뒤 매도.\n")
q = r.quantile([.05, .1, .25, .5, .75])
P(f"- 참고: 2년 가격 범위(최고/최저-1)의 분포 — 하위 5% {q[.05]:.0%}, 10% {q[.1]:.0%}, 25% {q[.25]:.0%}, 중앙값 {q[.5]:.0%}, 75% {q[.75]:.0%}\n")

P("## 1. 2년 횡보 폭별 (250거래일)\n"); P(HEAD)
P(line("전체 종목 (기준)", ALL))
for lab, lo, hi in (("2년 범위 40% 이하", 0, .4), ("40~60%", .4, .6), ("60~100%", .6, 1.0), ("100~200%", 1.0, 2.0), ("200% 초과", 2.0, 99)):
    P(line(lab, (r > lo) & (r <= hi)))

P("\n## 2. 횡보 + 바닥권 + 거래량 (250거래일)\n"); P(HEAD)
B60, B100 = r <= .6, r <= 1.0
low = dd <= .5
P(line("2년 범위 60% 이하", B60))
P(line("  + 5년 고점 대비 -50% 이하 (바닥권)", B60 & low))
P(line("  + 바닥권 + 범위 아래 절반", B60 & low & (pos <= .5)))
P(line("  + 바닥권 + 거래량 1.5배↑", B60 & low & (vr >= 1.5)))
P(line("  + 거래량 1.5배↑ (바닥권 무관)", B60 & (vr >= 1.5)))
P(line("  + 거래량 3배↑", B60 & (vr >= 3)))
P(line("2년 범위 100% 이하 + 바닥권", B100 & low))
P(line("  + 거래량 1.5배↑", B100 & low & (vr >= 1.5)))
P(line("  + 5년 고점 대비 -70% 이하", B100 & (dd <= .3)))
P(line("(대조) 바닥권만, 횡보 무관", low))

P("\n## 3. 같은 집단의 120거래일 결과\n"); P(HEAD.replace("250", "120"))
for lab, m in (("전체 종목 (기준)", ALL), ("2년 범위 60% 이하", B60), ("60% 이하 + 바닥권", B60 & low),
               ("60% 이하 + 바닥권 + 거래량 1.5배↑", B60 & low & (vr >= 1.5)), ("100% 이하 + 바닥권", B100 & low)):
    P(line(lab, m, 120))

P("\n## 4. 규모·상태별 — '2년 범위 60% 이하 + 바닥권' 을 나눠 보기 (250거래일)\n"); P(HEAD)
G = B60 & low
capq = ev.groupby("t")["cap"].rank(pct=True)
for lab, m in (("시총 하위 1/3", capq <= 1 / 3), ("시총 중간 1/3", (capq > 1 / 3) & (capq <= 2 / 3)), ("시총 상위 1/3", capq > 2 / 3),
               ("60일 평균 거래대금 1억 미만", ev["amt60"] < 1e8), ("1억~10억", (ev["amt60"] >= 1e8) & (ev["amt60"] < 1e9)),
               ("10억 이상", ev["amt60"] >= 1e9),
               ("관리·환기종목 (2014~ 만 표시됨)", ev["mgmt"]), ("관리·환기 아님", ~ev["mgmt"])):
    P(line(lab, G & m))
P("\n(같은 구분으로 전체 종목)\n"); P(HEAD)
for lab, m in (("시총 하위 1/3", capq <= 1 / 3), ("시총 중간 1/3", (capq > 1 / 3) & (capq <= 2 / 3)), ("시총 상위 1/3", capq > 2 / 3)):
    P(line(lab, m))

P("\n## 5. 기간별 — 전체 대비 초과수익 (250거래일, 괄호 t)\n")
per = [("2007~2010", 2007, 2010), ("2011~2014", 2011, 2014), ("2015~2018", 2015, 2018), ("2019~2021", 2019, 2021), ("2022~2025", 2022, 2025)]
P("| 집단 | " + " | ".join(p[0] for p in per) + " |"); P("|---|" + "---|" * len(per))
for lab, m in (("2년 범위 60% 이하", B60), ("60% 이하 + 바닥권", B60 & low), ("60% 이하 + 바닥권 + 거래량 1.5배↑", B60 & low & (vr >= 1.5)),
               ("100% 이하 + 바닥권", B100 & low)):
    cells = []
    for _, a, b in per:
        g = ev[m & (ev["year"] >= a) & (ev["year"] <= b)]
        pm = g.groupby("t")[f"ret{H}"].mean()
        ex = pm - base_m[f"ret{H}"].reindex(pm.index)
        cells.append(f"{ex.mean():+.1%} ({nw(ex, 6):+.1f}, 월 {g.groupby('t').size().mean():.0f}종목)" if len(pm) >= 12 else "-")
    P(f"| {lab} | " + " | ".join(cells) + " |")

P("\n## 6. 급등은 언제 오는가 — '60% 이하 + 바닥권' 중 +50% 에 닿은 사건의 도달 시점\n")
g = ev[G & (ev["tpday250"] >= 0)]
d = g["tpday250"]
P(f"- +50% 도달 사건 {len(g):,}건 / 전체 {int(G.sum()):,}건. 도달까지 거래일: 25% {d.quantile(.25):.0f}일, 중앙값 {d.median():.0f}일, 75% {d.quantile(.75):.0f}일")
P(f"- 도달 못 한 사건의 250거래일 수익: 평균 {ev[G & (ev['tpday250'] < 0)]['ret250'].mean():+.1%}, 중앙값 {ev[G & (ev['tpday250'] < 0)]['ret250'].median():+.1%}")
print("\n".join(out))
