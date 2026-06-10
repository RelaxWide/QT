"""
KW Super Value 즉시 신호 산출 + 매수 안내.

가장 최근 분기 리밸런싱일 (5/16, 8/16, 11/16, 4/1) 기준 KW SV 신호 생성.
주어진 시드 자본으로 종목당 매수 수량 + 호가단위 라운딩 + 잔여 현금 계산.

사용:
    python run_kw_immediate.py --capital 2000000             # 출력만 (수동 매수용)
    python run_kw_immediate.py --capital 2000000 --execute   # 자동 매수 (KIS API)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.markets import get_profile
from src.markets.tick_size import round_buy_to_tick
from src.strategy._kw_common import (
    rebalance_dates_kr_quarterly,
    adjust_signals_to_trading,
)
from src.strategy.kw_super_value import generate_super_value_signals


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--capital", type=int, required=True, help="시드 자본 (KRW)")
    p.add_argument("--buffer-pct", type=float, default=1.0, help="현금 버퍼 (%) — 호가단위 잔여")
    p.add_argument("--execute", action="store_true", help="자동 매수 실행 (KIS API)")
    p.add_argument("--dry-run", action="store_true", help="execute 시에도 실제 주문 안 보냄")
    p.add_argument("--save-pending", action="store_true",
                   help="live_trading/wed_buy_pending_kr.json 에 저장 — 매주 수 09:00 KST 자동 매수 (Windows Task Scheduler QT_KR_WedMorningBuy)")
    args = p.parse_args()

    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    params = dict(cfg["kw_super_value"])
    top_n = params.get("top_n", 18)
    small_cap_pct = params.get("small_cap_pct", 0.15)

    today = pd.Timestamp.today().normalize()
    print(f"=" * 70)
    print(f"KW Super Value 즉시 신호 산출 ({today.date()})")
    print(f"=" * 70)
    print(f"파라미터: small_cap_pct={small_cap_pct}, top_n={top_n}")
    print(f"시드 자본: ₩{args.capital:,}")

    # 1) 가장 최근 분기 리밸런싱일 찾기
    raw_rebal = rebalance_dates_kr_quarterly(
        "2026-01-01", today.strftime("%Y-%m-%d"),
        params["rebalance_months"], params["rebalance_dom"],
    )
    if not raw_rebal:
        print("[FAIL] 올해 분기 리밸런싱일 없음")
        return
    print(f"\n분기 리밸런싱일들: {[d.date() for d in raw_rebal]}")
    latest_rebal_raw = raw_rebal[-1]
    print(f"가장 최근: {latest_rebal_raw.date()}")

    # 2) 데이터 로드 (가격 + 펀더멘털)
    print(f"\n[1/4] 가격 데이터 로딩...")
    start_dt = (latest_rebal_raw - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    end_dt = today.strftime("%Y-%m-%d")
    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    price_data = fetch_all(tickers, start_dt, end_dt, min_bars=60, market="kr")
    print(f"  {len(price_data)} 종목 가격 데이터 보유")

    # 영업일 보정
    calendar = price_data[profile.index_ticker].index
    rebal_dates = adjust_signals_to_trading([latest_rebal_raw], calendar)
    actual_rebal = rebal_dates[0]
    print(f"\n[2/4] 영업일 보정 후 신호일: {actual_rebal.date()}")
    if actual_rebal > today:
        print(f"  ⚠️  신호일이 미래 ({actual_rebal.date()}) — 아직 진입 불가")
        return
    print(f"  (오늘 {today.date()} 기준 {(today - actual_rebal).days}일 지남)")

    # 3) 펀더멘털 + 신호 생성
    print(f"\n[3/4] 펀더멘털 + KW SV 신호 생성...")
    panel = build_fundamentals_panel([actual_rebal])
    # generate_super_value_signals 가 내부에서 분기 일정 재계산 — 신호일 포함 충분히 넓게
    start_window = (actual_rebal - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    end_window   = (actual_rebal + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    sigs = generate_super_value_signals(panel, price_data, params, start_window, end_window)
    # 가장 최근 신호만 선택 (날짜 ≤ 오늘)
    sigs = [s for s in sigs if s.date <= today]
    if not sigs:
        print("[FAIL] 신호 생성 실패 — actual_rebal 일자 신호가 panel 에 없음")
        print(f"      디버그: panel keys = {list(panel.keys())[:5]}... (총 {len(panel)})")
        if panel:
            first_t = next(iter(panel.keys()))
            print(f"      panel[{first_t}] 인덱스 샘플: {list(panel[first_t].index[:5])}")
        return
    sig = sigs[-1]   # 가장 최근
    print(f"  신호일: {sig.date.date()}, 진입 종목: {len(sig.weights)} (universe size: {sig.universe_size})")

    # 4) 매수 수량 산출
    print(f"\n[4/4] 종목당 매수 수량 산출 (시드 ₩{args.capital:,})")
    budget = args.capital * (1 - args.buffer_pct / 100)
    per_stock = budget / top_n
    print(f"  종목당 예산: ₩{per_stock:,.0f} (버퍼 {args.buffer_pct}% 제외)")

    # 각 종목 현재가 + 매수 수량 계산
    orders = []
    total_spent = 0
    for ticker in sorted(sig.weights.keys()):
        px_df = price_data.get(ticker)
        if px_df is None or px_df.empty:
            continue
        last_close = float(px_df.iloc[-1]["close"])
        if last_close <= 0:
            continue
        # 호가단위 적용
        order_px = int(round_buy_to_tick(last_close, "kr"))
        qty = int(per_stock // order_px)
        if qty < 1:
            print(f"  ⚠️ {ticker} (현재가 ₩{order_px:,}): 매수 불가 (예산 ₩{per_stock:,.0f} < 단가)")
            continue
        spent = qty * order_px
        total_spent += spent
        orders.append({
            "ticker": ticker,
            "name":   "",
            "price":  order_px,
            "qty":    qty,
            "value":  spent,
            "score":  sig.scores.get(ticker, 0),
        })

    # 출력
    print()
    print(f"=" * 70)
    print(f"매수 안내 ({len(orders)} 종목)")
    print(f"=" * 70)
    print(f"{'종목':>8s} {'현재가':>10s} {'수량':>5s} {'예산':>12s} {'score':>7s}")
    print(f"-" * 70)
    orders.sort(key=lambda x: x["score"])   # score 좋은 순
    for o in orders:
        print(f"{o['ticker']:>8s} ₩{o['price']:>9,} {o['qty']:>4d}주 ₩{o['value']:>11,} {o['score']:>7.3f}")
    print(f"-" * 70)
    print(f"  총 매수 금액: ₩{total_spent:,}")
    print(f"  잔여 현금:   ₩{args.capital - total_spent:,}")
    print(f"  자본 사용률: {total_spent / args.capital * 100:.1f}%")

    # 자동 매수
    if args.execute:
        print()
        print(f"=" * 70)
        print(f"KIS API 자동 매수 실행")
        print(f"=" * 70)
        from live_trading.kis_client import KISClient
        cli = KISClient.from_config(allow_prod=True, market="kr")
        print(f"market={cli.market}, mode={cli.mode}, account={cli.cano}-{cli.acnt_prdt}")

        if args.dry_run:
            print("\n[DRY-RUN] 실제 주문 안 보냄. 시뮬레이션만:")

        results = []
        for o in orders:
            if args.dry_run:
                print(f"  [DRY] {o['ticker']} {o['qty']}주 @₩{o['price']:,}")
                results.append({"ticker": o["ticker"], "status": "dry-run"})
                continue
            try:
                r = cli.place_order(o["ticker"], o["qty"], side="BUY",
                                    order_type="LIMIT", price=o["price"])
                results.append({"ticker": o["ticker"], "order_no": r.get("order_no"), "raw": r})
                print(f"  ✅ {o['ticker']} {o['qty']}주 @₩{o['price']:,} → 주문번호 {r.get('order_no')}")
                time.sleep(0.5)   # KIS 초당 한도 회피
            except Exception as e:
                results.append({"ticker": o["ticker"], "error": str(e)})
                print(f"  ❌ {o['ticker']}: {e}")

        print(f"\n주문 결과: {len([r for r in results if r.get('order_no')])} 성공 / {len(results)} 시도")

    # 매수 종목 저장 (페이퍼 트래커)
    out_path = Path("paper_trading/positions_kw_super_value_kr.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    state = {
        "strategy": "kw_super_value",
        "rebal_date": str(actual_rebal.date()),
        "capital": args.capital,
        "spent":   total_spent,
        "orders":  orders,
    }
    out_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {out_path}")

    # pending 저장 — Windows Task Scheduler 가 매주 수 09:00 KST 자동 매수
    if args.save_pending:
        pending_path = Path("live_trading/wed_buy_pending_kr.json")
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        # 기존 pending 보존 (다른 전략 — clenow/weinstein 등)
        existing = {}
        if pending_path.exists():
            try:
                existing = json.loads(pending_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing["kw_super_value"] = {
            "signal_date": str(actual_rebal.date()),
            "symbols":     [o["ticker"] for o in orders],
        }
        pending_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved pending: {pending_path}")
        print(f"  → 다음 수요일 09:00 KST QT_KR_WedMorningBuy 자동 매수")

    if not args.execute:
        print()
        print(f"=" * 70)
        print(f"수동 매수 가이드")
        print(f"=" * 70)
        print(f"1. KIS HTS/MTS 로그인 (계좌 44474877-01)")
        print(f"2. 각 종목을 LIMIT 주문 (현재가 그대로 또는 +0~1%)")
        print(f"3. 분할 주문 권장 (한 번에 가지 말고 5초 간격)")
        print(f"4. 미체결 시 다음날 재시도 (호가 +1% 상향)")
        print(f"5. 자동 매수 원하면 --execute 옵션 추가")


if __name__ == "__main__":
    main()
