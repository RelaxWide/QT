"""KR 수급 사건 연구 공통: marcap(일별 시가총액·상장주식수, 상장폐지 포함) 로딩과 대상 종목 선정."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

MARCAP_URL = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{year}.parquet"
START_YEAR = 2014            # 신호 계산에 2014 년 말 수급이 필요하다
TOP_N = 200
BAD_DEPT = ("관리종목", "투자주의환기", "SPAC", "외국기업")


def load_marcap(years, cache_dir: Path | None = None) -> pd.DataFrame:
    frames = []
    for y in years:
        f = cache_dir / f"marcap-{y}.parquet" if cache_dir else None
        if f is not None and f.exists():
            df = pd.read_parquet(f)
        else:
            r = requests.get(MARCAP_URL.format(year=y), timeout=120)
            r.raise_for_status()
            df = pd.read_parquet(io.BytesIO(r.content))
            if f is not None:
                cache_dir.mkdir(parents=True, exist_ok=True)
                df.to_parquet(f)
        frames.append(df[["Code", "Name", "Date", "Close", "ChangesRatio", "Volume", "Amount", "Marcap",
                          "Stocks", "Market", "Dept"]])
    out = pd.concat(frames, ignore_index=True)
    out["Code"] = out["Code"].astype(str)
    out["Date"] = pd.to_datetime(out["Date"])
    return out


def eligible(df: pd.DataFrame) -> pd.Series:
    """보통주 · KOSPI/KOSDAQ · 관리종목/환기종목/SPAC/외국기업 제외 (그 날짜 기준 소속부로 판정)."""
    code = df["Code"]
    name = df["Name"].fillna("")
    dept = df["Dept"].fillna("")
    ok = code.str.match(r"^\d{5}0$") & df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])
    ok &= ~dept.str.contains("|".join(BAD_DEPT))
    ok &= ~name.str.contains("스팩|리츠|인프라$|맥쿼리")
    return ok


def top_n_by_date(df: pd.DataFrame, dates, n: int = TOP_N) -> pd.DataFrame:
    """각 날짜의 시가총액 상위 n (그 시점 기준 — 나중에 커진 종목을 미리 넣지 않는다)."""
    d = df[df["Date"].isin(dates) & eligible(df)]
    d = d.sort_values(["Date", "Marcap"], ascending=[True, False])
    return d.groupby("Date").head(n)
