"""
scheduler/daily_close.py
실행 시각: 평일 06:30 KR (미국장 마감 후 — 서머타임 1.5h / 표준시 0.5h)
          월요일 실행분이 금요일 세션을 처리한다 (토요일엔 실행 안 함)
역할:
  1. Clenow / Weinstein 매도 신호 (MA 이탈, rank_exit) → 즉시 주문
  2. KR 수요일: 매수 후보를 live_trading/wed_buy_pending.json 으로 저장
     (wednesday_morning_buy.py 가 Wed 11 AM ET 에 KIS 주문 전송 — Clenow 후 남는 현금으로 코어 SPY 보충)
     Clenow: SPY > MA200 일 때만 매수 후보, 업종당 종목 수 제한, 변동성 역가중 배율 포함
  3. 종료 전략(config_live capital.liquidate_strategies) 보유분 전량 매도
  4. KIS 잔고 동기화 + 텔레그램 1건

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
from live_trading.account import sync_all, reconcile_fills
from live_trading.risk_guard import (RiskGuard, record_nav, add_unsettled,
                                     load_spy_closes, update_benchmark, benchmark_line)
from live_trading.freshness import expected_us_session, is_stale, session_after
from live_trading.tracker_live import load_live_positions

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

WED_BUY_PENDING = Path("live_trading/wed_buy_pending.json")
MIN_TODAY_COVERAGE = 0.90   # 요청 종목 중 data_today 봉이 있어야 하는 비율 (미만이면 불완전 데이터 → 신호 판정 중단)
# 끝까지 정상 처리된 실행만 남기는 완료 기록 — catchup.py 가 "오늘 처리됐는가" 를 이걸로 판단한다.
# NAV 는 실행 초반에 기록되므로 완료 여부의 근거가 못 된다 (이후 단계에서 죽어도 NAV 는 남는다).
DONE_MARKER = Path("live_trading/daily_close_done.json")


def main(dry_run: bool = False, refresh: bool = False, force_wed: bool = False):
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    # ── 0. 어제 낸 주문 체결 확정 (신호 생성 전에 보유 상태를 실잔고에 맞춘다) ──
    # NAV 기록보다 먼저 한다: 체결된 매도 대금을 미결제로 등록해야 매도 다음 날 가짜 낙폭이 안 생긴다.
    # 미체결 매도는 신호 재발생 여부와 무관하게 아래에서 재매도한다 (전략이 이미 청산 결정함).
    session = _current_session()
    recon, recon_error = None, ""
    retry_sells: dict[str, list[str]] = {"clenow": [], "weinstein": []}
    if not dry_run:
        try:
            recon = reconcile_fills(KISClient.from_config(allow_prod=True), fallback_session=session)
            for strat, sym in recon["sells_unfilled"]:
                if strat in retry_sells:
                    retry_sells[strat].append(sym)
            log.info(f"[daily_close] reconcile: {recon}")
            # 미결제 매도대금 등록은 reconcile_fills 안에서 포지션 저장 전에 끝난다
        except Exception as e:
            recon_error = str(e)
            log.error(f"[daily_close] 체결 확정 실패: {e}")

    # 실계좌 NAV 를 먼저 기록해야 가드가 오늘 기준으로 판단한다
    nav_error, nav_row = "", None
    if not dry_run:
        try:
            nav_row = record_nav("us", KISClient.from_config(allow_prod=True).get_balance(),
                                 session=session)
            if nav_row is None:
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

    # KR 시간 기준 요일 (Wed 06:30 KR = Tue close 처리 → 그날 11 AM ET 매수 준비)
    now_kst = pd.Timestamp.now(tz="Asia/Seoul")
    is_wed  = now_kst.weekday() == 2 or force_wed   # --force-wed: 수요일 매수 후보 미리보기 (dry-run 용)

    # ── 1. 가격 데이터 로드 ──────────────────────────────────────────────
    from src.fetch.universe import get_sp500_tickers
    from src.fetch.prices import fetch_all
    tickers    = get_sp500_tickers()
    # 추적 중인 보유 종목은 유니버스(현재 S&P 500 구성)에서 빠졌어도 가격을 받는다.
    # 안 그러면 지수에서 제외된 보유 종목은 가격이 없어 청산 주문이 조용히 건너뛰어진다.
    held_all = sorted({s for strat in ("clenow", "weinstein") for s in load_live_positions(strat)})
    extra = [s for s in held_all if s not in set(tickers)]
    if extra:
        log.warning(f"[daily_close] 유니버스 밖 보유 종목 가격 추가 수집: {extra}")
        tickers = tickers + extra
    price_data = fetch_all(tickers, cfg["data"]["start_date"],
                           cfg["data"]["end_date"], min_bars=150,
                           refresh=refresh)
    log.info(f"[daily_close] {len(price_data)} / {len(tickers)} 종목 로드 완료")

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
    # 날짜 하나(max)만 보면 1종목만 최신이어도 전체가 최신으로 통과한다 → 요청 종목 대비
    # "data_today 봉이 있는 종목 비율" 도 본다 (페이퍼와 같은 90% 기준).
    def _coverage(pdata, day) -> float:
        return sum(1 for df in pdata.values() if not df.empty and df.index.max() == day) / max(len(tickers), 1)

    expected = expected_us_session()
    coverage = _coverage(price_data, today)
    if (is_stale(today, expected) or coverage < MIN_TODAY_COVERAGE) and not refresh:
        log.warning(f"[daily_close] 가격 데이터 정체 감지 (data_today={today.date()}, "
                    f"expected={expected.date() if expected is not None else '?'}) "
                    f"— 캐시 무시하고 강제 재다운로드")
        price_data = fetch_all(tickers, cfg["data"]["start_date"],
                               cfg["data"]["end_date"], min_bars=150,
                               refresh=True)
        latest_dates = [df.index.max() for df in price_data.values() if not df.empty]
        if latest_dates:
            today = max(latest_dates)
        coverage = _coverage(price_data, today)
        log.info(f"[daily_close] refresh 후 data_today={today.date()}")
    data_stale = is_stale(today, expected) or coverage < MIN_TODAY_COVERAGE
    log.info(f"[daily_close] 당일({today.date()}) 봉 커버리지 {coverage:.0%} (기준 {MIN_TODAY_COVERAGE:.0%})")
    if data_stale:
        log.error(f"[daily_close] 데이터 정체/불완전 (data_today={today.date()}, "
                  f"expected={expected.date() if expected is not None else '?'}, 커버리지 {coverage:.0%}) "
                  f"— 신호 판정 중단, 미체결 재매도만 KIS 종가로")

    # SPY 벤치마크 — NAV 이력에 같은 세션의 SPY 종가를 붙인다 (판정 기준: SPY 보유 대비).
    # 실패해도 매매에는 영향 없음. SPY 가 밀려 있으면 오늘 행은 비워 두고 다음 실행에서 채운다.
    spy_closes = None
    try:
        spy_closes = load_spy_closes(cfg)
    except Exception as e:
        log.warning(f"[daily_close] SPY 가격 조회 실패: {e}")
    if not dry_run and spy_closes is not None:
        try:
            n = update_benchmark("us", spy_closes, session)
            log.info(f"[daily_close] SPY 벤치마크 {n}행 반영")
        except Exception as e:
            log.warning(f"[daily_close] SPY 벤치마크 갱신 실패: {e}")

    # Clenow 레짐 — SPY 종가 > MA200 일 때만 신규 매수 (원래 설계). 판정 불가(None)면 매수 보류.
    from src.strategy.clenow_momentum import spy_regime_ok
    regime = None
    if spy_closes is not None:
        regime = spy_regime_ok(spy_closes, today, cfg.get("clenow_strategy", {}).get("spy_ma200_period", 200))
    log.info(f"[daily_close] Clenow 레짐: {'ON' if regime else 'OFF' if regime is False else '판정 불가'}")

    om = OrderManager.from_config(allow_prod=True)

    # 종료된 전략 (config_live capital.liquidate_strategies) — 신호와 무관하게 보유분 전량 매도, 매수 없음.
    # 명시적 목록으로만 판단한다 (배분 0 을 기준으로 하면 잔고 조회 실패가 전량 청산으로 이어질 수 있다)
    liquidate = set(om.cap.get("liquidate_strategies") or [])
    liq_syms: dict[str, list[str]] = {}
    for strat in liquidate:
        liq_syms[strat] = [s for s, p in load_live_positions(strat).items() if p.status == "open"]
    if liquidate:
        log.info(f"[daily_close] 전략 종료 청산 대상: {liq_syms}")

    # ── 2. Clenow 신호 ────────────────────────────────────────────────────
    # 보유 기준은 실계좌 포지션 (positions_live_*.json).
    # 페이퍼 positions_clenow.json 은 GHA(run_daily.py)가 관리하는 가상 포트폴리오라
    # 실계좌와 다르다 — 이걸 쓰면 미보유 종목 매도/보유 종목 방치가 발생 (2026-07-02 사고).
    from paper_trading.live_signals import get_clenow_signals
    # 데이터 정체면 낡은 종가로 신호도 매도 지정가도 만들지 않는다.
    # 이미 청산이 결정된 미체결 재매도만 KIS 시세(장 마감 후 = 종가)로 낸다.
    if data_stale:
        sell_px = _kis_close_frames(
            om.kis,
            retry_sells["clenow"] + retry_sells["weinstein"] + [s for v in liq_syms.values() for s in v],
            today)
        cl_sigs = {"sell_ma100": [], "sell_ranked": [], "buy": []}
    else:
        sell_px = price_data
        cl_pos  = load_live_positions("clenow")
        sectors = {}
        if is_wed:
            from src.fetch.universe import get_sp500_sectors
            sectors = get_sp500_sectors()
        cl_sigs = get_clenow_signals(price_data, set(cl_pos.keys()), cfg, today, is_wed,
                                     regime_ok=(regime is True), sectors=sectors,
                                     max_per_sector=int(om.cap.get("clenow_max_per_sector", 2)))

    # 매도 (MA100 이탈 + rank 탈락) 는 즉시 주문
    cl_ma100  = cl_sigs.get("sell_ma100", [])
    cl_ranked = cl_sigs.get("sell_ranked", [])
    cl_sell_only = {
        "sell_ma100":  cl_ma100 + [s for s in retry_sells["clenow"] + liq_syms.get("clenow", [])
                                   if s not in cl_ma100 and s not in cl_ranked],
        "sell_ranked": cl_ranked,
        "buy":         [],
    }
    sell_px = dict(sell_px)          # 아래에서 KIS 시세를 덧붙이므로 사본
    sell_no_price: list[str] = []    # 가격을 끝내 못 구해 매도 주문을 못 낸 종목

    def _ensure_sell_prices(symbols) -> None:
        """매도 대상 중 data_today 봉이 없는 종목은 KIS 현재가(장 마감 후 = 종가)로 채운다."""
        def _lacks(s):
            return s not in sell_px or today not in sell_px[s].index
        missing = [s for s in symbols if _lacks(s)]
        if missing:
            log.warning(f"[daily_close] 매도 대상 가격 없음 → KIS 시세 조회: {missing}")
            sell_px.update(_kis_close_frames(om.kis, missing, today))
            sell_no_price.extend(s for s in missing if _lacks(s))

    _ensure_sell_prices(cl_sell_only["sell_ma100"] + cl_sell_only["sell_ranked"])
    cl_sell_orders = om.place_clenow_orders(cl_sell_only, sell_px, today, dry_run)
    log.info(f"[daily_close] Clenow 매도 주문 {len(cl_sell_orders)}건")

    # 매수 후보는 pending 저장 (수요일 + allow_new 인 경우만)
    pending_payload = {}
    if is_wed and allow_new and cl_sigs.get("buy") and "clenow" not in liquidate:
        pending_payload["clenow"] = {
            "signal_date": str(today.date()),
            "symbols":     cl_sigs["buy"],
            "size_mult":   cl_sigs.get("size_mult", {}),    # 변동성 역가중 배율
            # 매수 시점에 업종 한도를 다시 확인하기 위한 정보 (후보 + 현재 보유).
            # 신호 단계는 "매도가 체결된다" 고 가정하고 업종을 세므로, 미체결이면 한도를 넘길 수 있다.
            "sectors":        {s: sectors[s] for s in list(cl_sigs["buy"]) + list(cl_pos.keys()) if s in sectors},
            "max_per_sector": int(om.cap.get("clenow_max_per_sector", 2)),
        }
        log.info(f"[daily_close] Clenow 매수 후보 {len(cl_sigs['buy'])}건 → pending 저장")

    # ── 3. Weinstein 신호 ────────────────────────────────────────────────
    from paper_trading.live_signals import get_weinstein_signals
    if data_stale or "weinstein" in liquidate:
        w_sigs = {"sell_ma30": [], "buy": []}
    else:
        w_pos  = load_live_positions("weinstein")
        w_sigs = get_weinstein_signals(price_data, set(w_pos.keys()), cfg, today, is_wed)

    w_ma30 = w_sigs.get("sell_ma30", [])
    w_sell_only = {
        "sell_ma30": list(dict.fromkeys(w_ma30 + retry_sells["weinstein"] + liq_syms.get("weinstein", []))),
        "buy":       [],
    }
    _ensure_sell_prices(w_sell_only["sell_ma30"])
    w_sell_orders = om.place_weinstein_orders(w_sell_only, sell_px, today, dry_run)

    # 당일 봉이 없는 보유 종목은 추세 이탈 청산을 판정하지 못했다 (정체가 아닐 때만 의미 있음)
    # (이번에 매도 주문이 나간 종목은 제외. 한 종목의 데이터 누락은 재실행으로 풀리는 경우가 드물어
    #  완료 판정에는 넣지 않고 경고만 한다 — 전체적인 지연은 위 커버리지 기준이 잡는다)
    ordered = {r.symbol for r in cl_sell_orders + w_sell_orders if r.ok}
    held_no_bar = [] if data_stale else sorted(
        s for s in held_all if s not in ordered
        and (s not in price_data or today not in price_data[s].index))
    if held_no_bar:
        log.warning(f"[daily_close] 당일 봉 없는 보유 종목 — 청산 판정 못 함: {held_no_bar}")

    # 이 실행이 "끝까지 정상" 이었는지 — 아니면 완료 기록을 남기지 않아 catchup 이 다시 돌린다
    problems = []
    if recon_error:
        problems.append("체결 확정 실패")
    if nav_error:
        problems.append("NAV 기록 실패")
    failed_sells = [r.symbol for r in cl_sell_orders + w_sell_orders if not r.ok]
    if failed_sells:
        problems.append("매도 주문 실패 " + ",".join(failed_sells))
    if sell_no_price:
        problems.append("매도 가격 없음 " + ",".join(sell_no_price))
    if data_stale:
        problems.append("데이터 정체/불완전")
    log.info(f"[daily_close] Weinstein 매도 주문 {len(w_sell_orders)}건")

    if is_wed and allow_new and w_sigs.get("buy"):
        pending_payload["weinstein"] = {
            "signal_date": str(today.date()),
            "symbols":     w_sigs["buy"],
        }
        log.info(f"[daily_close] Weinstein 매수 후보 {len(w_sigs['buy'])}건 → pending 저장")

    # pending 파일 저장 / 정리 (dry-run 은 파일을 건드리지 않고 후보만 보여 준다)
    if dry_run:
        view = _pending_view(pending_payload, om)
        log.info(f"[DRY-RUN] 매수 후보(파일 미저장): { {k: v['buy'] for k, v in view.items()} } "
                 f"| 배율 {pending_payload.get('clenow', {}).get('size_mult', {})}")
        core = om.core_status()
        if core:
            log.info(f"[DRY-RUN] 코어 {core['symbol']}: 보유 ${core['held']:,.0f} ({core['held_pct']:.0f}%) / "
                     f"목표 {core['target_pct']:.0f}% / 차이 ${core['gap']:,.0f} / 현금 ${core['cash']:,.0f}")
    elif pending_payload:
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

    # ── 4. KIS 잔고 동기화 + 핵심정보 텔레그램 1건 ───────────────────────
    if not dry_run:
        kis = KISClient.from_config(allow_prod=True)
        report = sync_all(kis)
        msg = _build_core_message(report, today, cl_sell_orders, w_sell_orders,
                                  _pending_view(pending_payload, om), data_stale, now_kst,
                                  unsettled=(nav_row or {}).get("unsettled", 0))
        msg += _build_recon_message(recon, recon_error)
        if is_wed and regime is not True and "clenow" not in liquidate:
            msg += ("\n⏸️ Clenow 매수 보류: " +
                    ("SPY 가 200일선 아래 (레짐 OFF)" if regime is False else "SPY 조회 실패로 레짐 판정 불가"))
        core = om.core_status()
        if core:
            line = (f"\n🧱 코어 {core['symbol']}: ${core['held']:,.0f} "
                    f"({core['held_pct']:.0f}% / 목표 {core['target_pct']:.0f}%)")
            buyable = om.core_spend(core)      # 목표까지 남은 금액·현금·1회 한도 중 작은 값
            if is_wed and allow_new and core["gap"] >= core["total"] * om.CORE_MIN_GAP_PCT / 100 and buyable > 0:
                line += f" → 매수 예정 약 ${buyable:,.0f}"
            msg += line
        for strat in sorted(liquidate):
            left = sorted(load_live_positions(strat).keys())
            if left:
                msg += f"\n🧹 <b>{strat.capitalize()} 종료 청산</b>: 잔여 {', '.join(left)} (매도 주문 중)"
        msg += "\n" + guard.summary_line()
        bench = benchmark_line("us")
        if bench:
            msg += "\n" + bench
        if nav_error:
            msg += f"\n🚨 NAV 기록 실패: {nav_error}"
        if (report.get("balance") or {}).get("_cash_failed"):
            msg += "\n🚨 현금(주문가능금액) 조회 실패 — 위 잔고의 현금 $0 은 실제 값이 아님. NAV 기록·매수 보류"
        if sell_no_price:
            msg += "\n🚨 매도 가격을 못 구해 주문 못 냄: " + ", ".join(sell_no_price)
        for strat, sym, local_qty, kis_qty in getattr(om, "qty_mismatch", []):
            msg += (f"\n🚨 <b>수량 불일치로 매도 보류</b>: {sym} 장부 {local_qty}주 / KIS {kis_qty}주 ({strat}) — "
                    f"분할·수동 거래 확인 후 live_trading/positions_live_{strat}.json 수량을 맞추면 다음 실행에서 나감")
        if getattr(om, "unknown_orders", []):
            msg += ("\n🚨 <b>접수 불명 주문</b> (응답 유실): "
                    + ", ".join(f"{r.side} {r.symbol} x{r.qty}" for r in om.unknown_orders)
                    + " — 재전송 안 함. KIS 앱에서 접수 여부 확인, 체결은 다음 장마감 처리에서 잔고로 확정")
        if held_no_bar:
            msg += "\n⚠️ 당일 봉 없는 보유 종목 (청산 판정 못 함): " + ", ".join(held_no_bar)
        if problems:
            msg += "\n🔁 미완료 처리 — 따라잡기 실행이 다시 시도: " + " / ".join(problems)
        if not allow_new:
            msg += f"\n🛑 <b>신규 매수 중단</b>: {reason} (청산은 계속)"
        if data_stale:
            exp = f"{expected:%m/%d}" if expected is not None else "?"
            msg = (f"⚠️ <b>가격 데이터 정체/불완전</b> — 데이터 {today:%m/%d} (당일 봉 {coverage:.0%}), 있어야 할 날 {exp}\n"
                   f"신호 판정 중단 (신규 매도·매수 없음, 미체결 재매도만 KIS 종가로). "
                   f"캐시/네트워크 점검 필요\n") + msg
        log.info("[daily_close] " + msg.replace("\n", " | "))
        _notify(msg, cfg_live)

    # 완료 기록: 실제 실행(dry-run 아님)이 끝까지 왔고 **필수 단계가 전부 성공**했을 때만.
    # 체결 확정·NAV 기록·매도 주문 중 하나라도 실패했거나 데이터가 정체/불완전하면 미완료로 남겨
    # catchup 이 나중에 다시 시도하게 한다. (텔레그램 전송 성공 여부는 조건이 아니다)
    if not dry_run and not problems:
        try:
            DONE_MARKER.parent.mkdir(exist_ok=True)
            DONE_MARKER.write_text(json.dumps({
                "date": now_kst.strftime("%Y-%m-%d"), "data_today": str(today.date()),
                "finished_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
            }, indent=2), encoding="utf-8")
        except Exception as e:
            log.error(f"[daily_close] 완료 기록 실패: {e}")
    elif problems:
        log.warning(f"[daily_close] 미완료 — 완료 기록을 남기지 않음 (catchup 재시도 대상): {problems}")

    log.info("[daily_close] 완료")


def _current_session() -> str:
    """방금 마감한 NYSE 세션 날짜. 달력 실패 시 뉴욕 날짜를 평일로 되돌린 근사."""
    s = expected_us_session()
    if s is None:
        s = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
        while s.weekday() >= 5:
            s -= pd.Timedelta(days=1)
    return str(s.date())


def _pending_view(pending: dict, om) -> dict:
    """매수 후보 → 실제로 주문될 종목. wednesday_morning_buy 와 같은 규칙
    (기보유 제외, 빈자리 = 한도 − 보유(매도 대기 포함), 순위순으로 빈자리만큼)."""
    from live_trading.tracker_live import load_live_positions
    view = {}
    for k, v in pending.items():
        syms = v.get("symbols", [])
        held = set(load_live_positions(k).keys())
        cands = [s for s in syms if s not in held]
        slots = max(0, om._strategy_max_positions(k) - len(held))
        view[k] = {"n_cand": len(syms), "slots": slots, "buy": cands[:slots]}
    return view


_KR_DOW = "월화수목금토일"


def _buy_time_label(now_kst=None) -> str:
    """다음 수요일 11:00 ET 를 KST 로 (서머타임 목 00:00 / 표준시 목 01:00)."""
    try:
        now = pd.Timestamp.now(tz="Asia/Seoul") if now_kst is None else now_kst
        d = pd.Timestamp(now.tz_convert("America/New_York").date())
        while d.weekday() != 2:
            d += pd.Timedelta(days=1)
        t = (d + pd.Timedelta(hours=11)).tz_localize("America/New_York").tz_convert("Asia/Seoul")
        return f"수 11:00 ET = {_KR_DOW[t.weekday()]} {t:%H:%M} KST"
    except Exception:
        return "수 11:00 ET"


def _syms(orders) -> str:
    return ", ".join(r.symbol + ("(접수 불명)" if getattr(r, "unknown", False) else "" if r.ok else "(실패)")
                     for r in orders) or "-"


def _build_core_message(report, today, cl_sells, w_sells, pending,
                        data_stale=False, now_kst=None, unsettled=0.0) -> str:
    """daily_close 핵심정보 텔레그램. 종목별 스팸 대신 요약 1건."""
    # 헤더 날짜는 데이터 기준일. 실행일과 다를 수 있어(주말·휴장·정체) 둘 다 표기
    run = f" · 실행 {now_kst:%m/%d %H:%M} KST" if now_kst is not None else ""
    L = [f"🇺🇸 <b>US 장마감 처리</b> · 데이터 {today:%m/%d}{run}"]

    bal = report.get("balance") or {}
    cash = bal.get("cash_usd", 0.0)
    sum_eval = sum(p.get("eval_amt", 0) for p in bal.get("positions", []))
    pnl = bal.get("total_pnl_usd", 0.0)
    # cash_usd 는 KIS 주문가능금액(inquire-psamount) — 결제 전 매도 대금은 빠져 있다
    line = f"잔고: 주문가능 ${cash:,.2f} | 평가 ${sum_eval:,.2f} | 평가손익 ${pnl:+,.2f}"
    if unsettled:
        line += f" | 결제대기 매도대금 ${unsettled:,.2f}"
    L.append(line)

    n_sells = len(cl_sells) + len(w_sells)
    if data_stale:
        L.append(f"💸 미체결 재매도 {n_sells}건 (신호 판정 중단): {_syms(cl_sells + w_sells)}"
                 if n_sells else "매도: 판정 안 함 (데이터 정체)")
    elif n_sells:
        L.append(f"💸 매도 주문 {n_sells}건 — Clenow: {_syms(cl_sells)} / Weinstein: {_syms(w_sells)}")
    else:
        L.append("매도: 신호 없음")

    if pending:
        parts = []
        for k, v in pending.items():
            buy = ", ".join(v["buy"]) if v["buy"] else "매수 없음"
            parts.append(f"{k.capitalize()} {buy} (후보 {v['n_cand']} 중 빈자리 {v['slots']})")
        L.append(f"🔔 매수 예정 ({_buy_time_label(now_kst)}): " + " / ".join(parts))

    # 전략별 추적 수 — 예전 "KIS N종목 추적" 은 미할당 보유까지 세서 추적 중인 것처럼 보였다
    by = report.get("by_strategy", {})
    parts = [f"{k.capitalize()} {n}" for k, n in by.items() if n]
    line = f"보유: KIS {report.get('kis_count', 0)}종목"
    if parts:
        line += " = " + " · ".join(parts)
    if report.get("core"):
        line += " + 코어 " + "·".join(report["core"])
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
        sym = lambda tags: ", ".join(t.split("/", 1)[-1] for t in tags) or "-"
        L.append(f"✅ 체결 확정: 매수 {sym(recon['buys_filled'])} / 매도 {sym(recon['sells_filled'])}")
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
    p.add_argument("--force-wed", action="store_true",
                   help="수요일이 아니어도 매수 후보를 계산 (--dry-run 과 함께 미리보기용)")
    args = p.parse_args()
    if args.force_wed and not args.dry_run:
        p.error("--force-wed 는 --dry-run 과 함께만 쓸 수 있습니다 (실제 매수 후보 저장 방지)")
    if args.dry_run:
        main(dry_run=True, refresh=args.refresh, force_wed=args.force_wed)
    else:
        # 다른 주문 실행(따라잡기·수요일 매수)과 겹치지 않게 계좌 잠금을 잡는다. 못 잡으면 완료 기록
        # 없이 끝나므로 다음 따라잡기 실행이 다시 시도한다.
        from live_trading.runlock import account_lock, LockBusy
        try:
            with account_lock("us", wait_sec=900):
                main(dry_run=False, refresh=args.refresh, force_wed=args.force_wed)
        except LockBusy as e:
            log.error(f"[daily_close] 실행 생략: {e}")
            try:
                _notify(f"🚨 <b>US 장마감 처리 실행 못 함</b>\n{e}\n다음 따라잡기 실행이 다시 시도",
                        yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8")))
            except Exception:
                pass
            sys.exit(1)
