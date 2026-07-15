"""
Step 1: 抓取全部台股清單 + 殖利率/本益比資料
- FinMind: 取得台股清單 + 產業分類（免費）
- yfinance: 取得殖利率(yield)、本益比(P/E)、歷史5年日K線
"""
import requests
import time
import json
import os
import pandas as pd
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Step 1a: 從 FinMind 抓全部台股清單 ──────────────────────
def fetch_stock_list():
    cache = os.path.join(CACHE_DIR, "stock_list.json")
    if os.path.exists(cache):
        mtime = os.path.getmtime(cache)
        if time.time() - mtime < 86400:  # 24h cache
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)

    print("從 FinMind 抓取台股清單...")
    url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&date=2026-07-01&token="
    r = requests.get(url, timeout=30)
    data = r.json().get("data", [])

    # 篩掉 ETF/ETN/指數/存託憑證等，只要個股
    skip_keywords = ["ETF", "ETN", "指數", "存託憑證", "受益證券", "大盤", "所有證券", "創新板"]
    stocks = []
    for d in data:
        cat = d.get("industry_category", "")
        if any(kw in cat for kw in skip_keywords):
            continue
        stocks.append({
            "stock_id": d["stock_id"],
            "stock_name": d["stock_name"],
            "industry": cat,
            "type": d.get("type", "twse"),
        })

    with open(cache, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

    print(f"✅ 共 {len(stocks)} 檔個股")
    return stocks


# ── Step 1b: 從 yfinance 抓殖利率 + 本益比 ───────────────────
def fetch_fundamentals_batch(stock_list, batch_size=50):
    """
    抓殖利率(dividendYield)、本益比(trailingPE)、配息率(payoutRatio)
    yfinance 一次抓一檔，每檔約 1-2 秒
    """
    cache = os.path.join(CACHE_DIR, "fundamentals.json")
    results = {}
    
    if os.path.exists(cache):
        with open(cache, "r", encoding="utf-8") as f:
            results = json.load(f)
    
    done = set(results.keys())
    todo = [s for s in stock_list if s["stock_id"] not in done]
    
    print(f"已快取 {len(done)} 筆，剩 {len(todo)} 筆待抓")
    
    for i, stock in enumerate(todo):
        sid = stock["stock_id"]
        suffix = ".TW" if stock["type"] == "twse" else ".TWO"
        ticker_id = f"{sid}{suffix}"
        
        try:
            t = yf.Ticker(ticker_id)
            info = t.info
            
            div_yield = info.get("dividendYield", None)
            trailing_pe = info.get("trailingPE", None)
            payout_ratio = info.get("payoutRatio", None)
            market_cap = info.get("marketCap", None)
            
            # 把殖利率轉成百分比
            if div_yield is not None:
                div_yield = round(div_yield * 100, 2)  # 0.05 -> 5.0%
            
            if div_yield is not None or trailing_pe is not None:
                results[sid] = {
                    "stock_id": sid,
                    "stock_name": stock["stock_name"],
                    "industry": stock["industry"],
                    "dividend_yield": div_yield,
                    "trailing_pe": round(trailing_pe, 2) if trailing_pe else None,
                    "payout_ratio": round(payout_ratio * 100, 2) if payout_ratio else None,
                    "market_cap": market_cap,
                    "type": stock["type"],
                }
        except Exception as e:
            pass  # 很多小股票 yfinance 沒資料，skip
        
        # 進度
        if (i + 1) % 50 == 0:
            print(f"  進度: {i+1}/{len(todo)}")
            # 中途存檔
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        
        time.sleep(0.3)  # 避免 rate limit
    
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 成功抓到 {len(results)} 檔基本面資料")
    return results


if __name__ == "__main__":
    stocks = fetch_stock_list()
    print(f"\n開始抓殖利率資料（可能要 10-15 分鐘）...")
    fund = fetch_fundamentals_batch(stocks)
    
    # 快速統計
    df = pd.DataFrame(fund.values())
    if not df.empty:
        high_yield = df[df["dividend_yield"].notna() & (df["dividend_yield"] > 0)]
        print(f"\n有殖利率資料的: {len(high_yield)} 檔")
        print(high_yield.sort_values("dividend_yield", ascending=False).head(20)[
            ["stock_id", "stock_name", "industry", "dividend_yield", "trailing_pe", "market_cap"]
        ].to_string(index=False))
