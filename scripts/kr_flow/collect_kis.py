"""
KR 수급 사건 연구 — KIS 쪽 수급 수집 (PC 에서 실행, 조회 전용: 주문·장부 변경 없음).

네이버 수급으로 한 사건 연구를 KIS 수급으로 다시 돌려 결론이 출처에 따라 달라지는지 보기 위한 것.
  대상  : research/kr_flow/universe.csv (시총 상위 200 합집합, 512종목)
  API   : 종목별 투자자매매동향(일별) FHPTJ04160001 — 기준일 포함 30 거래일씩, 2014-10 까지 거슬러 올라감
  저장  : data/kr_flow/kis/<종목코드>.parquet  (종목별 — 중단 후 다시 실행하면 이어서 받는다)
실계좌 운용을 방해하지 않도록: 장마감 처리·수요일 매수 시간대와 계좌 잠금이 잡혀 있는 동안은 쉰다.

실행: scripts\\collect_kis_flows.bat   (또는 python scripts/kr_flow/collect_kis.py [종목수 제한])
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:                                   # py<3.9
    from backports.zoneinfo import ZoneInfo

from live_trading.kis_client import KISClient
from live_trading.runlock import LockBusy, account_lock

OUT = ROOT / "data" / "kr_flow" / "kis"
PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
TR_ID = "FHPTJ04160001"
STOP_BEFORE = "20141001"
SLEEP = 0.25
KST = ZoneInfo("Asia/Seoul")


def blackout(now: datetime) -> str:
    """실계좌 예약 작업 시간대면 사유, 아니면 빈 문자열."""
    hm, wd = now.hour * 60 + now.minute, now.weekday()        # 월=0
    if wd < 5 and 5 * 60 + 20 <= hm <= 7 * 60 + 45:
        return "장마감 처리·요약 시간대 (05:20~07:45)"
    if (wd == 2 and hm >= 23 * 60 + 45) or (wd == 3 and hm <= 1 * 60 + 40):
        return "수요일 매수 시간대 (수 23:45~목 01:40)"
    return ""


def wait_until_clear() -> None:
    while True:
        why = blackout(datetime.now(KST))
        if not why:
            try:
                with account_lock("us", wait_sec=0):     # 따라잡기 등 다른 실행이 돌고 있으면 쉰다
                    return
            except LockBusy:
                why = "실계좌 작업 실행 중 (계좌 잠금)"
        print(f"  … 대기: {why}", flush=True)
        time.sleep(120)


def fetch_code(kis: KISClient, code: str, end: str) -> list[dict]:
    rows, date, prev_oldest = [], end, None
    for _ in range(140):
        d = None
        for attempt in range(5):
            try:
                r = requests.get(kis.base_url + PATH, headers=kis._headers(TR_ID), timeout=15,
                                 params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                                         "FID_INPUT_DATE_1": date, "FID_ORG_ADJ_PRC": "", "FID_ETC_CLS_CODE": ""})
                d = r.json()
                if d.get("rt_cd") == "0":
                    break
                msg = str(d.get("msg1", ""))
                time.sleep(1.5 if "초당" in msg else 3.0)
            except (requests.RequestException, ValueError):
                time.sleep(3.0)
            d = None
        if d is None:
            raise RuntimeError(f"{code} 기준일 {date} 조회 실패")
        page = d.get("output2") or []
        if not page:
            break
        rows.extend(page)
        oldest = min(x["stck_bsop_date"] for x in page)
        if oldest <= STOP_BEFORE or oldest == prev_oldest:
            break
        prev_oldest = oldest
        date = (pd.Timestamp(oldest) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        time.sleep(SLEEP)
    return rows


def main():
    uni = pd.read_csv(ROOT / "research" / "kr_flow" / "universe.csv", dtype={"code": str})
    codes = uni["code"].str.zfill(6).tolist()
    if len(sys.argv) > 1:
        codes = codes[: int(sys.argv[1])]
    OUT.mkdir(parents=True, exist_ok=True)
    kis = KISClient.from_config(allow_prod=True)
    end = datetime.now(KST).strftime("%Y%m%d")
    todo = [c for c in codes if not (OUT / f"{c}.parquet").exists()]
    print(f"대상 {len(codes)}종목 중 남은 {len(todo)}종목 — 저장 위치 {OUT}", flush=True)
    t0, failed = time.time(), []
    for i, code in enumerate(todo, 1):
        wait_until_clear()
        try:
            rows = fetch_code(kis, code, end)
        except Exception as e:
            failed.append(code)
            print(f"  [실패] {code}: {e}", flush=True)
            continue
        df = pd.DataFrame(rows)
        if len(df):
            keep = ["stck_bsop_date", "stck_clpr", "acml_vol"] + [c for c in df.columns if c.endswith("_ntby_qty")
                                                                   or c.endswith("_ntby_vol")]
            df = df[keep].drop_duplicates("stck_bsop_date").sort_values("stck_bsop_date")
            for c in df.columns[1:]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df.insert(0, "code", code)
        tmp = OUT / f"{code}.parquet.tmp"
        df.to_parquet(tmp, index=False)
        tmp.replace(OUT / f"{code}.parquet")
        if i % 10 == 0 or i == len(todo):
            el = time.time() - t0
            print(f"  {i}/{len(todo)} 종목 ({el / 60:.0f}분 경과, 남은 예상 {el / i * (len(todo) - i) / 60:.0f}분)", flush=True)
    done = len(list(OUT.glob("*.parquet")))
    print(f"끝. 저장된 종목 {done}/{len(codes)} | 실패 {len(failed)}: {failed[:20]}", flush=True)
    if done >= len(codes) - len(failed):
        merged = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob("*.parquet"))], ignore_index=True)
        merged.to_parquet(OUT.parent / "kis_flows.parquet", index=False)
        print(f"합본: {OUT.parent / 'kis_flows.parquet'} ({len(merged):,}행)", flush=True)


if __name__ == "__main__":
    main()
