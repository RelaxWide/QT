"""
한국 주식 펀더멘털 데이터 fetch (PyKRX 기반).

지원 지표 (PyKRX `get_market_fundamental` / `get_market_cap`):
- PER, PBR, EPS, BPS, DPS, 배당수익률
- 시가총액, 발행주식수, 종가

캐시:
- data/raw/kr/fundamentals/{YYYYMMDD}.parquet — 일자별 전체 KOSPI 펀더멘털 스냅샷
- data/raw/kr/marketcap/{YYYYMMDD}.parquet — 일자별 시가총액

KRX 인증 필요: 환경변수 KRX_ID, KRX_PW 설정 후 사용 가능.
미설정 시 빈 응답 → ffill 폴백.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

log = logging.getLogger(__name__)

CACHE_FUND = Path("data/raw/kr/fundamentals")
CACHE_MCAP = Path("data/raw/kr/marketcap")


def krx_login_status() -> str:
    """현재 PyKRX KRX 로그인 세션 상태 (진단용).

    반환: "ok" | "no_credentials" | "login_failed" | "unknown"
    """
    try:
        from pykrx.website.comm.auth import get_auth_session
        sess = get_auth_session()
        if sess is None:
            import os
            if os.getenv("KRX_ID") and os.getenv("KRX_PW"):
                return "login_failed"
            return "no_credentials"
        return "ok" if sess.is_valid() else "login_failed"
    except Exception:
        return "unknown"


def _ymd(date) -> str:
    """date → 'YYYYMMDD' 형식."""
    return pd.Timestamp(date).strftime("%Y%m%d")


def _ymd_dash(date) -> str:
    return pd.Timestamp(date).strftime("%Y-%m-%d")


# ── 시점별 KOSPI 전체 fundamental 조회 ─────────────────────────────────────
def fetch_pykrx_fundamentals(date) -> pd.DataFrame:
    """PyKRX 로 특정 일자 KOSPI 전 종목 PER/PBR/EPS/BPS 등 일괄 조회.

    반환: ticker × [bps, per, pbr, eps, div_yield, dps]
    KRX_ID 미설정 시 빈 DataFrame 반환.
    """
    CACHE_FUND.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_FUND / f"{_ymd(date)}.parquet"
    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass

    try:
        from pykrx import stock as _kx
        df = _kx.get_market_fundamental_by_ticker(_ymd(date), market="KOSPI")
        if df is None or df.empty:
            return pd.DataFrame()
        # PyKRX 컬럼: BPS, PER, PBR, EPS, DIV, DPS
        df = df.rename(columns={
            "BPS": "bps", "PER": "per", "PBR": "pbr",
            "EPS": "eps", "DIV": "div_yield", "DPS": "dps",
        })
        # KRX 가 휴장일이면 모두 0 반환 — 그 경우 빈 DF 처리
        if (df.fillna(0).abs().sum().sum() == 0):
            return pd.DataFrame()
        df.index.name = "ticker"
        df.to_parquet(cache_path)
        return df
    except Exception as e:
        log.warning(f"[fundamentals_kr] PyKRX fundamental fetch 실패 ({_ymd(date)}): "
                    f"{type(e).__name__}: {e} | KRX 로그인 상태: {krx_login_status()}")
        return pd.DataFrame()


def fetch_market_cap_kr(date) -> pd.DataFrame:
    """PyKRX 로 특정 일자 KOSPI 전 종목 시가총액 일괄 조회.

    반환: ticker × [close, shares, mcap, volume, value]
    """
    CACHE_MCAP.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_MCAP / f"{_ymd(date)}.parquet"
    if cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass

    try:
        from pykrx import stock as _kx
        df = _kx.get_market_cap_by_ticker(_ymd(date), market="KOSPI")
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "종가":    "close",
            "거래량":  "volume",
            "거래대금": "value",
            "시가총액": "mcap",
            "상장주식수": "shares",
        })
        df.index.name = "ticker"
        df.to_parquet(cache_path)
        return df
    except Exception as e:
        log.warning(f"[fundamentals_kr] PyKRX market cap fetch 실패 ({_ymd(date)}): "
                    f"{type(e).__name__}: {e} | KRX 로그인 상태: {krx_login_status()}")
        return pd.DataFrame()


# ── 시점 리스트 → 종목별 panel 빌드 ─────────────────────────────────────
def build_fundamentals_panel(
    dates: Iterable,
    tickers: Iterable[str] | None = None,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """리밸런싱 일자 리스트 × 종목 × 펀더멘털 지표.

    반환: {ticker: DataFrame(date × [per, pbr, eps, bps, div_yield, mcap, shares, close])}
    fundamentals + marketcap 조인.
    """
    dates = [pd.Timestamp(d) for d in dates]
    if tickers is not None:
        tickers = set(tickers)

    # 일자별 일괄 fetch (캐시 사용)
    per_date_fund: dict[pd.Timestamp, pd.DataFrame] = {}
    per_date_cap:  dict[pd.Timestamp, pd.DataFrame] = {}
    from tqdm import tqdm
    for d in tqdm(dates, desc="Fundamentals fetch"):
        per_date_fund[d] = fetch_pykrx_fundamentals(d)
        per_date_cap[d]  = fetch_market_cap_kr(d)

    # ticker 별 panel 재구성
    all_tickers: set = set()
    for d in dates:
        if not per_date_fund[d].empty:
            all_tickers.update(per_date_fund[d].index)
    if tickers is not None:
        all_tickers &= tickers

    panel: dict[str, pd.DataFrame] = {}
    for ticker in all_tickers:
        rows = []
        for d in dates:
            fund = per_date_fund.get(d)
            cap  = per_date_cap.get(d)
            row = {"date": d}
            if fund is not None and not fund.empty and ticker in fund.index:
                for col in ("per", "pbr", "eps", "bps", "div_yield", "dps"):
                    if col in fund.columns:
                        row[col] = fund.at[ticker, col]
            if cap is not None and not cap.empty and ticker in cap.index:
                for col in ("close", "mcap", "shares", "volume", "value"):
                    if col in cap.columns:
                        row[col] = cap.at[ticker, col]
            rows.append(row)
        df = pd.DataFrame(rows).set_index("date")
        panel[ticker] = df
    return panel


# ── KIS 기반 panel 빌드 (pykrx/KRX 로그인 완전 우회) ─────────────────────
def build_fundamentals_panel_kis(
    date,
    tickers: Iterable[str],
    cli=None,
    sleep_sec: float = 0.08,
) -> dict[str, pd.DataFrame]:
    """KIS `inquire-price` 종목별 조회로 PER/PBR/EPS/BPS/시가총액 panel 빌드.

    pykrx 는 KRX 로그인(비밀번호 평문 전송)이 구조적으로 막혀 있어 쓸 수 없다
    (fundamentals_kr.krx_login_status 참고). KIS 는 이미 인증된 세션이고
    종목 단위 조회라 전종목 상위 N개만 보여주는 KRX 순위 API의 상한(30건)
    제약도 없다. 분기 1회만 호출하므로 종목당 한 번씩 순차 호출해도 충분.

    반환: build_fundamentals_panel 과 동일한 형태 — {ticker: DataFrame(date × [...])}
    (단일 날짜만 들어있음 — 실시간 조회라 과거 시점 재현은 불가능, 라이브 전용)
    """
    import time
    from tqdm import tqdm

    if cli is None:
        from live_trading.kis_client import KISClient
        cli = KISClient.from_config(allow_prod=True, market="kr")

    date = pd.Timestamp(date)
    tickers = list(tickers)
    panel: dict[str, pd.DataFrame] = {}
    n_failed = 0
    for ticker in tqdm(tickers, desc="KIS fundamentals fetch"):
        try:
            d = cli.get_fundamentals_kr(ticker)
        except Exception as e:
            log.warning(f"[fundamentals_kr] KIS {ticker} 조회 예외: {e}")
            d = {}
        if not d or d.get("mcap", 0) <= 0:
            n_failed += 1
            time.sleep(sleep_sec)
            continue
        panel[ticker] = pd.DataFrame([{
            "date": date, "per": d["per"], "pbr": d["pbr"], "eps": d["eps"],
            "bps": d["bps"], "close": d["close"], "mcap": d["mcap"],
            "shares": d["shares"],
        }]).set_index("date")
        time.sleep(sleep_sec)

    log.info(f"[fundamentals_kr] KIS panel 빌드 완료 — {len(panel)}/{len(tickers)}종목 "
              f"성공 ({n_failed}건 실패/거래정지 추정)")
    return panel


# ── 백분위 랭킹 유틸 ─────────────────────────────────────────────────────
def percentile_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """NaN/0 무시한 백분위 랭크 (0~1).
    ascending=True: 작을수록 0 에 가까움 (PER 낮은 게 좋으면 ascending)
    """
    valid = series.replace([0, float("inf"), float("-inf")], pd.NA).dropna()
    if valid.empty:
        return pd.Series(index=series.index, dtype=float)
    ranks = valid.rank(ascending=ascending, pct=True)
    out = pd.Series(index=series.index, dtype=float)
    out.loc[ranks.index] = ranks
    return out


# ── 가치 팩터 계산 ────────────────────────────────────────────────────────
def derive_value_factors(
    panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    universe: list[str] | None = None,
    min_per: float = 0.5,
    max_per: float = 100.0,
    min_pbr: float = 0.1,
) -> pd.DataFrame:
    """슈퍼가치 팩터: PER/PBR 백분위 → value_score (낮을수록 좋음).
    적자/극단치 종목 제외.

    반환: ticker × [per, pbr, per_rank, pbr_rank, value_score, valid]
    """
    rows = []
    for ticker, df in panel.items():
        if universe and ticker not in universe:
            continue
        if date not in df.index:
            # 직전 가용 시점 사용
            available = df.index[df.index <= date]
            if len(available) == 0:
                continue
            row = df.loc[available[-1]]
        else:
            row = df.loc[date]
        per = row.get("per", float("nan"))
        pbr = row.get("pbr", float("nan"))
        if pd.isna(per) or pd.isna(pbr):
            continue
        if per <= min_per or per >= max_per or pbr <= min_pbr:
            continue
        rows.append({"ticker": ticker, "per": per, "pbr": pbr})

    if not rows:
        return pd.DataFrame(columns=["per", "pbr", "per_rank", "pbr_rank", "value_score"])

    out = pd.DataFrame(rows).set_index("ticker")
    out["per_rank"] = percentile_rank(out["per"], ascending=True)
    out["pbr_rank"] = percentile_rank(out["pbr"], ascending=True)
    out["value_score"] = (out["per_rank"] + out["pbr_rank"]) / 2
    return out.dropna(subset=["value_score"])


# ── 퀄리티 팩터 계산 (PyKRX 만으로 가능한 ROE+변동성) ────────────────────
def derive_quality_factors_pykrx_only(
    panel: dict[str, pd.DataFrame],
    price_data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    universe: list[str] | None = None,
    vol_lookback_days: int = 120,
    min_roe: float = 0.0,
) -> pd.DataFrame:
    """슈퍼퀄리티 팩터 (PyKRX 한정): ROE (EPS/BPS) + 120일 변동성 백분위.

    반환: ticker × [roe, vol_120d, roe_rank, vol_rank, quality_score]
    DART 통합 시 GP/A, F-Score, 자산성장률 추가.
    """
    rows = []
    for ticker, df in panel.items():
        if universe and ticker not in universe:
            continue
        if date not in df.index:
            available = df.index[df.index <= date]
            if len(available) == 0:
                continue
            row = df.loc[available[-1]]
        else:
            row = df.loc[date]
        eps = row.get("eps", float("nan"))
        bps = row.get("bps", float("nan"))
        if pd.isna(eps) or pd.isna(bps) or bps <= 0 or eps <= min_roe * bps:
            continue
        roe = eps / bps   # ROE 근사

        # 변동성: price_data 의 close.std() 백분율
        px_df = price_data.get(ticker)
        if px_df is None or px_df.empty:
            continue
        recent = px_df[px_df.index <= date].tail(vol_lookback_days)
        if len(recent) < vol_lookback_days // 2:
            continue
        rets = recent["close"].pct_change().dropna()
        if rets.empty or rets.std() <= 0:
            continue
        vol_120d = float(rets.std())

        rows.append({"ticker": ticker, "roe": roe, "vol_120d": vol_120d})

    if not rows:
        return pd.DataFrame(columns=["roe", "vol_120d", "roe_rank", "vol_rank", "quality_score"])

    out = pd.DataFrame(rows).set_index("ticker")
    out["roe_rank"] = percentile_rank(out["roe"], ascending=False)   # 높을수록 좋음 → 큰 값이 작은 rank
    out["vol_rank"] = percentile_rank(out["vol_120d"], ascending=True)   # 낮을수록 좋음
    out["quality_score"] = (out["roe_rank"] + out["vol_rank"]) / 2
    return out.dropna(subset=["quality_score"])
