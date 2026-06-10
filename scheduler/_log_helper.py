"""
스케줄러 스크립트 공통 파일 로거.

매 실행마다 logs/<task_name>_<YYYYMMDD>.log 에 stdout/stderr 모두 기록.
Task Scheduler 가 stdout 캡처 안 하므로 자동 실행 결과 추적용.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_file_logger(task_name: str, log_dir: str = "logs") -> Path:
    """logs/<task_name>_<YYYYMMDD>.log 핸들러 추가 + stderr/stdout tee.

    Returns:
        log_path: 생성된 로그 파일 경로
    """
    p = Path(log_dir)
    p.mkdir(exist_ok=True)
    log_path = p / f"{task_name}_{datetime.now():%Y%m%d_%H%M%S}.log"

    # 파일 핸들러
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    # 루트 로거에 추가 (기존 basicConfig 핸들러는 유지 — tee)
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.DEBUG)

    # 시끄러운 서드파티 DEBUG 억제 (yfinance 가 KR 로그를 ~519KB 로 비대화시킴)
    # yfinance/peewee 는 매 fetch 마다 Entering/Exiting/cookie/crumb 수천 줄 출력
    for noisy in ("yfinance", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # stdout/stderr 도 파일에 tee
    class _Tee:
        def __init__(self, stream, file):
            self.stream = stream
            self.file = file
        def write(self, msg):
            self.stream.write(msg)
            self.file.write(msg)
            self.file.flush()
        def flush(self):
            self.stream.flush()
            self.file.flush()

    tee_file = open(log_path.with_suffix(".stdout.log"), "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, tee_file)
    sys.stderr = _Tee(sys.stderr, tee_file)

    logging.info(f"=== Task started ({task_name}) — log: {log_path} ===")
    return log_path
