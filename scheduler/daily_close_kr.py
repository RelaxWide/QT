"""
scheduler/daily_close_kr.py
실행 시각: 평일 KST 16:00 (KOSPI 정규장 마감 15:30 + 30분 마진)

역할 (2026-05-29 재설계):
  KR 트랙은 **KW Super Value 분기 리밸런싱 단일 전략**이다 (Clenow KR / Weinstein KR 폐기).
  KW SV 는 분기 사이 매매가 없으므로 일반 평일엔 할 일이 없다.

  1. KW SV 리밸런싱일(5/16, 8/16, 11/16, 4/1 영업일 보정) 감지
     → 해당 분기 후보를 한 번만 생성해 live_trading/wed_buy_pending_kr.json 저장
       (wednesday_morning_buy_kr.py 가 수 09:00 KST 에 LIMIT 매수)
  2. 비리밸런싱일 → 인덱스 1종목만 확인 후 즉시 종료 (불필요한 universe fetch 제거)
  3. 핵심 정보 텔레그램 1건 발송

폐기 전략(Clenow KR / Weinstein KR)은 더 이상 계산하지 않는다 (docs/REJECTED_STRATEGIES.md 7-0, 7-A).

수동:
    python scheduler/daily_close_kr.py [--dry-run] [--force-rebalance] [--refresh]
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
setup_file_logger("daily_close_kr")

from live_trading.risk_guard import RiskGuard
from src.markets import get_profile

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

WED_BUY_PENDING_KR = Path("live_trading/wed_buy_pending_kr.json")
KW_STATE_PATH      = Path("paper_trading/positions_kw_super_value_kr.json")
MARKET             = "kr"
TRIGGER_WINDOW_DAYS = 7   # 리밸런싱일 이후 며칠 이내까지 후보 생성 허용


def _notify(msg: str, cfg_live: dict):
    try:
        token   = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"[daily_close_kr] 텔레그램 실패: {e}")


def _save_pending_kw(symbols: list[str], signal_date: str):
    """기존 pending(다른 전략) 보존하며 kw_super_value 섹션만 갱신."""
    WED_BUY_PENDING_KR.parent.mkdir(exist_ok=True)
    existing = {}
    if WED_BUY_PENDING_KR.exists():
        try:
            existing = json.loads(WED_BUY_PENDING_KR.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing["kw_super_value"] = {"signal_date": signal_date, "symbols": symbols}
    WED_BUY_PENDING_KR.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def kw_super_value_rebalance(cfg, cfg_live, dry_run=False, force=False, refresh=False):
    """
    분기 리밸런싱일이면 KW SV 후보를 생성해 pending 저장.
    반환: 핵심정보 dict (텔레그램용) 또는 None (리밸런싱일 아님).

    설계상 run_kw_immediate.py 와 동일한 라이브러리 호출 시퀀스를 사용한다.
    비리밸런싱일에는 인덱스 1종목만 fetch 하고 즉시 None 반환 (universe fetch 회피).
    """
    from src.fetch.prices import fetch_all
    from src.strategy._kw_common import (
        rebalance_dates_kr_quarterly, adjust_signals_to_trading,
    )

    profile = get_profile(MARKET)
    params  = dict(cfg["kw_super_value"])
    top_n   = params.get("top_n", 18)
    small_cap_pct = params.get("small_cap_pct", 0.15)

    today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()

    # 1) 올해 분기 리밸런싱일 (raw)
    raw_rebal = rebalance_dates_kr_quarterly(
        f"{today.year}-01-01", today.strftime("%Y-%m-%d"),
        params["rebalance_months"], params["rebalance_dom"],
    )
    if not raw_rebal:
        log.info("[kw_sv] 올해 리밸런싱일 없음 — 스킵")
        return None
    latest_raw = raw_rebal[-1]

    # 2) 영업일 보정용 캘린더 = 인덱스(^KS11) 한 종목만 가볍게 fetch
    idx_data = fetch_all([profile.index_ticker], cfg["data"]["start_date"],
                         cfg["data"]["end_date"], min_bars=60,
                         market=MARKET, refresh=refresh)
    idx_df = idx_data.get(profile.index_ticker)
    if idx_df is None or idx_df.empty:
        log.error("[kw_sv] 인덱스 데이터 로드 실패 — 스킵")
        return None
    calendar = idx_df.index
    actual_rebal = adjust_signals_to_trading([latest_raw], calendar)[0]

    # 3) 트리거 판정 (idempotent: 이미 처리한 분기면 스킵)
    done_rebal = None
    if KW_STATE_PATH.exists():
        try:
            done_rebal = json.loads(KW_STATE_PATH.read_text(encoding="utf-8")).get("rebal_date")
        except Exception:
            pass

    days_since = (today - actual_rebal).days
    if not force:
        if actual_rebal > today:
            log.info(f"[kw_sv] 다음 리밸런싱 {actual_rebal.date()} 미래 — 스킵 "
                     f"(다음 분기 대기)")
            return None
        if days_since > TRIGGER_WINDOW_DAYS:
            log.info(f"[kw_sv] 최근 리밸런싱 {actual_rebal.date()} "
                     f"({days_since}일 경과) 처리 윈도우({TRIGGER_WINDOW_DAYS}일) 밖 — 스킵")
            return None
        if done_rebal == str(actual_rebal.date()):
            log.info(f"[kw_sv] {actual_rebal.date()} 이미 처리됨 — 스킵 (재생성 안 함)")
            return None

    log.info(f"[kw_sv] 리밸런싱 트리거: {actual_rebal.date()} "
             f"(today={today.date()}, {days_since}일 경과)")

    # 4) 전체 universe 가격 + 펀더멘털 (여기서부터 무거운 작업 — 리밸런싱일만)
    from src.fetch.universe import get_kospi_all_tickers
    from src.fetch.fundamentals_kr import build_fundamentals_panel
    from src.strategy.kw_super_value import generate_super_value_signals
    from src.markets.tick_size import round_buy_to_tick

    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    start_dt = (actual_rebal - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    end_dt   = today.strftime("%Y-%m-%d")
    price_data = fetch_all(tickers, start_dt, end_dt, min_bars=60,
                           market=MARKET, refresh=refresh)
    log.info(f"[kw_sv] {len(price_data)} 종목 가격 로드")

    panel = build_fundamentals_panel([actual_rebal])
    start_window = (actual_rebal - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    end_window   = (actual_rebal + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    sigs = generate_super_value_signals(panel, price_data, params,
                                        start_window, end_window)
    sigs = [s for s in sigs if s.date <= today]
    if not sigs:
        log.error("[kw_sv] 신호 생성 실패 — panel 에 리밸런싱일 데이터 없음")
        return {"rebal_date": str(actual_rebal.date()), "error": "신호 생성 실패"}
    sig = sigs[-1]
    log.info(f"[kw_sv] 신호일 {sig.date.date()}, 진입 {len(sig.weights)}종목 "
             f"(universe {sig.universe_size})")

    # 5) 예산 + 종목당 매수 수량
    cap = (cfg_live.get("kr") or {}).get("capital", {})
    capital   = int(cap.get("kw_super_value_krw") or cap.get("total_krw") or 0)
    buffer_pct = float(cap.get("buffer_pct", 1.0))
    if capital <= 0:
        log.error("[kw_sv] kr.capital.kw_super_value_krw 미설정 — 후보만 산출, 매수수량 0")
    budget   = capital * (1 - buffer_pct / 100)
    per_stock = budget / top_n if top_n else 0

    orders = []
    total_spent = 0
    skipped_halt = []
    for ticker in sorted(sig.weights.keys()):
        px_df = price_data.get(ticker)
        if px_df is None or px_df.empty:
            continue
        # 거래정지/관리종목 가드 — 시세가 오래됐거나 최근 거래량이 없으면 매수 제외
        # (2026-05 분기 008500 거래정지 편입 사고 재발 방지)
        last_bar = px_df.index.max()
        if (today - last_bar).days > 7:
            log.warning(f"[kw_sv] {ticker} 최근 시세 없음 (last={last_bar.date()}) — 거래정지 의심, 제외")
            skipped_halt.append(ticker)
            continue
        if "volume" in px_df.columns and float(px_df["volume"].tail(5).sum()) <= 0:
            log.warning(f"[kw_sv] {ticker} 최근 5일 거래량 0 — 거래정지 의심, 제외")
            skipped_halt.append(ticker)
            continue
        last_close = float(px_df.iloc[-1]["close"])
        if last_close <= 0:
            continue
        order_px = int(round_buy_to_tick(last_close, "kr"))
        qty = int(per_stock // order_px) if per_stock > 0 else 0
        if qty < 1:
            continue
        spent = qty * order_px
        total_spent += spent
        orders.append({
            "ticker": ticker, "name": "", "price": order_px,
            "qty": qty, "value": spent, "score": sig.scores.get(ticker, 0),
        })
    orders.sort(key=lambda x: x["score"])

    # 6) 상태 파일 + pending 저장 (dry_run 이면 저장 안 함)
    symbols = [o["ticker"] for o in orders]
    if not dry_run:
        KW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        KW_STATE_PATH.write_text(json.dumps({
            "strategy": "kw_super_value",
            "rebal_date": str(actual_rebal.date()),
            "capital": capital, "spent": total_spent, "orders": orders,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        if symbols:
            _save_pending_kw(symbols, str(actual_rebal.date()))
            log.info(f"[kw_sv] pending 저장 {len(symbols)}종목 → 수 09:00 KST 자동 매수")
    else:
        log.info(f"[kw_sv] [DRY-RUN] 저장 생략 ({len(symbols)}종목 후보)")

    return {
        "rebal_date":  str(actual_rebal.date()),
        "n_candidates": len(orders),
        "symbols":     symbols,
        "total_spent": total_spent,
        "capital":     capital,
        "universe":    sig.universe_size,
        "small_cap_pct": small_cap_pct,
        "top_n":       top_n,
        "skipped_halt": skipped_halt,
    }


def _build_core_message(result, today_str) -> str:
    L = [f"🇰🇷 <b>{today_str} KR 장마감 (KW Super Value)</b>"]
    if result is None:
        L.append("리밸런싱일 아님 — 분기 사이 매매 없음 (정상)")
        return "\n".join(L)
    if result.get("error"):
        L.append(f"⚠️ 리밸런싱 {result['rebal_date']} 처리 실패: {result['error']}")
        return "\n".join(L)
    L.append(f"📦 분기 리밸런싱 {result['rebal_date']} — 후보 {result['n_candidates']}종목")
    L.append(f"파라미터: 시총하위 {result['small_cap_pct']:.0%} / top {result['top_n']} "
             f"(universe {result['universe']})")
    L.append(f"예산 ₩{result['capital']:,} → 매수 ₩{result['total_spent']:,} "
             f"(잔여 ₩{result['capital'] - result['total_spent']:,})")
    if result["symbols"]:
        L.append("종목: " + ", ".join(result["symbols"]))
        L.append("→ 다음 수 09:00 KST QT_KR_WedMorningBuy 자동 매수")
        L.append("⚠️ 직전 분기 보유 중 top-18 탈락분은 수동 매도 검토 필요 (KR 자동매도 미구현)")
    if result.get("skipped_halt"):
        L.append("🚫 거래정지 의심 제외: " + ", ".join(result["skipped_halt"]))
    return "\n".join(L)


def main(dry_run: bool = False, force_rebalance: bool = False, refresh: bool = False):
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"[daily_close_kr] 리스크 가드: {reason} — 후보 생성은 진행, 매수는 wed_buy 에서 차단됨")

    today_str = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")

    result = None
    try:
        result = kw_super_value_rebalance(cfg, cfg_live, dry_run=dry_run,
                                          force=force_rebalance, refresh=refresh)
    except Exception as e:
        log.exception(f"[daily_close_kr] KW SV 리밸런싱 처리 오류: {e}")
        _notify(f"🇰🇷 ⚠️ KR daily_close KW SV 처리 오류: {e}", cfg_live)
        log.info("[daily_close_kr] 완료 (오류)")
        return

    # 핵심 정보 텔레그램 — 리밸런싱일에만 발송 (평일 스팸 방지)
    msg = _build_core_message(result, today_str)
    log.info("[daily_close_kr] " + msg.replace("\n", " | "))
    if result is not None:
        if dry_run:
            log.info("[daily_close_kr] [DRY-RUN] 텔레그램 발송 생략")
        else:
            _notify(msg, cfg_live)

    log.info("[daily_close_kr] 완료")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run",          action="store_true",
                   help="후보만 산출, pending/상태 저장 안 함")
    p.add_argument("--force-rebalance",  action="store_true",
                   help="요일·윈도우 무관 KW SV 후보 강제 생성 (테스트용)")
    p.add_argument("--refresh",          action="store_true",
                   help="가격 캐시 강제 갱신")
    args = p.parse_args()
    main(dry_run=args.dry_run, force_rebalance=args.force_rebalance, refresh=args.refresh)
