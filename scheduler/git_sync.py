"""
scheduler/git_sync.py
실행 시각: 평일 05:40 KR (daily_close 06:00 직전)
역할:
  GHA 페이퍼 커밋을 로컬로 당겨온다 (git pull --ff-only origin master).
  실패하면 텔레그램 경고 — 예전엔 git 을 직접 실행해서 실패가 아무 데도 안 보였고,
  로컬이 GHA 소유 파일을 덮어쓴 탓에 73커밋 뒤처진 채 방치됐다 (2026-10).

수동 실행:
    python scheduler/git_sync.py
"""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler._log_helper import setup_file_logger
setup_file_logger("git_sync")

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("git_sync")


# 작업 스케줄러 환경은 PATH 가 다를 수 있어 설치 경로를 fallback 으로 둔다
GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run([GIT, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    r = _git("pull", "--ff-only", "origin", "master")
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        log.info(f"[git_sync] OK: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''}")
        return

    log.error(f"[git_sync] pull 실패 (rc={r.returncode}): {out}")
    # 원인 진단용: 커밋 안 된 추적 파일 + 원격 대비 앞/뒤 커밋 수
    dirty = [l[3:] for l in _git("status", "--porcelain", "-uno").stdout.splitlines()]
    ahead_behind = _git("rev-list", "--left-right", "--count",
                        "HEAD...origin/master").stdout.split()
    lines = ["🚨 <b>로컬 git 동기화 실패</b>",
             "GHA 페이퍼 상태를 못 받아옴 — 방치하면 로컬이 계속 뒤처진다"]
    if len(ahead_behind) == 2:
        lines.append(f"로컬 앞섬 {ahead_behind[0]} / 뒤처짐 {ahead_behind[1]}커밋")
    if dirty:
        lines.append("수정된 추적 파일: " + ", ".join(dirty[:8]))
    lines.append(f"git: {out.splitlines()[-1][:200] if out else '(출력 없음)'}")
    _notify("\n".join(lines))
    sys.exit(1)


def _notify(msg: str):
    try:
        cfg = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        token   = cfg.get("notify", {}).get("telegram_token", "")
        chat_id = cfg.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
    except Exception as e:
        log.warning(f"[git_sync] 텔레그램 실패: {e}")


if __name__ == "__main__":
    main()
