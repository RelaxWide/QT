"""
DART OpenAPI 검증.
DART_API_KEY 환경변수 또는 config.yaml 의 dart_api_key 설정 후 실행.

사용:
    python scripts/debug_dart_kr.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch.dart_kr import fetch_financials, compute_derived_metrics, _get_api_key


def main():
    key = _get_api_key()
    print(f"DART API key 설정: {bool(key)} ({key[:8]+'...' if key else '없음'})")
    if not key:
        print("\n[STOP] DART API key 필요. https://opendart.fss.or.kr/ 회원가입 + 인증키 신청 (5분)")
        print("       export DART_API_KEY=<40자 키>")
        print("       또는 config.yaml 에 dart_api_key: '<키>' 추가")
        return

    # 삼성전자 2023 사업보고서 (refresh=True 로 캐시 무시 — 코드 수정 검증용)
    print("\n=== 005930 (삼성전자) 2023 사업보고서 (refresh=True) ===")
    fin = fetch_financials("005930", 2023, "annual", refresh=True)
    if fin:
        for k, v in fin.items():
            print(f"  {k}: {v}")
        print()
        # 전년도와 비교
        fin_prev = fetch_financials("005930", 2022, "annual", refresh=True)
        derived = compute_derived_metrics(fin, fin_prev)
        print("=== 유도 지표 ===")
        for k, v in derived.items():
            if isinstance(v, float):
                print(f"  {k}: {v:.4f}")
            else:
                print(f"  {k}: {v}")
    else:
        print("[FAIL] 재무제표 조회 실패")


if __name__ == "__main__":
    main()
