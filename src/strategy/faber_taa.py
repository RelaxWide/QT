"""
Meb Faber "A Quantitative Approach to Tactical Asset Allocation" (SSRN, 2006)

규칙:
  매월 말, 각 자산의 종가가 10개월 SMA 위이면 보유, 아래이면 현금.
  보유 자산은 동일비중 (1 / 보유 자산 수).
  현금은 무이자 (보수적 가정).

10개월 SMA: 월간 종가 데이터 기준 (일간 close의 월말 리샘플링).
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class FaberSignal:
    date: pd.Timestamp       # 월말 기준일
    assets_in: list[str]     # 10M SMA 위 자산
    assets_out: list[str]    # 10M SMA 아래 자산 (현금 처리)


def generate_faber_signals(
    price_data: dict[str, pd.DataFrame],
    universe: list[str],
    sma_months: int = 10,
) -> list[FaberSignal]:
    """월별 Faber TAA 신호 생성."""
    # 각 자산의 월말 종가 추출
    monthly: dict[str, pd.Series] = {}
    for sym in universe:
        if sym not in price_data:
            continue
        df = price_data[sym]
        monthly[sym] = df["close"].resample("ME").last()

    if not monthly:
        return []

    # 공통 월말 날짜 인덱스
    combined = pd.DataFrame(monthly).dropna(how="all")
    sma = combined.rolling(sma_months).mean()

    signals: list[FaberSignal] = []
    for i in range(sma_months, len(combined)):
        date = combined.index[i]
        row_close = combined.iloc[i]
        row_sma   = sma.iloc[i]

        in_syms  = []
        out_syms = []
        for sym in universe:
            if sym not in row_close or sym not in row_sma:
                out_syms.append(sym)
                continue
            c = row_close[sym]
            s = row_sma[sym]
            if pd.isna(c) or pd.isna(s):
                out_syms.append(sym)
                continue
            if c > s:
                in_syms.append(sym)
            else:
                out_syms.append(sym)

        signals.append(FaberSignal(
            date=date,
            assets_in=in_syms,
            assets_out=out_syms,
        ))

    return signals
