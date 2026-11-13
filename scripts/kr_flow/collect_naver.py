"""
KR 수급 사건 연구용 데이터 수집 (GHA 에서 실행 — 네이버 모바일 증권 API).

1) marcap 으로 2015 년 이후 매월 첫 거래일의 시가총액 상위 200 (보통주, 관리종목 등 제외) 을 구해 합집합을 만든다.
2) 그 종목들의 일별 외국인·기관·개인 순매수 수량을 2014-10 부터 받는다 (한 번에 60 일치, 과거로 이동).
출력: out/universe.csv, out/naver_flows.parquet, out/collect_log.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import START_YEAR, TOP_N, load_marcap, top_n_by_date  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
URL = "https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60&bizdate={bizdate}"
STOP_BEFORE = "20141001"
SLEEP = 0.12


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").replace("+", "").replace("%", ""))
    except ValueError:
        return float("nan")


def fetch_code(code: str, session: requests.Session, end: str) -> tuple[list[dict], str]:
    rows, bizdate, note = [], end, ""
    for _ in range(80):
        for attempt in range(4):
            try:
                r = session.get(URL.format(code=code, bizdate=bizdate), headers=UA, timeout=15)
                if r.status_code == 200:
                    break
                time.sleep(2 * (attempt + 1))
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
        else:
            return rows, f"요청 실패 (bizdate={bizdate})"
        try:
            page = r.json()
        except ValueError:
            return rows, f"JSON 아님 (bizdate={bizdate})"
        if not page:
            break
        for x in page:
            rows.append({"code": code, "date": x["bizdate"],
                         "frgn": _num(x.get("foreignerPureBuyQuant")), "inst": _num(x.get("organPureBuyQuant")),
                         "indv": _num(x.get("individualPureBuyQuant")), "frgn_ratio": _num(x.get("foreignerHoldRatio")),
                         "close": _num(x.get("closePrice")), "volume": _num(x.get("accumulatedTradingVolume"))})
        oldest = page[-1]["bizdate"]
        if oldest <= STOP_BEFORE or oldest >= bizdate:
            break
        bizdate = oldest            # bizdate 는 배타적 — 그 날짜보다 앞의 60 일이 온다
        time.sleep(SLEEP)
    return rows, note


def main():
    out = Path("out")
    out.mkdir(exist_ok=True)
    this_year = pd.Timestamp.today().year
    mc = load_marcap(range(START_YEAR + 1, this_year + 1))
    first_days = mc.groupby([mc["Date"].dt.year, mc["Date"].dt.month])["Date"].min().tolist()
    top = top_n_by_date(mc, first_days, TOP_N)
    uni = (top.groupby("Code").agg(name=("Name", "last"), first_in=("Date", "min"), last_in=("Date", "max"),
                                    months_in=("Date", "nunique")).reset_index().rename(columns={"Code": "code"}))
    last_seen = mc.groupby("Code")["Date"].max()
    uni["last_listed"] = uni["code"].map(last_seen)
    uni.to_csv(out / "universe.csv", index=False)
    print(f"대상 종목 {len(uni)}개 (매월 첫 거래일 시총 상위 {TOP_N} 의 합집합, {first_days[0].date()}~{first_days[-1].date()})")

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    codes = uni["code"].tolist()[:limit]
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y%m%d")
    s = requests.Session()
    all_rows, log = [], {"empty": [], "errors": {}, "rows": {}}
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        rows, note = fetch_code(code, s, end)
        log["rows"][code] = len(rows)
        if note:
            log["errors"][code] = note
        if not rows:
            log["empty"].append(code)
        all_rows.extend(rows)
        if i % 25 == 0 or i == len(codes):
            print(f"  {i}/{len(codes)} 종목, 누적 {len(all_rows):,}행, {time.time() - t0:.0f}초", flush=True)
    df = pd.DataFrame(all_rows).drop_duplicates(["code", "date"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["code", "date"])
    df.to_parquet(out / "naver_flows.parquet", index=False)
    (out / "collect_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {len(df):,}행, 종목 {df['code'].nunique()}개, {df['date'].min().date()}~{df['date'].max().date()} "
          f"| 빈 종목 {len(log['empty'])}개 | 오류 {len(log['errors'])}개")


if __name__ == "__main__":
    main()
