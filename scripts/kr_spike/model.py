"""
KR 급등 패턴 연구 — 표본 외 검증.
  1) 급등(60거래일 내 +50%) 확률 모형  2) 익절·손절 규칙 초과수익 모형
둘 다 과거 자료로만 학습하고 (확장 창, 2년 단위 재학습, 60거래일 간격을 띄움) 그 뒤 기간에서 평가한다.
사용: python scripts/kr_spike/model.py <캐시 폴더>   → <폴더>/spike_oos.parquet
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

cache = Path(sys.argv[1])
ev = pd.read_parquet(cache / "spike_panel.parquet")
OUT = ("ret60", "ret20", "max60", "min60", "del60", "r_tp50_sl20", "r_tp30_sl15", "r_tp50")
feats = [c for c in ev.columns if c not in ("t", "code", "date", "mgmt") + OUT]
ev["spike"] = (ev["max60"] >= 0.5).astype(np.int8)
ev["y_ex"] = ev["r_tp50_sl20"] - ev.groupby("t")["r_tp50_sl20"].transform("mean")
ev["y_ex"] = ev["y_ex"].clip(-0.6, 0.8)
X = ev[feats].astype(np.float32)
year = ev["date"].dt.year
blocks = [(2014, 2015), (2016, 2017), (2018, 2019), (2020, 2021), (2022, 2023), (2024, 2026)]
ev["p_spike"], ev["p_ret"] = np.nan, np.nan
for a, b in blocks:
    te = (year >= a) & (year <= b)
    t0 = ev.loc[te, "t"].min()
    tr = ev["t"] < t0 - 65                                  # 결과 창(60일)이 평가 기간과 겹치지 않게
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
                                         l2_regularization=1.0, random_state=0)
    clf.fit(X[tr], ev.loc[tr, "spike"])
    ev.loc[te, "p_spike"] = clf.predict_proba(X[te])[:, 1]
    reg = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
                                        l2_regularization=1.0, random_state=0)
    reg.fit(X[tr], ev.loc[tr, "y_ex"])
    ev.loc[te, "p_ret"] = reg.predict(X[te])
    print(f"{a}~{b}: 학습 {int(tr.sum()):,}행 (~{ev.loc[tr, 'date'].max().date()}), 평가 {int(te.sum()):,}행", flush=True)
ev[ev["p_spike"].notna()].to_parquet(cache / "spike_oos.parquet", index=False)
