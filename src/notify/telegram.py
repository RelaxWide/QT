"""
텔레그램 알림 전송

설정 방법:
  1. @BotFather 에서 봇 생성 → BOT_TOKEN 발급
  2. 봇에 메시지 보낸 뒤 https://api.telegram.org/bot<TOKEN>/getUpdates 에서 chat_id 확인
  3. 환경변수 설정:
       set TELEGRAM_BOT_TOKEN=<your_token>
       set TELEGRAM_CHAT_ID=<your_chat_id>
     또는 config.yaml의 telegram 섹션에 직접 입력
"""
import os
import requests


def send(text: str, token: str = "", chat_id: str = "") -> bool:
    token   = token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("[Telegram] 토큰 또는 chat_id 미설정 — 콘솔 출력으로 대체")
        print(text)
        return False

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"[Telegram] 전송 실패 (네트워크): {e}")
        return False
    if not resp.ok:
        # 호출부가 반환값을 확인 안 해도 실패가 로그에 남도록 여기서 반드시 출력
        # (2026-10 발견: 토큰 만료로 401 이 수개월간 조용히 삼켜짐)
        print(f"[Telegram] 전송 실패 ({resp.status_code}): {resp.text[:200]}")
    return resp.ok
