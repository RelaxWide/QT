"""
거래 비용 모델 — 키움증권 미국 주식 기준

수수료: 매수/매도 각각 commission_pct% (기본 0.25%)
양도소득세는 수익에 부과되는 세금으로 전략 성과와 무관하므로 제외.

주의: trade 통계(PF, WR, 평균R)는 수수료 차감 전 총손익 기준
     equity curve(Sharpe, MDD, CAGR)는 수수료 반영 후 순손익 기준
"""
import pandas as pd


class CostModel:
    def __init__(self, commission_pct: float = 0.0):
        self.commission_pct = commission_pct / 100

    def buy_cost(self, value: float) -> float:
        return value * self.commission_pct

    def sell_cost(self, value: float) -> float:
        return value * self.commission_pct


def make_cost_model(cfg: dict) -> CostModel:
    c = cfg.get("cost", {})
    return CostModel(commission_pct=c.get("commission_pct", 0.0))
