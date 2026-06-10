"""
scheduler/daily_close.py
실행 시각: 평일 06:00 KR (미국장 마감 직후)
역할:
  1. Clenow / Weinstein 매도 신호 (MA 이탈, rank_exit) → 즉시 주문
  2. KR 수요일: 매수 후보를 live_trading/wed_buy_pending.json 으로 저장
     (wednesday_morning_buy.py 가 Wed 11 AM ET 에 KIS 주문 전송)
  3. Phase 4 신규 신호 생성 → pending.json 저장 (다음날 morning_entry가 주문)
  4. KIS 잔고 동기화

수동 실행:
    python scheduler/daily_close.py [--dry-run]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler._log_helper import setup_file_logger
setup_file_logger("daily_close")

from live_trading.kis_client import KISClient
from live_trading.orders import OrderManager
from live_trading.account import sync_all
from live_trading.risk_guard import RiskGuard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

WED_BUY_PENDING = Path("live_trading/wed_buy_pending.json")


def main(dry_run: bool = False, refresh: bool = False):
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"[daily_close] 신규 주문 차단: {reason}")
        # 청산 신호는 그래도 처리
        allow_new = False
    else:
        allow_new = True

    # KR 시간 기준 요일 (Wed 06:00 KR = Tue close 처리 → 그날 11 AM ET 매수 준비)
    now_kst = pd.Timestamp.now(tz="Asia/Seoul")
    is_wed  = now_kst.weekday() == 2

    # ── 1. 가격 데이터 로드 ──────────────────────────────────────────────
    from src.fetch.universe import get_sp500_tickers
    from src.fetch.prices import fetch_all
    tickers    = get_sp500_tickers()
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=150,
                           refresh=refresh)
    log.info(f"[daily_close] {len(price_data)} 종목 로드 완료")

    # 데이터의 마지막 거래일을 today 로 사용 (실행 시점/타임존 영향 제거)
    latest_dates = [df.index.max() for df in price_data.values() if not df.empty]
    if not latest_dates:
        log.error("[daily_close] 가격 데이터 없음 - 종료")
        return
    today = max(latest_dates)
    log.info(f"[daily_close] data_today={today.date()} | KR_is_wed={is_wed}")

    om = OrderManager.from_config(allow_prod=True)

    # ── 2. Clenow 신호 ────────────────────────────────────────────────────
    from paper_trading.live_signals import get_clenow_signals
    from paper_trading.simple_tracker import load_simple_positions
    cl_pos  = load_simple_positions("clenow")
    cl_sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wed)

    # 매도 (MA100 이탈 + rank 탈락) 는 즉시 주문
    cl_sell_only = {
        "sell_ma100":  cl_sigs.get("sell_ma100", []),
        "sell_ranked": cl_sigs.get("sell_ranked", []),
        "buy":         [],
    }
    cl_sell_orders = om.place_clenow_orders(cl_sell_only, price_data, today, dry_run)
    log.info(f"[daily_close] Clenow 매도 주문 {len(cl_sell_orders)}건")

    # 매수 후보는 pending 저장 (수요일 + allow_new 인 경우만)
    pending_payload = {}
    if is_wed and allow_new and cl_sigs.get("buy"):
        pending_payload["clenow"] = {
            "signal_date": str(today.date()),
            "symbols":     cl_sigs["buy"],
        }
        log.info(f"[daily_close] Clenow 매수 후보 {len(cl_sigs['buy'])}건 → pending 저장")

    # ── 3. Weinstein 신호 ────────────────────────────────────────────────
    from paper_trading.live_signals import get_weinstein_signals
    w_pos  = load_simple_positions("weinstein")
    w_sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wed)

    w_sell_only = {
        "sell_ma30": w_sigs.get("sell_ma30", []),
        "buy":       [],
    }
    w_sell_orders = om.place_weinstein_orders(w_sell_only, price_data, today, dry_run)
    log.info(f"[daily_close] Weinstein 매도 주문 {len(w_sell_orders)}건")

    if is_wed and allow_new and w_sigs.get("buy"):
        pending_payload["weinstein"] = {
            "signal_date": str(today.date()),
            "symbols":     w_sigs["buy"],
        }
        log.info(f"[daily_close] Weinstein 매수 후보 {len(w_sigs['buy'])}건 → pending 저장")

    # pending 파일 저장 / 정리
    if pending_payload:
        WED_BUY_PENDING.parent.mkdir(exist_ok=True)
        WED_BUY_PENDING.write_text(
            json.dumps(pending_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info(f"[daily_close] pending 파일 저장: {WED_BUY_PENDING}")
    elif is_wed and WED_BUY_PENDING.exists():
        # 수요일인데 후보 없음 → 옛 pending 정리
        WED_BUY_PENDING.unlink()
        log.info(f"[daily_close] 매수 후보 없음 - 이전 pending 삭제")

    # ── 4. Phase 4 신규 신호 → pending.json 저장 (익일 MOO) ─────────────
    if allow_new:
        _update_phase4_pending(cfg, cfg_live, price_data, today)

    # ── 5. KIS 잔고 동기화 + 핵심정보 텔레그램 1건 ───────────────────────
    if not dry_run:
        kis = KISClient.from_config(allow_prod=True)
        report = sync_all(kis)
        msg = _build_core_message(report, today, cl_sell_orders, w_sell_orders,
                                  pending_payload)
        log.info("[daily_close] " + msg.replace("\n", " | "))
        _notify(msg, cfg_live)

    log.info("[daily_close] 완료")


def _build_core_message(report, today, cl_sells, w_sells, pending) -> str:
    """daily_close 핵심정보 텔레그램. 종목별 스팸 대신 요약 1건."""
    L = [f"🇺🇸 <b>{today.date()} US 장마감 처리</b>"]

    bal = report.get("balance") or {}
    cash = bal.get("cash_usd", 0.0)
    sum_eval = sum(p.get("eval_amt", 0) for p in bal.get("positions", []))
    pnl = bal.get("total_pnl_usd", 0.0)
    L.append(f"잔고: 예수금 ${cash:,.2f} | 평가 ${sum_eval:,.2f} | 손익 ${pnl:+,.2f}")

    n_sells = len(cl_sells) + len(w_sells)
    if n_sells:
        L.append(f"💸 매도 {n_sells}건 (Clenow {len(cl_sells)} / Weinstein {len(w_sells)})")
    else:
        L.append("매도: 없음")

    if pending:
        parts = [f"{k} {len(v.get('symbols', []))}종목" for k, v in pending.items()]
        L.append("🔔 매수대기 (목 00:00 KST 실행): " + ", ".join(parts))

    L.append(f"보유: KIS {report.get('kis_count', 0)}종목 추적 중")

    # 진짜 이상만 경고 (거짓 '미기록' 스팸 제거됨)
    if report.get("untracked"):
        L.append("⚠️ 미할당 KIS 보유 (등록 필요): " + ", ".join(report["untracked"]))
    if report.get("missing"):
        items = ", ".join(f"{s}({st})" for s, st in report["missing"])
        L.append(f"⚠️ 외부 청산·미체결 의심: {items}")
    if report.get("mismatch"):
        items = ", ".join(f"{m['symbol']} 로컬{m['local_qty']}/KIS{m['kis_qty']}"
                          for m in report["mismatch"])
        L.append(f"⚠️ 수량 불일치: {items}")

    return "\n".join(L)


def _update_phase4_pending(cfg, cfg_live, price_data, today):
    """Phase 4 신호 재생성 → pending.json 업데이트."""
    from src.indicators.regime import compute_regime
    from src.indicators.factors import build_factor_matrices
    from src.strategy.factor_stack import generate_factor_signals
    from paper_trading.tracker import load_positions, save_pending, PendingEntry

    p1 = cfg["phase1_breakout_pullback"]
    p2 = cfg["phase2_cloud_support"]
    p3 = cfg["phase3_hybrid"]
    p4 = cfg["phase4_factor_stack"]
    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]
    p4["momentum_period"] = p4.get("momentum_period", 63)

    regime_on = compute_regime(
        cfg["data"]["start_date"],
        cfg["data"]["end_date"],
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )
    if today in regime_on.index and not bool(regime_on.at[today, "trade_ok"]):
        log.info("[phase4] 레짐 OFF - pending 업데이트 스킵")
        return

    mom_period = p4.get("momentum_period", 63)
    mom_rank, bbw_rank, spy_mom = build_factor_matrices(price_data, mom_period=mom_period)
    current_positions = load_positions()
    new_pending = []

    for sym, df in price_data.items():
        if sym in current_positions:
            continue
        sigs = generate_factor_signals(sym, df, p1, p2_filter, p4, mom_rank, bbw_rank, spy_mom)
        for s in sigs:
            if s.entry_date == today + pd.Timedelta(days=1):
                new_pending.append(PendingEntry(
                    symbol=sym,
                    signal_date=str(today.date()),
                    entry_price_est=float(df.at[today, "close"]) if today in df.index else s.entry_price,
                    stop=s.stop,
                    r=s.r,
                    targets=s.targets,
                    partial_weights=s.partial_weights,
                ))

    save_pending(new_pending)
    log.info(f"[phase4] pending {len(new_pending)}건 저장")


def _notify(msg: str, cfg_live: dict):
    try:
        token   = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"텔레그램 알림 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--refresh", action="store_true", help="가격 캐시 강제 갱신")
    args = p.parse_args()
    main(dry_run=args.dry_run, refresh=args.refresh)
