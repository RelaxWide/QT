"""
scheduler/daily_close.py
실행 시각: 평일 06:00 KR (미국장 마감 직후)
역할:
  1. Clenow / Weinstein 매도 신호 (MA 이탈, rank_exit) → 즉시 주문
  2. KR 수요일: 매수 후보를 live_trading/wed_buy_pending.json 으로 저장
     (wednesday_morning_buy.py 가 Wed 11 AM ET 에 KIS 주문 전송)
  3. Phase 4 신규 신호 생성 → live_trading/pending_phase4_live.json 저장
     (다음날 morning_entry가 주문). Phase 4 실거래 배분 0 이면 생략 — 페이퍼는 GHA 담당
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
from live_trading.orders import OrderManager, PHASE4_LIVE_PENDING, phase4_live_enabled
from live_trading.account import sync_all, reconcile_fills
from live_trading.risk_guard import RiskGuard, record_nav
from live_trading.freshness import expected_us_session, is_stale

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

WED_BUY_PENDING = Path("live_trading/wed_buy_pending.json")


def main(dry_run: bool = False, refresh: bool = False):
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    # 실계좌 NAV 를 먼저 기록해야 가드가 오늘 기준으로 판단한다
    nav_error = ""
    if not dry_run:
        try:
            if record_nav("us", KISClient.from_config(allow_prod=True).get_balance()) is None:
                nav_error = "KIS 잔고 조회 실패"
        except Exception as e:
            nav_error = str(e)
        if nav_error:
            log.error(f"[daily_close] NAV 기록 실패: {nav_error}")

    guard = RiskGuard.from_config(market="us")
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

    # 데이터 신선도 가드 — NYSE 달력상 "지금 있어야 할 마지막 거래일" 과 비교.
    # 하루라도 밀리면 강제 재다운로드, 그래도 밀리면 신호 판정 중단.
    # (2026-06 한 달 정체, 07 3회 재발 — 예전 BDay(3) 허용폭 안에서 낡은 신호·지정가가 나갔다)
    expected = expected_us_session()
    if is_stale(today, expected) and not refresh:
        log.warning(f"[daily_close] 가격 데이터 정체 감지 (data_today={today.date()}, "
                    f"expected={expected.date() if expected is not None else '?'}) "
                    f"— 캐시 무시하고 강제 재다운로드")
        price_data = fetch_all(tickers, cfg["data"]["start_date"],
                               cfg["data"]["end_date"], min_bars=150,
                               refresh=True)
        latest_dates = [df.index.max() for df in price_data.values() if not df.empty]
        if latest_dates:
            today = max(latest_dates)
        log.info(f"[daily_close] refresh 후 data_today={today.date()}")
    data_stale = is_stale(today, expected)
    if data_stale:
        log.error(f"[daily_close] 데이터 정체 (data_today={today.date()}, "
                  f"expected={expected.date() if expected is not None else '?'}) "
                  f"— 신호 판정 중단, 미체결 재매도만 KIS 종가로")

    om = OrderManager.from_config(allow_prod=True)

    # ── 1-b. 어제 낸 주문 체결 확정 (신호 생성 전에 보유 상태를 실잔고에 맞춘다) ──
    # 미체결 매도는 신호 재발생 여부와 무관하게 아래에서 재매도한다 (전략이 이미 청산 결정함).
    recon, recon_error = None, ""
    retry_sells: dict[str, list[str]] = {"clenow": [], "weinstein": []}
    if not dry_run:
        try:
            recon = reconcile_fills(KISClient.from_config(allow_prod=True))
            for strat, sym in recon["sells_unfilled"]:
                if strat in retry_sells:
                    retry_sells[strat].append(sym)
            log.info(f"[daily_close] reconcile: {recon}")
        except Exception as e:
            recon_error = str(e)
            log.error(f"[daily_close] 체결 확정 실패: {e}")

    # ── 2. Clenow 신호 ────────────────────────────────────────────────────
    # 보유 기준은 실계좌 포지션 (positions_live_*.json).
    # 페이퍼 positions_clenow.json 은 GHA(run_daily.py)가 관리하는 가상 포트폴리오라
    # 실계좌와 다르다 — 이걸 쓰면 미보유 종목 매도/보유 종목 방치가 발생 (2026-07-02 사고).
    from paper_trading.live_signals import get_clenow_signals
    from live_trading.tracker_live import load_live_positions
    # 데이터 정체면 낡은 종가로 신호도 매도 지정가도 만들지 않는다.
    # 이미 청산이 결정된 미체결 재매도만 KIS 시세(장 마감 후 = 종가)로 낸다.
    if data_stale:
        sell_px = _kis_close_frames(om.kis, retry_sells["clenow"] + retry_sells["weinstein"], today)
        cl_sigs = {"sell_ma100": [], "sell_ranked": [], "buy": []}
    else:
        sell_px = price_data
        cl_pos  = load_live_positions("clenow")
        cl_sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wed)

    # 매도 (MA100 이탈 + rank 탈락) 는 즉시 주문
    cl_ma100  = cl_sigs.get("sell_ma100", [])
    cl_ranked = cl_sigs.get("sell_ranked", [])
    cl_sell_only = {
        "sell_ma100":  cl_ma100 + [s for s in retry_sells["clenow"]
                                   if s not in cl_ma100 and s not in cl_ranked],
        "sell_ranked": cl_ranked,
        "buy":         [],
    }
    cl_sell_orders = om.place_clenow_orders(cl_sell_only, sell_px, today, dry_run)
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
    if data_stale:
        w_sigs = {"sell_ma30": [], "buy": []}
    else:
        w_pos  = load_live_positions("weinstein")
        w_sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wed)

    w_ma30 = w_sigs.get("sell_ma30", [])
    w_sell_only = {
        "sell_ma30": w_ma30 + [s for s in retry_sells["weinstein"] if s not in w_ma30],
        "buy":       [],
    }
    w_sell_orders = om.place_weinstein_orders(w_sell_only, sell_px, today, dry_run)
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

    # ── 4. Phase 4 신규 신호 → 실계좌 pending 저장 (익일 MOO) ────────────
    # 배분 0 이면 생략. 예전엔 GHA 소유 paper_trading/pending.json 을 덮어써서
    # 로컬 git 동기화가 조용히 멈췄다 (2026-10, 73커밋 정체).
    if allow_new and not data_stale and phase4_live_enabled(cfg_live):
        _update_phase4_pending(cfg, cfg_live, price_data, today)

    # ── 5. KIS 잔고 동기화 + 핵심정보 텔레그램 1건 ───────────────────────
    if not dry_run:
        kis = KISClient.from_config(allow_prod=True)
        report = sync_all(kis)
        msg = _build_core_message(report, today, cl_sell_orders, w_sell_orders,
                                  pending_payload, data_stale, now_kst)
        msg += _build_recon_message(recon, recon_error)
        msg += "\n" + guard.summary_line()
        if nav_error:
            msg += f"\n🚨 NAV 기록 실패: {nav_error}"
        if not allow_new:
            msg += f"\n🛑 <b>신규 매수 중단</b>: {reason} (청산은 계속)"
        if data_stale:
            exp = f"{expected:%m/%d}" if expected is not None else "?"
            msg = (f"⚠️ <b>가격 데이터 정체</b> — 데이터 {today:%m/%d}, 있어야 할 날 {exp}\n"
                   f"신호 판정 중단 (신규 매도·매수 없음, 미체결 재매도만 KIS 종가로). "
                   f"캐시/네트워크 점검 필요\n") + msg
        log.info("[daily_close] " + msg.replace("\n", " | "))
        _notify(msg, cfg_live)

    log.info("[daily_close] 완료")


def _build_core_message(report, today, cl_sells, w_sells, pending,
                        data_stale=False, now_kst=None) -> str:
    """daily_close 핵심정보 텔레그램. 종목별 스팸 대신 요약 1건."""
    # 헤더 날짜는 데이터 기준일. 실행일과 다를 수 있어(주말·휴장·정체) 둘 다 표기
    run = f" · 실행 {now_kst:%m/%d %H:%M} KST" if now_kst is not None else ""
    L = [f"🇺🇸 <b>US 장마감 처리</b> · 데이터 {today:%m/%d}{run}"]

    bal = report.get("balance") or {}
    cash = bal.get("cash_usd", 0.0)
    sum_eval = sum(p.get("eval_amt", 0) for p in bal.get("positions", []))
    pnl = bal.get("total_pnl_usd", 0.0)
    # cash_usd 는 KIS 주문가능금액(inquire-psamount) — 예수금·미정산 구분값이 아니다
    L.append(f"잔고: 주문가능 ${cash:,.2f} | 평가 ${sum_eval:,.2f} | 평가손익 ${pnl:+,.2f}")

    n_sells = len(cl_sells) + len(w_sells)
    if data_stale:
        L.append(f"💸 미체결 재매도 {n_sells}건 (신호 판정 중단)" if n_sells
                 else "매도: 판정 안 함 (데이터 정체)")
    elif n_sells:
        L.append(f"💸 매도 {n_sells}건 (Clenow {len(cl_sells)} / Weinstein {len(w_sells)})")
    else:
        L.append("매도: 신호 없음")

    if pending:
        parts = [f"{k} {len(v.get('symbols', []))}종목" for k, v in pending.items()]
        L.append("🔔 매수대기 (목 00:00 KST 실행): " + ", ".join(parts))

    # 전략별 추적 수 — 예전 "KIS N종목 추적" 은 미할당 보유까지 세서 추적 중인 것처럼 보였다
    by = report.get("by_strategy", {})
    parts = [f"{k.capitalize()} {n}" for k, n in by.items() if n]
    line = f"보유: KIS {report.get('kis_count', 0)}종목"
    if parts:
        line += " = " + " · ".join(parts)
    if report.get("untracked"):
        line += f" + 미할당 {len(report['untracked'])}"
    L.append(line)

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


def _kis_close_frames(kis, symbols, today) -> dict:
    """데이터 정체 시 재매도 지정가용 — KIS 현재가(장 마감 후 = 종가)를 1행 프레임으로."""
    out = {}
    for sym in symbols:
        try:
            last = float(kis.get_price(sym).get("last", 0) or 0)
        except Exception as e:
            log.warning(f"[daily_close] {sym} KIS 시세 조회 실패 — 재매도 보류: {e}")
            continue
        if last > 0:
            out[sym] = pd.DataFrame({"close": [last]}, index=[today])
    return out


def _build_recon_message(recon, error: str) -> str:
    """체결 확정 결과. 정상(전부 체결)이면 한 줄, 이상만 상세."""
    if error:
        return f"\n🚨 <b>체결 확정 실패</b> — 포지션 미갱신: {error}"
    if not recon:
        return ""
    L = []
    if recon["buys_filled"] or recon["sells_filled"]:
        L.append(f"✅ 체결 확정: 매수 {len(recon['buys_filled'])} / 매도 {len(recon['sells_filled'])}")
    if recon["sells_unfilled"]:
        items = ", ".join(f"{s}({st})" for st, s in recon["sells_unfilled"])
        L.append(f"⚠️ 매도 미체결 → 재매도(-3% LIMIT): {items}")
    if recon["buys_expired"]:
        L.append("⚠️ 매수 미체결 (추적 삭제): " + ", ".join(recon["buys_expired"]))
    if recon["skipped"]:
        L.append("ℹ️ 판정 보류: " + ", ".join(recon["skipped"]))
    if recon["price_fallback"]:
        L.append("ℹ️ 체결가 추정: " + ", ".join(recon["price_fallback"]))
    return ("\n" + "\n".join(L)) if L else ""


def _update_phase4_pending(cfg, cfg_live, price_data, today):
    """Phase 4 신호 재생성 → live_trading/pending_phase4_live.json 업데이트."""
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

    save_pending(new_pending, PHASE4_LIVE_PENDING)
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
