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

# 텔레그램 한도는 4096 (파싱 후 UTF-16 단위). 넘으면 잘리지 않고 400 으로 통째로 거부된다.
# 태그 포함 원문 길이로 재서 보수적으로 자른다 (2026-11: 페이퍼 수요일 교체일 4,298자 추정)
MAX_LEN = 4000


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    """줄 단위로 나눠 HTML 태그가 조각 사이에 걸리지 않게 한다. 한 줄이 한도를 넘을 때만 강제로 자름."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        step = limit // 2   # UTF-16 은 문자당 최대 2
        pieces = [line[i:i + step] for i in range(0, len(line), step)] if _u16(line) > limit else [line]
        for p in pieces:
            cand = f"{cur}\n{p}" if cur else p
            if cur and _u16(cand) > limit:
                chunks.append(cur)
                cur = p
            else:
                cur = cand
    if cur:
        chunks.append(cur)
    return chunks


def _post(url: str, chat_id: str, text: str, token: str) -> bool:
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        # requests 예외 메시지엔 URL(=봇 토큰)이 들어 있다 — 로그에 남지 않게 가린다
        print(f"[Telegram] 전송 실패 (네트워크): {str(e).replace(token, '<token>')}")
        return False
    if not resp.ok:
        # 호출부가 반환값을 확인 안 해도 실패가 로그에 남도록 여기서 반드시 출력
        # (2026-10 발견: 토큰 만료로 401 이 수개월간 조용히 삼켜짐)
        print(f"[Telegram] 전송 실패 ({resp.status_code}): {resp.text[:200]}")
    return resp.ok


def send(text: str, token: str = "", chat_id: str = "") -> bool:
    token   = token   or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print("[Telegram] 토큰 또는 chat_id 미설정 — 콘솔 출력으로 대체")
        print(text)
        return False

    url    = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _split(text)
    if len(chunks) > 1:
        chunks = [f"({i}/{len(chunks)})\n{c}" for i, c in enumerate(chunks, 1)]
    # 한 조각이 실패해도 나머지는 보낸다
    results = [_post(url, chat_id, c, token) for c in chunks]
    return all(results)
