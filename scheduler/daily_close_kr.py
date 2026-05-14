"""
scheduler/daily_close_kr.py
실행 시각: 평일 KST 16:00 (KOSPI 정규장 마감 15:30 + 30분 마진)
역할:
  1. Clenow / Weinstein 매도 신호 (MA 이탈, rank 탈락) → 즉시 KIS 주문 (KR)
  2. 화요일 (KST 화 16:00): 수요일 시초 매수 후보를 live_trading/wed_buy_pending_kr.json 저장
     → wednesday_morning_buy_kr.py (수 09:00 KST) 가 KIS 주문 전송
  3. KIS 잔고 동기화 (KR 포지션만)

수동:
    python scheduler/daily_close_kr.py [--dry-run]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.orders import OrderManager
from live_trading.risk_guard import RiskGuard
from src.markets import get_profile

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

WED_BUY_PENDING_KR = Path("live_trading/wed_buy_pending_kr.json")
MARKET = "kr"


def main(dry_run: bool = False, force_wednesday: bool = False):
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    profile = get_profile(MARKET)
    cfg["market"] = {
        "code":         profile.code,
        "regime_index": profile.index_ticker,
        "currency":     profile.currency,
    }
    cfg.setdefault("clenow_strategy",    {}).setdefault("index_ticker", profile.index_ticker)
    cfg["clenow_strategy"].setdefault("min_price",    profile.min_price)
    cfg.setdefault("weinstein_strategy", {}).setdefault("min_price",    profile.min_price)
    cfg["weinstein_strategy"].setdefault("weekly_freq", profile.calendar_freq)

    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    allow_new = ok
    if not ok:
        log.warning(f"[daily_close_kr] 신규 주문 차단: {reason}")

    # KST 기준 — 화요일 16:00 KST 에 daily_close_kr 가 실행되어, 수요일 09:00 매수 준비.
    # (US 와 달리 KR 매수는 같은 요일 데이터로 다음 영업일 시초가 매수)
    now_kst = pd.Timestamp.now(tz="Asia/Seoul")
    # 'KR 매수 후보 저장' 트리거: 화요일 (다음날 수요일 시초 매수)
    is_tue = now_kst.weekday() == 1 or force_wednesday

    # ── 데이터 로드 ──────────────────────────────────────────────────────
    from src.fetch.universe import get_kospi200_tickers
    from src.fetch.prices import fetch_all
    tickers = get_kospi200_tickers()
    if profile.index_ticker not in tickers:
        tickers = [profile.index_ticker] + tickers
    price_data = fetch_all(tickers,
                           cfg["data"]["start_date"],
                           cfg["data"]["end_date"],
                           min_bars=150, market=MARKET)
    log.info(f"[daily_close_kr] {len(price_data)} 종목 로드 완료")

    latest_dates = [df.index.max() for df in price_data.values() if not df.empty]
    if not latest_dates:
        log.error("[daily_close_kr] 가격 데이터 없음 - 종료")
        return
    today = max(latest_dates)
    log.info(f"[daily_close_kr] data_today={today.date()} | trigger_buy_pending={is_tue}")

    om = OrderManager.from_config(allow_prod=True, market=MARKET)

    # ── Clenow 신호 ──────────────────────────────────────────────────────
    from paper_trading.live_signals import get_clenow_signals
    from paper_trading.simple_tracker import load_simple_positions

    # 라이브 KR 포지션은 live_trading/positions_live_clenow_kr.json 에서 동기화하지만
    # 신호 계산은 paper 의 보유 추적만 알면 됨 (라이브 보유 = paper 와 동기화 가정)
    cl_pos  = load_simple_positions("clenow", market=MARKET)
    cl_sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today,
                                 is_wednesday=is_tue)

    cl_sell_only = {
        "sell_ma100":  cl_sigs.get("sell_ma100", []),
        "sell_ranked": cl_sigs.get("sell_ranked", []),
        "buy":         [],
    }
    cl_sell_orders = om.place_clenow_orders(cl_sell_only, price_data, today, dry_run)
    log.info(f"[daily_close_kr] Clenow 매도 주문 {len(cl_sell_orders)}건")

    pending = {}
    if is_tue and allow_new and cl_sigs.get("buy"):
        pending["clenow"] = {
            "signal_date": str(today.date()),
            "symbols":     cl_sigs["buy"],
        }
        log.info(f"[daily_close_kr] Clenow 매수 후보 {len(cl_sigs['buy'])}건 → pending 저장")

    # ── Weinstein 신호 ───────────────────────────────────────────────────
    from paper_trading.live_signals import get_weinstein_signals
    w_pos  = load_simple_positions("weinstein", market=MARKET)
    w_sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today,
                                   is_wednesday=is_tue)

    w_sell_only = {
        "sell_ma30": w_sigs.get("sell_ma30", []),
        "buy":       [],
    }
    w_sell_orders = om.place_weinstein_orders(w_sell_only, price_data, today, dry_run)
    log.info(f"[daily_close_kr] Weinstein 매도 주문 {len(w_sell_orders)}건")

    if is_tue and allow_new and w_sigs.get("buy"):
        pending["weinstein"] = {
            "signal_date": str(today.date()),
            "symbols":     w_sigs["buy"],
        }
        log.info(f"[daily_close_kr] Weinstein 매수 후보 {len(w_sigs['buy'])}건 → pending 저장")

    if pending:
        WED_BUY_PENDING_KR.parent.mkdir(exist_ok=True)
        WED_BUY_PENDING_KR.write_text(
            json.dumps(pending, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"[daily_close_kr] pending 파일 저장: {WED_BUY_PENDING_KR}")
    elif is_tue and WED_BUY_PENDING_KR.exists():
        WED_BUY_PENDING_KR.unlink()
        log.info("[daily_close_kr] 매수 후보 없음 - 이전 pending 삭제")

    log.info("[daily_close_kr] 완료")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run",          action="store_true")
    p.add_argument("--force-wednesday",  action="store_true",
                   help="요일 무관 매수 후보 생성 강제 (테스트용)")
    args = p.parse_args()
    main(dry_run=args.dry_run, force_wednesday=args.force_wednesday)
