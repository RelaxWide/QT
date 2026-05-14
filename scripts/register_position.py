"""
KIS 잔고에 있는 종목을 전략 positions_live_*.json 에 수동 등록.

GHA 스모크 테스트나 수동 매수로 KIS 에 들어왔으나 로컬 추적 파일에
없는 포지션을 등록하기 위해 사용한다.

예:
    # KIS 현재가로 1주 등록
    python scripts/register_position.py --strategy clenow --symbol APA

    # 명시적 진입가/수량
    python scripts/register_position.py --strategy clenow --symbol APA --qty 1 --fill-price 37.48

KIS 잔고에서 자동으로 qty / avg_price 를 가져온다 (--qty / --fill-price 미지정 시).
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.kis_client import KISClient
from live_trading.tracker_live import LivePosition, load_live_positions, save_live_positions


def main(strategy: str, symbol: str, qty: int | None, fill_price: float | None,
         entry_date: str | None):
    if qty is None or fill_price is None:
        print("[register] KIS 잔고에서 자동 추출 중...")
        kis = KISClient.from_config(allow_prod=True)
        bal = kis.get_balance()
        match = next((p for p in bal.get("positions", []) if p["symbol"] == symbol), None)
        if not match:
            print(f"[register] KIS 잔고에 {symbol} 없음 - 종료")
            return
        qty        = qty        or int(match["qty"])
        fill_price = fill_price or float(match["avg_price"])
        print(f"[register] {symbol}: KIS qty={qty}, avg_price=${fill_price:.4f}")

    today = entry_date or date.today().isoformat()

    positions = load_live_positions(strategy)
    if symbol in positions:
        print(f"[register] 이미 등록됨: {symbol} (qty={positions[symbol].qty}). 덮어쓰기.")
    positions[symbol] = LivePosition(
        symbol=symbol,
        strategy=strategy,
        entry_date=today,
        fill_date=today,
        signal_price=fill_price,
        fill_price=fill_price,
        qty=qty,
        order_no="MANUAL_REGISTER",
    )
    save_live_positions(strategy, positions)
    print(f"[register] {symbol} → positions_live_{strategy}.json 저장 완료")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", required=True, choices=["phase4", "clenow", "weinstein"])
    p.add_argument("--symbol",   required=True)
    p.add_argument("--qty",      type=int, default=None)
    p.add_argument("--fill-price", type=float, default=None)
    p.add_argument("--entry-date", default=None, help="ISO 형식 (기본: 오늘)")
    args = p.parse_args()
    main(args.strategy, args.symbol, args.qty, args.fill_price, args.entry_date)
