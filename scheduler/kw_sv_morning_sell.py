"""
scheduler/kw_sv_morning_sell.py
실행 시각: 평일 KST 09:00 (KOSPI 정규장 개장 직후)
역할:
  daily_close_kr 가 KW Super Value 분기 리밸런싱에서 top-18 탈락분으로
  저장한 매도 예약(live_trading/kw_sv_pending_sells.json)을 실제로 매도.

  KIS 는 정규장(09:00~15:30 KST) 밖에는 매도 주문을 거부한다(APBK0918,
  "증시시간이 아닙니다") — daily_close_kr 는 16:00(장마감 후) 실행이라
  즉시 매도가 구조적으로 불가능해서 다음 영업일 이 시각으로 미뤄 처리한다.

수동:
    python scheduler/kw_sv_morning_sell.py [--dry-run] [--no-wait]
"""
import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler._log_helper import setup_file_logger
setup_file_logger("kw_sv_morning_sell")

from live_trading.orders import OrderManager

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

KW_SELL_PENDING_PATH = Path("live_trading/kw_sv_pending_sells.json")
TARGET_KST_HOUR      = 9
MARKET                = "kr"


def _wait_until_target_kst():
    kst    = ZoneInfo("Asia/Seoul")
    now    = datetime.now(kst)
    target = now.replace(hour=TARGET_KST_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        log.info(f"[kw_sv_sell] 현재 KST {now:%H:%M} - 즉시 실행")
        return
    wait = (target - now).total_seconds()
    log.info(f"[kw_sv_sell] 현재 KST {now:%H:%M} -> {TARGET_KST_HOUR}:00 까지 {wait/60:.1f}분 대기")
    time.sleep(wait)
    log.info(f"[kw_sv_sell] KST {TARGET_KST_HOUR}:00 도달 - 주문 실행")


def _notify(msg: str, cfg_live: dict):
    try:
        token   = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            if not telegram.send(msg, token=token, chat_id=chat_id):
                log.warning("[kw_sv_sell] 텔레그램 전송 실패 (토큰/chat_id 확인 필요)")
    except Exception as e:
        log.warning(f"[kw_sv_sell] 텔레그램 실패: {e}")


def main(dry_run: bool = False, no_wait: bool = False):
    if not KW_SELL_PENDING_PATH.exists():
        log.info("[kw_sv_sell] 매도 예약 없음 - 종료")
        return

    payload = json.loads(KW_SELL_PENDING_PATH.read_text(encoding="utf-8"))
    keep_symbols = payload.get("keep_symbols", [])
    signal_date  = payload.get("signal_date", "")

    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))

    if not no_wait:
        _wait_until_target_kst()

    om = OrderManager.from_config(allow_prod=True, market=MARKET)
    results, skipped = om.place_kw_sv_exits(keep_symbols, signal_date, dry_run=dry_run)

    sold        = [r.symbol for r in results if r.ok]
    sell_failed = [r.symbol for r in results if not r.ok]
    skipped_halt = [s["symbol"] for s in skipped]

    if not dry_run:
        KW_SELL_PENDING_PATH.unlink()
        log.info("[kw_sv_sell] 매도 예약 파일 삭제")

    lines = [f"🇰🇷 KR KW SV 탈락분 매도 ({signal_date})"]
    if sold:
        lines.append(f"📤 매도 완료 {len(sold)}건: " + ", ".join(sold))
    if sell_failed:
        lines.append("⚠️ 매도 주문 실패 (재시도 필요): " + ", ".join(sell_failed))
    if skipped_halt:
        lines.append("🚫 매도 불가(거래정지 추정, 수동 확인 필요): " + ", ".join(skipped_halt))
    if not (sold or sell_failed or skipped_halt):
        lines.append("매도 대상 없음")
    _notify("\n".join(lines), cfg_live)
    log.info("[kw_sv_sell] " + " | ".join(lines))

    # 실패분은 다음 영업일 재시도 — 예약 파일 유지 (실패 없으면 이미 삭제됨)
    if not dry_run and sell_failed:
        KW_SELL_PENDING_PATH.write_text(json.dumps({
            "keep_symbols": keep_symbols, "signal_date": signal_date,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"[kw_sv_sell] 매도 실패 {len(sell_failed)}건 — 예약 파일 재저장 (다음 영업일 재시도)")

    log.info("[kw_sv_sell] 완료")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-wait", action="store_true",
                   help="KST 09:00 대기 없이 즉시 실행 (테스트용)")
    args = p.parse_args()
    main(dry_run=args.dry_run, no_wait=args.no_wait)
