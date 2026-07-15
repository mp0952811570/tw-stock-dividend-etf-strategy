"""
Step 2: 篩選高殖利率存股名單
- 殖利率 > 4%（放寬一點，先多收集候選）
- 有本益比資料（排除 weird 的）
- 殖利率 < 15%（超高的可能有問題，避開地雷）
- 依市值排序（大公司優先，流動性好）
"""
import json
import os
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
FUND_CACHE = os.path.join(CACHE_DIR, "fundamentals.json")


def filter_dividend_stocks(
    min_yield=4.0,
    max_yield=15.0,
    max_pe=None,
    min_market_cap=None,
    top_n=50,
):
    with open(FUND_CACHE, "r", encoding="utf-8") as f:
        fund = json.load(f)

    df = pd.DataFrame(fund.values())

    # 篩殖利率
    df = df[df["dividend_yield"].notna()]
    df = df[(df["dividend_yield"] >= min_yield) & (df["dividend_yield"] <= max_yield)]

    # 篩本益比（有PE的優先，沒PE的保留但排名靠後）
    if max_pe:
        df = df[(df["trailing_pe"].isna()) | (df["trailing_pe"] <= max_pe)]

    # 篩市值
    if min_market_cap:
        df = df[df["market_cap"].notna() & (df["market_cap"] >= min_market_cap)]

    # 排序：殖利率降序 + 市值降序
    df = df.sort_values(
        by=["dividend_yield", "market_cap"], ascending=[False, False]
    ).reset_index(drop=True)

    df = df.head(top_n)

    print(f"篩選條件: 殖利率 {min_yield}%-{max_yield}%, 取前 {top_n} 檔")
    print(f"✅ 篩出 {len(df)} 檔")
    return df


if __name__ == "__main__":
    df = filter_dividend_stocks(min_yield=4.0, max_yield=15.0, top_n=50)
    print(df[
        ["stock_id", "stock_name", "industry", "dividend_yield", "trailing_pe", "market_cap"]
    ].to_string(index=False))
