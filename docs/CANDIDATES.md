# 새 전략 후보 조사 (2026-11-12)

목표: SPY 를 이길 전략. 기존 99개 곡선은 재채점에서 전부 탈락([`RESCORE.md`](RESCORE.md)).
Clenow 는 실계좌 기록(매일 텔레그램 "SPY 대비")으로 판정하고 백테스트 재검증은 하지 않는다 (사용자 결정).

## 1. 기대치 — 조사에서 확인한 기저율

| 사실 | 출처 |
|---|---|
| 15년 기준 미국 대형주 액티브 펀드의 약 90% 가 S&P 500 에 뒤짐. 2025년 한 해도 79% 가 뒤짐 | SPIVA U.S. Scorecard (S&P Dow Jones Indices) |
| 논문에 실린 이상현상 120개: 거래비용과 발표 후 약화를 빼면 평균 기대수익이 월 0.04~0.13% 수준. 가장 강한 것도 월 0.1% 안팎 | Chen & Velikov, "Zeroing in on the Expected Returns of Anomalies" (JFQA) |
| 반론: 팩터 대부분은 재현되고 13개 주제로 묶인다. 회전율 낮은 가치·모멘텀·수익성은 비용 후에도 남는다 | Jensen·Kelly·Pedersen (JF 2023), Novy-Marx & Velikov (RFS 2016) |

→ 종목 선택형 전략으로 얻을 수 있는 초과수익은 잘해야 연 1~2% 대이고, 왕복 0.5% 수수료에서는 **회전율이 낮은 것만** 살아남는다.

## 2. 후보

| 후보 | SPY 와의 관계 | 수익이 SPY 를 넘는가 | 판단 |
|---|---|---|---|
| **추세추종 선물 ETF** (DBMF, KMLM 등) | 상관 거의 0 (2020-12 이후 DBMF 0.15, KMLM -0.10). 2026년 초 주식 급락 때 +7~8% | **아니오.** DBMF 2019-05~2026-05 누적 +89% vs SPY +187%. KMLM 은 2022 고점 대비 -31% 낙폭 | 수익이 아니라 **조합의 낙폭·Sharpe 개선용**. RESCORE 기준으로 검증할 1순위 |
| 저변동성 종목 | 방어적 | **아니오.** S&P 500 Low Vol 지수 2011년 이후 연 10.45% vs 13.24%. 1989년 이후로는 위험조정 성과만 우위 | 수익 목표와 안 맞음 |
| 퀄리티 | 시장과 비슷 | 주기적. 2025년 MSCI World Quality 16.9% vs 21.6% 로 뒤짐 | ETF 로 대체 가능, 직접 운용 이점 없음 |
| 실적 발표 후 드리프트 | 종목 선택형 | 대형주에서는 2006년경 소멸했다는 연구와 소형주·극단적 서프라이즈에는 남아 있다는 연구가 엇갈림 | S&P 500 유니버스에서는 기대 낮음. 소형주는 비용·유동성 문제 (기존 폐기 판정 유지) |

## 3. 결론

- "SPY 보다 **수익률**이 높은" 롱온리 전략은 조사 범위에서 찾지 못했다. 남는 길은 (a) 모멘텀 집중(=Clenow 가 이미 하는 것), (b) 레버리지(이미 폐기), (c) 더 큰 위험 감수뿐이다.
- "SPY 와 **비슷한 수익에 낙폭이 작은**" 조합은 가능성이 있다 — 상관이 낮은 자산을 섞는 방식.
- 따라서 먼저 정할 것: **목표가 수익률 초과인가, 위험조정 성과인가.**

## 출처

- SPIVA: https://www.spglobal.com/spdji/en/spiva/article/spiva-us-year-end-2024 , https://247wallst.com/investing/2026/05/27/if-90-percent-of-professional-managers-cant-beat-the-sp-500-why-do-you-think-you-can/
- Chen & Velikov: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/zeroing-in-on-the-expected-returns-of-anomalies/945133D5A3ECEEAF466AEE91551FD225
- Jensen, Kelly, Pedersen: https://nber.org/system/files/working_papers/w28432/w28432.pdf
- Novy-Marx & Velikov 요약: https://www.etf.com/node/94299.md
- 추세추종 ETF: https://portfolioslab.com/portfolio/btx8i6uzs6yuv4zg87nyrnfw , https://totalrealreturns.com/n/DBMF,SPY , https://totalrealreturns.com/n/KMLM,DBMF,SPY , https://247wallst.com/investing/2026/04/10/the-3-managed-futures-etfs-that-made-money-while-the-sp-500-crashed/
- 저변동성: https://www.spglobal.com/spdji/en/education/article/sp-500-low-volatility-index-15-years-of-live-performance-in-15-exhibits/
- 퀄리티: https://www.morganstanley.com/im/en-lu/institutional-investor/insights/articles/factor-investing-endures.html
- 실적 드리프트: https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again
