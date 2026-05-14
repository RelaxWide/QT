"""
KOSPI 유니버스 조회.

1차: PyKRX (KRX 공식 — KOSPI200 구성종목)
2차: FinanceDataReader 폴백 (KRX 마스터에서 KOSPI 시총 상위 200)
3차: 캐시 (data/raw/kr/_universe_kospi200.txt)
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

_CACHE = Path("data/raw/kr")
_KOSPI200_INDEX_CODE = "1028"   # KRX 의 KOSPI200 지수 코드


def _today_str() -> str:
    return _dt.date.today().strftime("%Y%m%d")


def _cache_path() -> Path:
    return _CACHE / "_universe_kospi200.txt"


def _load_cache() -> list[str] | None:
    p = _cache_path()
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return [line.strip() for line in text.splitlines() if line.strip()]


def _save_cache(tickers: list[str]) -> None:
    _CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path().write_text("\n".join(tickers), encoding="utf-8")


def _try_pykrx() -> list[str]:
    """KOSPI200 구성 종목 (PyKRX)."""
    from pykrx import stock as _kx
    # 영업일 기준으로 가장 가까운 과거 날짜 사용
    today = _today_str()
    tickers = _kx.get_index_portfolio_deposit_file(_KOSPI200_INDEX_CODE, today)
    if not tickers:
        # 영업일 아니거나 데이터 없으면 직전 영업일 시도
        for delta in range(1, 7):
            d = (_dt.date.today() - _dt.timedelta(days=delta)).strftime("%Y%m%d")
            tickers = _kx.get_index_portfolio_deposit_file(_KOSPI200_INDEX_CODE, d)
            if tickers:
                break
    if not tickers:
        raise RuntimeError("PyKRX: KOSPI200 구성종목 조회 실패")
    return sorted(tickers)


def _try_fdr() -> list[str]:
    """FinanceDataReader 폴백 — KRX 마스터에서 KOSPI 시총 상위 200."""
    import FinanceDataReader as fdr
    df = fdr.StockListing("KOSPI")
    if df.empty:
        raise RuntimeError("FDR: KOSPI 마스터 조회 실패")
    # FDR 컬럼명은 버전마다 변동. 시총 컬럼 후보들 탐색
    cap_col = None
    for c in ("Marcap", "MarketCap", "MktCap", "marcap", "marketcap"):
        if c in df.columns:
            cap_col = c
            break
    if cap_col is None:
        # 시총 컬럼 없으면 그냥 처음 200개 반환
        ticker_col = "Code" if "Code" in df.columns else "Symbol"
        return sorted(df[ticker_col].astype(str).str.zfill(6).head(200).tolist())
    ticker_col = "Code" if "Code" in df.columns else "Symbol"
    top = df.sort_values(cap_col, ascending=False).head(200)
    return sorted(top[ticker_col].astype(str).str.zfill(6).tolist())


def get_kospi200_tickers(refresh: bool = False) -> list[str]:
    """
    KOSPI200 구성 종목 6자리 코드 리스트.

    - PyKRX 우선 → 실패 시 FDR → 실패 시 캐시.
    - refresh=True 면 외부 API 강제 호출 (네트워크 필요).
    """
    if not refresh:
        cached = _load_cache()
        if cached:
            return cached

    last_err: Exception | None = None
    for fn in (_try_pykrx, _try_fdr):
        try:
            tickers = fn()
            if tickers:
                _save_cache(tickers)
                return tickers
        except Exception as e:
            last_err = e
            continue

    cached = _load_cache()
    if cached:
        return cached
    raise RuntimeError(f"KOSPI200 유니버스 조회 모두 실패: {last_err}")
