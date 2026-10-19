import sys

# pykrx 는 KRX_ID/KRX_PW 가 설정돼 있으면 import 시점에 KRX 로그인을 시도한다
# (website/comm/webio.py 모듈 레벨 `_session = build_krx_session()`). 로그인
# 실패 응답(예: CD010 비밀번호 변경 필요)을 처리하는 코드가 "⚠️" 를 print() 하는데
# Windows 콘솔 기본 인코딩(cp949)이 이를 인코딩 못해 UnicodeEncodeError 로 import
# 자체가 깨진다 — 이 예외가 fundamentals_kr.py 의 광범위한 try/except 에 삼켜져
# "fundamentals panel 비어있음"으로만 보이고 진짜 원인(로그인 실패)이 로그에 남지
# 않았던 사고(2026-08 KW SV 리밸런싱 실패) 가 있었다. 로그인 성패와 무관하게
# import 자체는 항상 성공해야 하므로 stdout/stderr 를 UTF-8 로 강제한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
