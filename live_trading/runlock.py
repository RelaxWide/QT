"""
계좌 단위 배타 실행 잠금.

QT_DailyClose · QT_CatchUp · QT_WedMorningBuy 는 서로 다른 예약 작업이라 겹쳐 돌 수 있다
(지연 실행, 수동 실행). 주문 경로는 "보냈는지 확인 → 전송 → 기록" 사이가 잠겨 있지 않아
두 프로세스가 같은 주문을 각자 한 번씩 낼 수 있었다. 주문을 내는 스크립트는 이 잠금을 잡고 돈다.

OS 파일 잠금(Windows msvcrt / 그 외 fcntl)이라 프로세스가 죽으면 자동으로 풀린다 — 남은 잠금
파일을 손으로 지울 일이 없다.
"""
from __future__ import annotations

import errno
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("kis")
LIVE_DIR = Path("live_trading")


class LockBusy(RuntimeError):
    """다른 실행이 잠금을 잡고 있다."""


def _try_lock(fh) -> bool:
    """잠금 시도. 다른 실행이 잡고 있으면 False. 그 밖의 오류(권한·파일시스템)는 잠금 없이 진행한다 —
    잠금 장치 고장으로 매매 전체가 멈추는 것이 겹침 위험보다 나쁘다."""
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        if isinstance(e, (BlockingIOError, PermissionError)) or e.errno in (errno.EACCES, errno.EAGAIN, 36):
            return False
        log.warning(f"[lock] 잠금 사용 불가 ({e!r}) — 잠금 없이 진행")
        return True


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def account_lock(name: str = "us", wait_sec: float = 0, poll_sec: float = 5.0):
    """잠금을 잡고 실행. wait_sec 동안 기다려도 못 잡으면 LockBusy."""
    LIVE_DIR.mkdir(exist_ok=True)
    path = LIVE_DIR / f"run_{name}.lock"
    fh = open(path, "a+b")
    try:
        if fh.seek(0, 2) == 0:      # Windows 는 존재하는 바이트 범위를 잠그는 편이 안전
            fh.write(b"\0")
            fh.flush()
        deadline = time.monotonic() + wait_sec
        while not _try_lock(fh):
            if time.monotonic() >= deadline:
                raise LockBusy(f"{path.name} — 다른 실행이 진행 중")
            log.info(f"[lock] {path.name} 대기 중 (다른 실행 진행 중)")
            time.sleep(poll_sec)
        try:
            yield
        finally:
            _unlock(fh)
    finally:
        fh.close()


if __name__ == "__main__":
    # 자가 점검: python -m live_trading.runlock  (이 PC 에서 잠금이 실제로 동작하는지)
    with account_lock("selftest"):
        try:
            with account_lock("selftest"):
                print("FAIL - 두 번째 잠금이 잡혔다 (잠금이 동작하지 않음)")
        except LockBusy:
            print("OK - 잠금 동작 (두 번째 시도는 거부됨)")
    with account_lock("selftest"):
        print("OK - 해제 후 다시 잡힘")
    try:
        (LIVE_DIR / "run_selftest.lock").unlink()
    except OSError:
        pass
