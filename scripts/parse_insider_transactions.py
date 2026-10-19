"""
SEC Form 345 분기 ZIP(data/raw/us_insider/*.zip)에서 내부자 "자기자본 매수"(TRANS_CODE='P')만
추출해 하나의 parquet으로 합친다.

출처: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
"P" 코드 = 공개시장/사적 매수 (옵션행사·증여·세금원천징수 등 제외) — 진짜 자기자본 매수만.

사용:
    python scripts/parse_insider_transactions.py
"""
import zipfile
from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw/us_insider")
OUT_PATH = RAW_DIR / "insider_purchases_p.parquet"


def parse_one_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        with z.open("NONDERIV_TRANS.tsv") as f:
            trans = pd.read_csv(f, sep="\t", usecols=[
                "ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE",
                "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
            ], low_memory=False)
        with z.open("SUBMISSION.tsv") as f:
            sub = pd.read_csv(f, sep="\t", usecols=[
                "ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK",
                "ISSUERTRADINGSYMBOL", "DOCUMENT_TYPE",
            ], low_memory=False)

    trans = trans[trans["TRANS_CODE"] == "P"]
    if trans.empty:
        return pd.DataFrame()

    df = trans.merge(sub, on="ACCESSION_NUMBER", how="left")
    df = df[df["ISSUERTRADINGSYMBOL"].notna() & (df["ISSUERTRADINGSYMBOL"] != "")]
    df["TRANS_DATE"] = pd.to_datetime(df["TRANS_DATE"], format="%d-%b-%Y", errors="coerce")
    df["TRANS_SHARES"] = pd.to_numeric(df["TRANS_SHARES"], errors="coerce")
    df["TRANS_PRICEPERSHARE"] = pd.to_numeric(df["TRANS_PRICEPERSHARE"], errors="coerce")
    df = df.dropna(subset=["TRANS_DATE", "TRANS_SHARES"])
    df["value_usd"] = df["TRANS_SHARES"] * df["TRANS_PRICEPERSHARE"].fillna(0)

    return df[["TRANS_DATE", "ISSUERTRADINGSYMBOL", "ISSUERCIK",
               "TRANS_SHARES", "TRANS_PRICEPERSHARE", "value_usd"]].rename(columns={
        "TRANS_DATE": "date", "ISSUERTRADINGSYMBOL": "ticker", "ISSUERCIK": "cik",
        "TRANS_SHARES": "shares", "TRANS_PRICEPERSHARE": "price",
    })


def main():
    zips = sorted(RAW_DIR.glob("*.zip"))
    print(f"{len(zips)}개 분기 파일 파싱")
    frames = []
    for z in zips:
        try:
            d = parse_one_zip(z)
        except Exception as e:
            print(f"  [FAIL] {z.name}: {e}")
            continue
        print(f"  {z.name}: {len(d)}건 P-코드 매수")
        if not d.empty:
            frames.append(d)

    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.drop_duplicates()
    all_df = all_df.sort_values("date")
    all_df.to_parquet(OUT_PATH, index=False)
    print(f"\n총 {len(all_df)}건 저장 -> {OUT_PATH}")
    print(f"기간: {all_df['date'].min()} ~ {all_df['date'].max()}")
    print(f"고유 티커 수: {all_df['ticker'].nunique()}")


if __name__ == "__main__":
    main()
