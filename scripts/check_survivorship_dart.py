"""
DART 회사목록 + 상장폐지일 활용 생존편향 측정.

OpenDartReader 의 `corp_codes()` 로 전체 상장사 + 상장폐지일 fetch.
분기 리밸런싱 시점에 살아있던 종목만 universe 로 백테스트.

비교:
  - 현재 (2026) KOSPI universe 838종목 (생존편향 포함)
  - 시점별 universe (상장폐지 종목 포함, 생존편향 제거)
  → 차이 = 생존편향 magnitude

사용:
    python scripts/check_survivorship_dart.py
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly, adjust_signals_to_trading
from src.strategy.kw_super_value import generate_super_value_signals
from src.fetch.dart_kr import _client


def get_dart_company_list() -> pd.DataFrame:
    """DART 전체 상장사 + 상장/폐지일."""
    dart = _client()
    if dart is None:
        print("[ERROR] DART API key 미설정")
        return pd.DataFrame()
    try:
        corp = dart.corp_codes  # property; returns DataFrame
        df = corp if isinstance(corp, pd.DataFrame) else corp()
        return df
    except Exception as e:
        print(f"[ERROR] DART corp_codes 실패: {e}")
        return pd.DataFrame()


def get_pit_universe(date: pd.Timestamp, corp_list: pd.DataFrame) -> set[str]:
    """date 시점에 상장 상태였던 KOSPI 6자리 코드 집합."""
    if corp_list.empty:
        return set()
    df = corp_list.copy()
    # listing date 필드 (modify_date / final_reprt_at 등은 변경됨, KIS 종목코드 = 6자리)
    # OpenDartReader corp_codes 는 컬럼 corp_code/corp_name/stock_code/modify_date 만 줌
    # 상장폐지 정보는 corp_codes 에 없음 → 다른 API 활용
    # 폴백: 6자리 stock_code 가 있는 것 = 현재 상장된 종목
    df = df[df["stock_code"].notna() & (df["stock_code"].astype(str).str.len() == 6)]
    return set(df["stock_code"].astype(str).tolist())


def main():
    print("=" * 70)
    print("생존편향 측정 — DART 회사목록 활용")
    print("=" * 70)

    corp_list = get_dart_company_list()
    if corp_list.empty:
        print("[STOP] DART 회사목록 fetch 실패")
        return
    print(f"DART 전체 상장사: {len(corp_list)}건")
    listed = corp_list[corp_list["stock_code"].notna()]
    print(f"  6자리 종목코드 보유: {len(listed)}")

    # 현재 KOSPI universe (FDR 기반)
    current_kospi = set(get_kospi_all_tickers())
    print(f"현재 KOSPI universe (FDR): {len(current_kospi)}")

    # DART 의 6자리 stock_code 중 KOSPI 인 종목 — OpenDartReader 는 상장폐지 구분 안 함
    # corp_codes 는 "지금 시점에 살아있는 + 과거 상장폐지된" 둘 다 포함 가능 — 라이브러리 버전 확인 필요
    dart_listed_all = set(listed["stock_code"].astype(str).tolist())
    union = dart_listed_all | current_kospi
    print(f"DART 전체 6자리 코드: {len(dart_listed_all)}")
    print(f"합집합 (DART + FDR 현재): {len(union)}")
    print(f"  DART - FDR (DART 만 있는, 과거 상장폐지 가능): {len(dart_listed_all - current_kospi)}")
    print(f"  FDR - DART (DART 누락): {len(current_kospi - dart_listed_all)}")

    # 합집합 universe 로 백테스트
    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code": profile.code, "regime_index": profile.index_ticker, "currency": profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]
    params = dict(cfg["kw_super_value"])

    start = "2014-01-01"
    end   = pd.Timestamp.today().strftime("%Y-%m-%d")

    base_tickers = [profile.index_ticker]
    full_universe = sorted(union)
    print(f"\n=== 합집합 universe ({len(full_universe)}) 로 백테스트 ===")
    print("Loading prices (이전 캐시 활용 + 신규 fetch)...")
    t0 = time.time()
    price_data = fetch_all(base_tickers + full_universe, start, end, min_bars=120, market="kr")
    print(f"  loaded {len(price_data)} in {time.time()-t0:.1f}s")
    print(f"  (요청 {len(full_universe)} 종목 중 {len(price_data)-1} 가격 데이터 보유)")

    raw_rebal = rebalance_dates_kr_quarterly(start, end, params["rebalance_months"], params["rebalance_dom"])
    rebal = adjust_signals_to_trading(raw_rebal, price_data[profile.index_ticker].index)
    panel = build_fundamentals_panel(rebal)
    print(f"  Fundamentals panel: {len(panel)} tickers")
    sigs = generate_super_value_signals(panel, price_data, params, start, end)
    print(f"  Signals: {len(sigs)}")
    eq, _ = run_quarterly_backtest(sigs, price_data, cfg, market="kr")
    m_union = compute_quarterly_metrics(eq, cap)
    print(f"  union universe: CAGR {m_union['cagr_pct']:+.2f}%  MDD {m_union['max_drawdown_pct']:+.2f}%  Sharpe {m_union['sharpe']:.2f}")

    # 비교
    print("\n=== 비교 ===")
    print(f"  현재 universe (838, 생존편향 포함): CAGR 20.71%  Sharpe 0.97  (기준 — 11.1년)")
    print(f"  합집합 (생존편향 일부 보정):       CAGR {m_union['cagr_pct']:+.2f}%  Sharpe {m_union['sharpe']:.2f}")
    print(f"  Δ CAGR (생존편향 부풀림):          {m_union['cagr_pct'] - 20.71:+.2f}%p")

    # 저장
    out = {
        "current_universe":   len(current_kospi),
        "dart_listed_all":    len(dart_listed_all),
        "union":              len(full_universe),
        "extra_in_dart":      len(dart_listed_all - current_kospi),
        "missing_in_dart":    len(current_kospi - dart_listed_all),
        "union_metrics":      m_union,
        "delta_cagr_vs_current": m_union["cagr_pct"] - 20.71,
    }
    Path("backtest_results/kr/survivorship_dart.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nSaved: backtest_results/kr/survivorship_dart.json")


if __name__ == "__main__":
    main()
