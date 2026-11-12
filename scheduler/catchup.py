"""
scheduler/catchup.py
놓친 장마감 처리 따라잡기 — 하루 몇 번(08:30 / 12:30 / 18:30 / 21:00 KST) 실행돼
"오늘 장마감 처리가 아직 안 돌았으면" git 동기화 → daily_close → summary 를 순서대로 돌린다.

배경: 2026-11-12 새벽 Windows 업데이트 재부팅으로 05:40·06:30·07:00 작업이 전부 빠졌고,
작업 스케줄러의 "놓치면 가능한 빨리 실행" 은 따라잡지 못했다. 원인이 무엇이든 그날 안에 복구되게 한다.

판단 기준:
  - 평일(KST 월~금)이고, 지금이 06:45~22:00 KST (미국 정규장이 닫혀 있는 시간)
  - live_trading/daily_close_done.json 의 날짜가 오늘이 아님
    (daily_close 가 **끝까지 정상 처리됐을 때만** 남기는 완료 기록. NAV 는 실행 초반에 기록되므로
     기준으로 쓰면 안 된다 — 이후 단계에서 실패하거나 데이터 정체로 신호를 건너뛰어도 NAV 는 남는다)
daily_close 는 다시 돌려도 안전하다 (주문은 키로 중복 방지, NAV 는 같은 날짜 덮어씀).

수동 실행:
    python scheduler/catchup.py [--check]     # --check: 판단만 하고 실행하지 않음
"""
import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DONE_MARKER  = Path("live_trading/daily_close_done.json")
WINDOW_START = time(6, 45)     # 정규 06:30 실행이 끝날 시간을 준 뒤부터
WINDOW_END   = time(22, 0)     # 미국장 개장(서머타임 22:30 KST) 전까지
STEPS = [
    ["scheduler/git_sync.py"],
    ["scheduler/daily_close.py", "--refresh"],
    ["scheduler/summary.py"],
]

log = logging.getLogger("kis")


def last_done_date(path: Path = DONE_MARKER) -> str | None:
    """마지막으로 끝까지 처리된 daily_close 의 실행일. 기록이 없거나 읽을 수 없으면 None (= 미완료로 본다)."""
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["date"])[:10]
    except Exception:
        return None


def should_run(now_kst: datetime, last_nav: str | None) -> tuple[bool, str]:
    if now_kst.weekday() >= 5:
        return False, "주말 — 장마감 처리 없음"
    if not (WINDOW_START <= now_kst.time() <= WINDOW_END):
        return False, f"실행 가능 시간(06:45~22:00 KST) 밖 ({now_kst:%H:%M})"
    if last_nav == now_kst.strftime("%Y-%m-%d"):
        return False, "오늘 장마감 처리 이미 완료"
    return True, f"오늘({now_kst:%m/%d}) 장마감 처리 완료 기록 없음 (마지막 완료 {last_nav or '없음'})"


def main(check_only: bool = False) -> None:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    run, why = should_run(now, last_done_date())
    log.info(f"[catchup] {why} → {'실행' if run else '건너뜀'}")
    if not run or check_only:
        return
    _notify(f"⏰ <b>놓친 장마감 처리 따라잡기</b> ({now:%m/%d %H:%M} KST)\n{why}")
    for step in STEPS:
        log.info(f"[catchup] 실행: {' '.join(step)}")
        r = subprocess.run([sys.executable, *step], cwd=str(ROOT))
        if r.returncode != 0:
            log.error(f"[catchup] {step[0]} 종료 코드 {r.returncode}")
            if step[0].endswith("daily_close.py"):
                _notify(f"🚨 따라잡기 실패: daily_close 종료 코드 {r.returncode} — 로그 확인 필요")
                return


def _notify(msg: str) -> None:
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config_live.yaml").read_text(encoding="utf-8"))
        token   = cfg.get("notify", {}).get("telegram_token", "")
        chat_id = cfg.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"[catchup] 텔레그램 알림 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="판단만 하고 실행하지 않음")
    args = p.parse_args()
    from scheduler._log_helper import setup_file_logger
    setup_file_logger("catchup")
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    main(check_only=args.check)
