"""
한국 DART OpenAPI 기반 재무제표 데이터 fetch.

opendartreader 패키지 사용 — 사업/분기 보고서 XBRL 파싱.
시점별 재무제표 (매출, 영업이익, 순이익, 총자산, 자기자본 등) 추출.

지표 산출 (PyKRX 만으로는 불가능했던 것):
  - GP/A (매출총이익 / 총자산) — 강환국 슈퍼퀄리티 핵심
  - 자산 성장률 (YoY)
  - 영업이익률, ROA, 부채비율
  - F-Score 9개 항목 (수익성·자본구조·운영효율)

사용자 액션 필요:
  1. https://opendart.fss.or.kr/ 회원 가입
  2. 마이페이지 → 인증키 신청 (즉시 발급, 40자)
  3. 환경변수 DART_API_KEY 또는 config.yaml 의 dart_api_key 설정

캐시: data/raw/kr/dart/{ticker}/{year}_{report}.parquet
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd

CACHE_DART = Path("data/raw/kr/dart")


@contextlib.contextmanager
def _suppress_stdout():
    """opendartreader 의 자체 print 출력 suppress.

    라이브러리가 'reprt_code...', '조회된 데이타가 없습니다', '전자공시...' 등을
    매 호출마다 print — 이 noise 를 잠시 차단.
    """
    saved = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = saved


def _get_api_key() -> str | None:
    """env DART_API_KEY 또는 config.yaml dart_api_key 우선."""
    key = os.getenv("DART_API_KEY")
    if key:
        return key
    try:
        import yaml
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        return cfg.get("dart_api_key")
    except Exception:
        return None


def _client():
    """opendartreader.OpenDartReader 인스턴스 반환. API key 미설정 시 None."""
    key = _get_api_key()
    if not key:
        return None
    # OpenDartReader 패키지는 버전·설치 상태에 따라 import 패턴이 다름.
    # 여러 형태 순차 시도.
    candidates = [
        ("OpenDartReader",                 "OpenDartReader"),   # FinanceData 표준
        ("OpenDartReader.OpenDartReader",  "OpenDartReader"),
        ("opendartreader",                 "OpenDartReader"),
        ("opendartreader",                 "opendartreader"),
    ]
    import importlib
    last_err = None
    for module_path, class_name in candidates:
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name, None)
            if cls is None and callable(mod):
                cls = mod
            if cls is None:
                continue
            return cls(key)
        except Exception as e:
            last_err = e
            continue
    print(f"[dart] 클라이언트 초기화 실패: {last_err}")
    print(f"[dart] 'python -c \"import OpenDartReader; print(OpenDartReader.__file__, dir(OpenDartReader))\"' 결과 공유 부탁")
    return None


# ── 핵심 재무지표 ─────────────────────────────────────────────────────────
def fetch_financials(ticker: str, year: int, report_type: str = "annual", refresh: bool = False) -> dict | None:
    """단일 종목·연도 재무제표 핵심 항목 추출.

    Args:
        ticker:      6자리 종목코드
        year:        보고 연도 (예: 2023)
        report_type: 'annual' (사업보고서) | 'q1' | 'q2' | 'q3'

    Returns:
        {
            'revenue':         매출액,
            'gross_profit':    매출총이익,
            'operating_inc':   영업이익,
            'net_income':      순이익,
            'total_assets':    총자산,
            'total_equity':    자기자본,
            'total_debt':      총부채,
            'op_cash_flow':    영업현금흐름,
            'reprt_code':      DART 보고서 코드,
        }
        실패 시 None.
    """
    dart = _client()
    if dart is None:
        return None
    cache_path = CACHE_DART / ticker / f"{year}_{report_type}.parquet"
    if cache_path.exists() and not refresh:
        try:
            df = pd.read_parquet(cache_path)
            return df.iloc[0].to_dict()
        except Exception:
            pass
    # 이전에 빈 응답으로 마킹된 종목은 skip
    empty_marker = cache_path.with_suffix(".empty")
    if empty_marker.exists() and not refresh:
        return None

    reprt_code = {"annual": "11011", "q1": "11013", "q2": "11012", "q3": "11014"}[report_type]
    # finstate_all 우선 (전체 재무항목, 매출원가/매출총이익 포함). 실패 시 finstate 폴백.
    # opendartreader 의 라이브러리 자체 print 는 suppress
    fs = None
    with _suppress_stdout():
        try:
            fs = dart.finstate_all(ticker, year, reprt_code=reprt_code, fs_div="CFS")
            if fs is None or fs.empty:
                fs = dart.finstate_all(ticker, year, reprt_code=reprt_code, fs_div="OFS")
        except Exception:
            fs = None
        if fs is None or fs.empty:
            try:
                fs = dart.finstate(ticker, year, reprt_code=reprt_code)
            except Exception:
                fs = None
    if fs is None or fs.empty:
        # 빈 캐시 마커로 다음 fetch 에서 skip
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        empty_marker = cache_path.with_suffix(".empty")
        empty_marker.touch()
        return None

    # 표준 항목 추출 (DART 계정명 매핑)
    name_map = {
        "revenue":       ["매출액", "수익(매출액)", "영업수익"],
        "cost_of_sales": ["매출원가"],
        "gross_profit":  ["매출총이익"],
        "operating_inc": ["영업이익", "영업이익(손실)"],
        "net_income":    ["당기순이익", "당기순이익(손실)"],
        "total_assets":  ["자산총계"],
        "total_equity":  ["자본총계"],
        "total_debt":    ["부채총계"],
    }
    result: dict = {"ticker": ticker, "year": year, "report": report_type, "reprt_code": reprt_code}
    for key, names in name_map.items():
        val = None
        for n in names:
            # 1순위: 정확 일치 (부채총계 vs 부채와자본총계 같은 substring 충돌 방지)
            mask = fs["account_nm"] == n
            if not mask.any():
                # 2순위: substring 매칭 (regex 비활성 — 영업이익(손실) 의 () 정규식 그룹 해석 방지)
                mask = fs["account_nm"].str.contains(n, na=False, regex=False)
            if mask.any():
                amt = fs.loc[mask, "thstrm_amount"].iloc[0]
                try:
                    val = float(str(amt).replace(",", ""))
                except Exception:
                    pass
                break
        result[key] = val

    # 매출총이익 누락 시 매출액 - 매출원가 로 역산 (IFRS 표준 재무제표는 매출원가만 표기되는 경우 많음)
    if result.get("gross_profit") is None and result.get("revenue") and result.get("cost_of_sales"):
        result["gross_profit"] = result["revenue"] - result["cost_of_sales"]

    # 영업현금흐름 (현금흐름표 항목, finstate 안 들어있을 수 있음)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([result]).to_parquet(cache_path)
    return result


def compute_derived_metrics(fin: dict, fin_prev: dict | None = None) -> dict:
    """재무제표에서 유도 지표 계산.

    - gp_a:            GP / 총자산 (퀄리티)
    - operating_margin: 영업이익률
    - roa:             순이익 / 총자산
    - roe:             순이익 / 자기자본
    - debt_ratio:      부채 / 자본
    - asset_growth:    총자산 성장률 (전기 대비)
    - fscore_partial:  F-Score 일부 (전체는 별도 분기 비교 필요)
    """
    if not fin:
        return {}
    out = {}
    ta = fin.get("total_assets")
    te = fin.get("total_equity")
    td = fin.get("total_debt")
    rev = fin.get("revenue")
    gp = fin.get("gross_profit")
    op = fin.get("operating_inc")
    ni = fin.get("net_income")

    if gp and ta and ta > 0:
        out["gp_a"] = gp / ta
    if op and rev and rev > 0:
        out["operating_margin"] = op / rev
    if ni and ta and ta > 0:
        out["roa"] = ni / ta
    if ni and te and te > 0:
        out["roe"] = ni / te
    if td and te and te > 0:
        out["debt_ratio"] = td / te

    if fin_prev:
        ta_prev = fin_prev.get("total_assets")
        if ta and ta_prev and ta_prev > 0:
            out["asset_growth"] = (ta - ta_prev) / ta_prev

        # F-Score 핵심 (4개 항목, 전체 9개 중)
        ni_prev = fin_prev.get("net_income")
        score = 0
        if ni and ni > 0: score += 1                       # 1. 당기 순이익 > 0
        if ni and ni_prev and ni > ni_prev: score += 1     # 2. 순이익 증가
        if td and ta and (td/ta < (fin_prev.get("total_debt", 0)/ta_prev if ta_prev else 1)): score += 1
                                                            # 3. 부채비율 감소
        if ni and ta and ni/ta > (ni_prev/ta_prev if (ni_prev and ta_prev) else 0): score += 1
                                                            # 4. ROA 증가
        out["fscore_partial"] = score   # 0~4점 (전체 9 중 4개만)
    return out


def fetch_kospi_financials_for_year(
    tickers: list[str],
    year: int,
    report_type: str = "annual",
) -> pd.DataFrame:
    """KOSPI 종목들의 단일 연도 재무제표 일괄 fetch.

    반환: ticker × [revenue, gross_profit, operating_inc, net_income,
                   total_assets, total_equity, total_debt, gp_a, roa, roe, ...]
    """
    from tqdm import tqdm
    rows = []
    for t in tqdm(tickers, desc=f"DART {year} {report_type}"):
        fin = fetch_financials(t, year, report_type)
        if fin:
            derived = compute_derived_metrics(fin)
            fin.update(derived)
            rows.append(fin)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("ticker")
