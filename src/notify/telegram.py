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
    resp = requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    return resp.ok
