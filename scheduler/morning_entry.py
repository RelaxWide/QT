"""
scheduler/morning_entry.py
실행 시각: 평일 22:29 KR (미국장 개장 1분 전)
역할: Phase 4 pending 신호 → MOO 근사 LIMIT 주문 전송

등록:
    schtasks /Create /XML scheduler/windows_tasks.xml /TN "QT_Live"
수동 실행:
    python scheduler/morning_entry.py [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.kis_client import KISClient
from live_trading.orders import OrderManager
from live_trading.risk_guard import RiskGuard

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")


def main(dry_run: bool = False):
    guard = RiskGuard.from_config()
    ok, reason = guard.check()
    if not ok:
        log.warning(f"[morning_entry] 주문 차단: {reason}")
        return

    om = OrderManager.from_config(allow_prod=True)
    results = om.place_phase4_entries(dry_run=dry_run)

    ok_count   = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count
    log.info(f"[morning_entry] Phase 4 - OK {ok_count} / FAIL {fail_count}")

    if fail_count > 0:
        _notify(f"[QT Live] Phase 4 진입 실패 {fail_count}건 — 확인 필요")


def _notify(msg: str):
    try:
        import yaml
        cfg = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        token   = cfg.get("notify", {}).get("telegram_token", "")
        chat_id = cfg.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"텔레그램 알림 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)
