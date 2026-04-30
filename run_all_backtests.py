"""
전체 전략 백테스트 일괄 실행
실행: python run_all_backtests.py
"""
import subprocess
import sys
import time

STRATEGIES = [
    ("Phase 4",   "run_phase4.py"),
    ("Weinstein", "run_weinstein.py"),
    ("Clenow",    "run_clenow.py"),
    ("High52",    "run_high52.py"),
]

results = []
for name, script in STRATEGIES:
    print(f"\n{'='*60}")
    print(f"  {name} ({script})")
    print(f"{'='*60}")
    t0 = time.time()
    ret = subprocess.run([sys.executable, script], capture_output=False)
    elapsed = time.time() - t0
    status = "OK" if ret.returncode == 0 else "FAIL"
    results.append((name, status, elapsed))
    print(f"  [{status}] {elapsed:.0f}s")

print(f"\n{'='*60}")
print("  완료 요약")
print(f"{'='*60}")
for name, status, elapsed in results:
    print(f"  {status:4s} | {name:12s} | {elapsed:.0f}s")
