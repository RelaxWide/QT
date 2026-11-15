"""
KR 장기 횡보(바닥) 종목 사건 연구 — "2년 이상 바닥권에서 유지되던 종목이 이후 솟는가".

종목을 미리 거르지 않는다: KOSPI·KOSDAQ 보통주 전부 (우선주·스팩·리츠만 제외 — 회사 주식이 아니거나 구조상 가격이 고정).
관리종목·시가총액·거래대금은 걸러내지 않고 **결과를 나눠 보는 기준**으로만 쓴다.

  기준일   : 매월 마지막 거래일 (2007-01 ~ 자료 끝 - 250거래일)
  횡보     : 최근 500거래일(약 2년) 수정주가의 (최고/최저 - 1) 이 기준 이하
  바닥권   : 현재가가 최근 1250거래일(약 5년) 최고가의 50% 이하
  거래량↑  : 최근 60일 평균 회전율이 그 앞 440일 평균의 1.5배 이상
  결과     : 기준일 다음 거래일 종가 진입 → 120·250거래일. 수익률, 기간 중 최고 도달, 큰 손실, 상장폐지
수정주가는 marcap 등락률을 누적해 만든다 (감자·분할 때 등락률이 보정 안 된 날은 시가총액 변화로 대체).

사용: python scripts/kr_base/base_study.py <marcap 캐시 폴더> [시작연도=2003]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_D, LONG_D = 500, 1250
HORIZONS = (120, 250)


def load(cache: Path, y0: int) -> pd.DataFrame:
    cols = ["Code", "Name", "Date", "Close", "ChangesRatio", "Volume", "Amount", "Marcap", "Stocks", "Market", "Dept"]
    fr = []
    for y in range(y0, pd.Timestamp.today().year + 1):
        f = cache / f"marcap-{y}.parquet"
        if f.exists():
            fr.append(pd.read_parquet(f, columns=cols))
    d = pd.concat(fr, ignore_index=True)
    d["Code"] = d["Code"].astype(str)
    d["Date"] = pd.to_datetime(d["Date"])
    name = d["Name"].fillna("")
    keep = d["Code"].str.match(r"^\d{5}0$") & d["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])
    keep &= ~name.str.contains("스팩|기업인수목적|리츠|맥쿼리인프라")
    return d[keep].sort_values(["Code", "Date"]).reset_index(drop=True)


def adj_returns(d: pd.DataFrame) -> pd.Series:
    """수정 일간 수익률. 기본은 거래소 등락률(권리락·분할이 반영된 기준가 대비).
    예외 처리:
      · 감자·병합(주식수 감소)인데 등락률이 원가격 변화 그대로인 날 → 시가총액 변화로 대체
        (주식수가 늘어난 날은 건드리지 않는다 — 증자·출자전환은 시가총액이 새 돈만큼 늘어 수익률이 아니다)
      · 전일 종가가 1원 이하인 자료 오류 → 0
      · 하루 +100% 초과는 +100% 로 자른다 (거래재개일 상한이 기준가의 200%. 그 이상은 정리매매·자료 문제)
    """
    g = d.groupby("Code", sort=False)
    pc, ps = g["Close"].shift(1), g["Stocks"].shift(1)
    ret = d["ChangesRatio"] / 100.0
    raw = d["Close"] / pc - 1
    sch = d["Stocks"] / ps - 1
    mc = d["Close"] * d["Stocks"] / (pc * ps) - 1
    fix = (sch < -0.05) & (ret > 0.31) & ((ret - raw).abs() < 0.01) & mc.notna()
    ret = ret.where(~fix, mc)
    ret = ret.where(pc.notna() & (pc > 1), 0.0)                      # 상장 첫날 · 자료 오류
    return ret.clip(lower=-0.999, upper=1.0)


def main():
    cache = Path(sys.argv[1])
    y0 = int(sys.argv[2]) if len(sys.argv) > 2 else 2003
    d = load(cache, y0)
    d["ret"] = adj_returns(d)
    d["mgmt"] = d["Dept"].fillna("").str.contains("관리종목|투자주의환기")
    d["turn"] = d["Volume"] / d["Stocks"].replace(0, np.nan)
    dates = pd.DatetimeIndex(sorted(d["Date"].unique()))
    codes = pd.Index(sorted(d["Code"].unique()))
    di = pd.Series(np.arange(len(dates)), index=dates)
    ci = pd.Series(np.arange(len(codes)), index=codes)
    r, c = di[d["Date"]].to_numpy(), ci[d["Code"]].to_numpy()

    def wide(col, fill=np.nan, dtype=np.float32):
        m = np.full((len(dates), len(codes)), fill, dtype=dtype)
        m[r, c] = d[col].to_numpy()
        return m

    ret = wide("ret")
    listed = ~np.isnan(ret)
    P = np.where(listed, np.log1p(np.nan_to_num(ret, nan=0.0)), 0.0).cumsum(axis=0).astype(np.float32)   # 폐지 뒤엔 마지막 값 유지
    Pm = np.where(listed, P, np.nan)
    vol, turn, cap, amt = wide("Volume"), wide("turn"), wide("Marcap"), wide("Amount")
    mgmt = wide("mgmt", fill=0, dtype=np.float32) > 0
    first = np.where(listed.any(0), listed.argmax(0), len(dates))
    last = np.where(listed.any(0), len(dates) - 1 - listed[::-1].argmax(0), -1)

    Pd = pd.DataFrame(Pm)
    hi500, lo500 = Pd.rolling(BASE_D, min_periods=BASE_D - 20).max().to_numpy(), Pd.rolling(BASE_D, min_periods=BASE_D - 20).min().to_numpy()
    hi5y = Pd.rolling(LONG_D, min_periods=BASE_D).max().to_numpy()
    T = pd.DataFrame(turn)
    t60 = T.rolling(60, min_periods=40).mean().to_numpy()
    t500 = T.rolling(BASE_D, min_periods=BASE_D - 20).mean().to_numpy()
    tprev = (t500 * BASE_D - t60 * 60) / (BASE_D - 60)
    a60 = pd.DataFrame(amt).rolling(60, min_periods=40).mean().to_numpy()

    month_end = pd.Series(dates).groupby([dates.year, dates.month]).max().to_numpy()
    Hmax = max(HORIZONS)
    forms = [di[pd.Timestamp(x)] for x in month_end if pd.Timestamp(x) >= pd.Timestamp("2007-01-01")
             and di[pd.Timestamp(x)] + 1 + Hmax < len(dates)]

    rows = []
    for t in forms:
        e = t + 1                                             # 진입: 다음 거래일 종가
        ok = listed[t] & listed[e] & (vol[t] > 0) & (vol[e] > 0) & ~np.isnan(hi500[t])
        idx = np.where(ok)[0]
        if len(idx) == 0:
            continue
        rng = np.expm1(hi500[t, idx] - lo500[t, idx])
        pos = (Pm[t, idx] - lo500[t, idx]) / np.maximum(hi500[t, idx] - lo500[t, idx], 1e-6)
        dd5 = np.exp(Pm[t, idx] - hi5y[t, idx])
        vr = t60[t, idx] / np.where(tprev[t, idx] > 0, tprev[t, idx], np.nan)
        rec = {"t": t, "code": idx, "range": rng, "pos": pos, "dd5": dd5, "vr": vr, "cap": cap[t, idx],
               "amt60": a60[t, idx], "mgmt": mgmt[t, idx]}
        for H in HORIZONS:
            win = P[e + 1: e + 1 + H, idx] - P[e, idx]        # 폐지 뒤엔 마지막 가격 유지
            rec[f"ret{H}"] = np.expm1(win[-1])
            rec[f"max{H}"] = np.expm1(win.max(0))
            rec[f"min{H}"] = np.expm1(win.min(0))
            rec[f"del{H}"] = last[idx] < e + H
            hit = win >= np.log(1.5)
            fp = np.where(hit.any(0), hit.argmax(0), -1)      # +50% 첫 도달일
            tp = np.where(fp >= 0, np.expm1(win[np.maximum(fp, 0), np.arange(len(idx))]), np.expm1(win[-1]))
            rec[f"tp{H}"] = tp
            rec[f"tpday{H}"] = fp
        rows.append(pd.DataFrame(rec))
    ev = pd.concat(rows, ignore_index=True)
    ev["date"] = dates[ev["t"].to_numpy()]
    ev["year"] = ev["date"].dt.year
    ev.to_parquet(cache / "base_events.parquet", index=False)
    print(f"사건 표 저장: {len(ev):,}행 (기준일 {ev['t'].nunique()}개, {ev['date'].min().date()}~{ev['date'].max().date()}, "
          f"종목 {ev['code'].nunique()}개)")


if __name__ == "__main__":
    main()
