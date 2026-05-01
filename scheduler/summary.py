"""
scheduler/summary.py
실행 시각: 평일 07:00 KR
역할: 실전 계좌 일일 요약 → 텔레그램 전송

수동 실행:
    python scheduler/summary.py [--print-only]
"""
import argparse
import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.kis_client import KISClient
from live_trading.account import slippage_report
from live_trading.tracker_live import load_live_positions, SLIPPAGE_LOG

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

STRATEGIES = ("phase4", "clenow", "weinstein")


def build_summary() -> str:
    lines = ["[QT Live] 일일 요약\n"]

    # KIS 잔고
    try:
        kis = KISClient.from_config()
        bal = kis.get_balance()
        cash  = bal.get("cash_usd", 0)
        total = bal.get("total_eval_usd", 0)
        lines.append(f"KIS 잔고")
        lines.append(f"  예수금:   ${cash:,.2f}")
        lines.append(f"  평가손익: ${total:,.2f}")
        lines.append("")
    except Exception as e:
        lines.append(f"KIS 잔고 조회 실패: {e}\n")

    # 전략별 보유 포지션
    for strategy in STRATEGIES:
        positions = load_live_positions(strategy)
        if not positions:
            continue
        lines.append(f"[{strategy}] {len(positions)}종목 보유")
        for sym, pos in positions.items():
            lines.append(f"  {sym:6s}  {pos.qty}주  진입 ${pos.fill_price:.2f}")
        lines.append("")

    # 슬리피지 요약
    sr = slippage_report()
    if sr.get("records", 0) > 0:
        lines.append(f"슬리피지 (누적 {sr['records']}건)")
        lines.append(f"  평균: {sr['mean_pct']:+.3f}%  표준편차: {sr['std_pct']:.3f}%")
        lines.append("")

    return "\n".join(lines)


def main(print_only: bool = False):
    msg = build_summary()
    log.info("\n" + msg)

    if print_only:
        return

    try:
        cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        token    = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id  = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
            log.info("[summary] 텔레그램 전송 완료")
        else:
            log.warning("[summary] telegram_token/chat_id 미설정 — 전송 스킵")
    except Exception as e:
        log.error(f"[summary] 텔레그램 전송 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--print-only", action="store_true", help="텔레그램 전송 없이 콘솔 출력만")
    args = p.parse_args()
    main(print_only=args.print_only)
