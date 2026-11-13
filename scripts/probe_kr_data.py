"""
KR 수급 전략용 데이터를 어디서 받을 수 있는지 점검 (GHA 수동 실행용, 결과는 로그로만 출력).

필요한 것:
  1) 종목별 일별 투자자 유형 순매수 (기관·외국인·개인) — 몇 년치까지?
  2) 과거 시가총액 / 상장주식수 (수급 강도 = 순매수 ÷ 시가총액)
  3) 상장폐지 종목의 과거 가격 (생존 편향 방지)
"""
import io
import sys
import traceback

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}", flush=True)


def attempt(label, fn):
    try:
        out = fn()
        print(f"[OK]   {label}: {out}", flush=True)
        return out
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {str(e)[:300]}", flush=True)
        traceback.print_exc(limit=1, file=sys.stdout)
        return None


def naver_frgn(code, page):
    """네이버 금융 '외국인·기관 순매매 거래량' 표 한 쪽. 열: 날짜, 종가, 전일비, 등락률, 거래량,
    기관 순매매량, 외국인 순매매량, 외국인 보유주수, 외국인 보유율"""
    import re
    from bs4 import BeautifulSoup
    r = requests.get(f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}", headers=UA, timeout=15)
    r.raise_for_status()
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) >= 9 and re.match(r"\d{4}\.\d{2}\.\d{2}$", tds[0]):
            rows.append(tds[:9])
    df = pd.DataFrame(rows, columns=["date", "close", "diff", "pct", "volume", "inst_net", "frgn_net",
                                     "frgn_hold", "frgn_ratio"])
    m = re.findall(r"page=(\d+)", r.text)
    return df, max(int(x) for x in m) if m else None


def probe_naver(code):
    df1, last = naver_frgn(code, 1)
    info = f"열={list(df1.columns)} | 1쪽 {len(df1)}행 {df1.iloc[-1, 0]}~{df1.iloc[0, 0]} | 마지막 쪽 번호={last}"
    if last:
        dfl, _ = naver_frgn(code, last)
        info += f" | 마지막 쪽 {len(dfl)}행, 가장 오래된 날짜 {dfl.iloc[-1, 0] if len(dfl) else '(빈 쪽)'}"
        mid = max(1, last // 2)
        dfm, _ = naver_frgn(code, mid)
        info += f" | {mid}쪽 {dfm.iloc[-1, 0] if len(dfm) else '(빈 쪽)'}"
    print(df1.head(3).to_string())
    return info


def main():
    section("1. pykrx (KRX 로그인 없이)")
    def _pykrx_inv():
        from pykrx import stock
        df = stock.get_market_trading_value_by_date("20240102", "20240131", "005930")
        return f"{df.shape} 열={list(df.columns)}"
    def _pykrx_cap():
        from pykrx import stock
        df = stock.get_market_cap("20240102", "20240131", "005930")
        return f"{df.shape} 열={list(df.columns)}"
    def _pykrx_ohlcv():
        from pykrx import stock
        df = stock.get_market_ohlcv("20240102", "20240131", "005930")
        return f"{df.shape}"
    attempt("투자자별 순매수(종목·일별)", _pykrx_inv)
    attempt("시가총액(종목·일별)", _pykrx_cap)
    attempt("가격", _pykrx_ohlcv)

    section("2. FinanceDataReader")
    import FinanceDataReader as fdr
    def _listing():
        df = fdr.StockListing("KRX")
        return f"{df.shape} 열={list(df.columns)[:14]}"
    delisted = {}
    def _delisting():
        df = fdr.StockListing("KRX-DELISTING")
        delisted["df"] = df
        return f"{df.shape} 열={list(df.columns)} | 예: {df.tail(3).to_dict('records')}"
    def _price():
        df = fdr.DataReader("005930", "2010-01-01")
        return f"{df.shape} {df.index[0].date()}~{df.index[-1].date()} 열={list(df.columns)}"
    attempt("현재 상장 종목 목록", _listing)
    attempt("상장폐지 종목 목록", _delisting)
    attempt("가격 005930 (2010~)", _price)
    sample = []
    df = delisted.get("df")
    if df is not None and len(df):
        col_d = next((c for c in df.columns if "Delist" in c or "폐지" in c), None)
        col_s = next((c for c in df.columns if c in ("Symbol", "Code")), df.columns[0])
        try:
            d = df.copy()
            d[col_d] = pd.to_datetime(d[col_d], errors="coerce")
            d = d[(d[col_d] >= "2018-01-01") & d[col_s].astype(str).str.match(r"^\d{5}0$")]
            print(f"  2018 년 이후 상장폐지(보통주 추정) {len(d)}건")
            sample = d.sort_values(col_d)[col_s].astype(str).tolist()[::max(1, len(d) // 4)][:4]
        except Exception as e:
            print("  상장폐지 표 해석 실패:", e)
    for code in sample:
        attempt(f"상장폐지 종목 {code} 가격 (FDR)", lambda c=code: (lambda x: f"{x.shape} {x.index[0].date() if len(x) else ''}~{x.index[-1].date() if len(x) else ''}")(fdr.DataReader(c, "2010-01-01")))

    section("3. 네이버 금융 — 종목별 외국인·기관 순매매 (frgn.naver)")
    for code in ["005930", "035720", "247540"] + sample[:2]:
        attempt(f"네이버 {code}", lambda c=code: probe_naver(c))

    section("4. marcap 데이터셋 (FinanceData/marcap — 일별 시가총액·상장주식수, 상장폐지 포함?)")
    def _marcap(year):
        url = f"https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{year}.parquet"
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        df = pd.read_parquet(io.BytesIO(r.content))
        dcol = "Date" if "Date" in df.columns else df.columns[0]
        return (f"{df.shape} 열={list(df.columns)} | {df[dcol].min()}~{df[dcol].max()} "
                f"| 종목수 {df['Code'].nunique() if 'Code' in df.columns else '?'}")
    for y in (2015, 2020, 2024, 2025, 2026):
        attempt(f"marcap-{y}", lambda y=y: _marcap(y))
    def _marcap_api():
        r = requests.get("https://api.github.com/repos/FinanceData/marcap/contents/data", timeout=30)
        r.raise_for_status()
        names = sorted(x["name"] for x in r.json())
        return f"{len(names)}개 파일, 처음 {names[:2]} … 마지막 {names[-3:]}"
    attempt("marcap 파일 목록", _marcap_api)

    section("5. KRX 정보데이터시스템 직접 (로그인 없이)")
    def _krx():
        r = requests.post("http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                          data={"bld": "dbms/MDC/STAT/standard/MDCSTAT02303", "locale": "ko_KR",
                                "inqTpCd": "2", "trdVolVal": "2", "askBid": "3",
                                "strtDd": "20240102", "endDd": "20240131", "isuCd": "KR7005930003",
                                "detailView": "1", "money": "1", "csvxls_isNo": "false"},
                          headers={**UA, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020303"},
                          timeout=20)
        return f"HTTP {r.status_code} | {r.text[:200]!r}"
    attempt("종목별 투자자 거래실적(일별)", _krx)


if __name__ == "__main__":
    main()
