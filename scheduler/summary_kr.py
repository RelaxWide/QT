"""
scheduler/summary_kr.py
실행 시각: 평일 KST 16:30 (KOSPI 마감 1시간 후)
역할: KR 실전 일일 요약 → 텔레그램

수동:
    python scheduler/summary_kr.py [--print-only]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_trading.kis_client import KISClient

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("kis")

KW_STATE_PATH = Path("paper_trading/positions_kw_super_value_kr.json")


def build_summary() -> str:
    cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
    market_cfg = cfg_live.get("kr") or {}
    cap = market_cfg.get("capital") or cfg_live.get("capital", {})

    today_kst = pd.Timestamp.now(tz="Asia/Seoul")
    today_str = today_kst.strftime("%Y-%m-%d")

    L = [f"📅 {today_str} KR KIS Live"]

    try:
        kis = KISClient.from_config(allow_prod=True, market="kr")
    except Exception as e:
        L.append(f"[!] KIS 클라이언트 초기화 실패: {e}")
        return "\n".join(L)

    try:
        bal = kis.get_balance()
    except Exception as e:
        L.append(f"[!] 잔고 조회 실패: {e}")
        return "\n".join(L)

    cash = bal.get("cash_krw", 0.0)
    pnl  = bal.get("total_pnl_krw", 0.0)
    sum_eval = sum(p["eval_amt"] for p in bal.get("positions", []))
    L.append(f"잔고: 예수금 ₩{cash:,.0f} | 평가 ₩{sum_eval:,.0f} | 손익 ₩{pnl:+,.0f}")

    # 잔고 종목 정보 (KIS 가 어떤 전략 소속인지 구분 못함 — 전체만 표시)
    L.append("")
    L.append(f"━━━ 보유 종목 {len(bal.get('positions', []))} ━━━")
    for pos in bal.get("positions", []):
        sym  = pos["symbol"]
        qty  = pos["qty"]
        avg  = pos["avg_price"]
        cur  = pos["cur_price"]
        ev   = pos["eval_amt"]
        pct  = (cur / avg - 1) * 100 if avg > 0 else 0
        L.append(f"  {sym}: {qty}주 ₩{avg:,.0f}→₩{cur:,.0f} ({pct:+.1f}%) | 평가 ₩{ev:,.0f}")

    L.append("")
    L.append("━━━ ⭐ KW Super Value (KR 메인 엔진) ━━━")
    L.append(f"KR 자본 {cap.get('kw_super_value_pct', 100)}% · "
             f"top {cap.get('kw_super_value_max_positions', 18)} 동일비중 · "
             f"분기 리밸런싱 (5/16·8/16·11/16·4/1)")
    # 최근 리밸런싱 상태
    if KW_STATE_PATH.exists():
        try:
            st = json.loads(KW_STATE_PATH.read_text(encoding="utf-8"))
            L.append(f"최근 리밸런싱: {st.get('rebal_date', '?')} | "
                     f"매수 {len(st.get('orders', []))}종목 ₩{st.get('spent', 0):,}")
        except Exception:
            pass

    return "\n".join(L)


def main(print_only: bool = False):
    msg = build_summary()
    try:
        log.info("\n" + msg)
    except UnicodeEncodeError:
        log.info(msg.encode("ascii", errors="replace").decode("ascii"))

    if print_only:
        return

    try:
        cfg_live = yaml.safe_load(Path("config_live.yaml").read_text(encoding="utf-8"))
        token   = cfg_live.get("notify", {}).get("telegram_token", "")
        chat_id = cfg_live.get("notify", {}).get("telegram_chat_id", "")
        if token and chat_id:
            from src.notify import telegram
            telegram.send(msg, token=token, chat_id=chat_id)
            log.info("[summary_kr] 텔레그램 전송 완료")
        else:
            log.warning("[summary_kr] telegram_token/chat_id 미설정 - 전송 스킵")
    except Exception as e:
        log.error(f"[summary_kr] 텔레그램 전송 실패: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--print-only", action="store_true")
    args = p.parse_args()
    main(print_only=args.print_only)
