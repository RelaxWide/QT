"""
Turn of the Month (TOM) — QuantifiedStrategies 클레임
월말 N영업일 전 종가 매수 → 다음달 M영업일 종가 매도
"""
import pandas as pd


def tom_signal_dates(index: pd.DatetimeIndex, days_before_eom: int = 5, days_after_bom: int = 3) -> tuple[set, set]:
    """
    반환: (entry_dates, exit_dates)
    entry: 월말 days_before_eom 영업일째 종가
    exit:  다음달 days_after_bom 영업일째 종가
    """
    df = pd.DataFrame({"d": index}, index=index)
    df["ym"] = df.index.to_period("M")

    entry_set = set()
    exit_set = set()
    for ym, g in df.groupby("ym"):
        dates = list(g.index)
        # 월말 N번째 (역순) — 5영업일 전 (인덱스: -5)
        if len(dates) >= days_before_eom:
            entry_set.add(dates[-days_before_eom])
        # 월초 M번째 — 3영업일째 (인덱스: M-1)
        if len(dates) >= days_after_bom:
            exit_set.add(dates[days_after_bom - 1])
    return entry_set, exit_set
