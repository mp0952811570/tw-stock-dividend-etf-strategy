"""
抓取 0050/0056/00878/00927 過去5年日K線 + 配息紀錄
"""
import yfinance as yf
import pandas as pd
import json
import os
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
PRICE_CACHE = os.path.join(CACHE_DIR, "etf_prices")
os.makedirs(PRICE_CACHE, exist_ok=True)

ETF_LIST = {
    "0050": {"name": "元大台灣50", "suffix": ".TW"},
    "0056": {"name": "元大高股息", "suffix": ".TW"},
    "00878": {"name": "國泰永續高股息", "suffix": ".TW"},
    "00927": {"name": "群益台灣精選高息", "suffix": ".TW"},
}

def fetch_etf_data(etf_id, period="5y"):
    """抓日K線 + 配息 + 分割"""
    info = ETF_LIST.get(etf_id, {"name": etf_id, "suffix": ".TW"})
    ticker_id = f"{etf_id}{info['suffix']}"
    
    cache_file = os.path.join(PRICE_CACHE, f"{etf_id}_5y.csv")
    div_cache = os.path.join(PRICE_CACHE, f"{etf_id}_dividends.csv")
    
    # 抓K線
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
    else:
        print(f"  抓取 {etf_id} {info['name']} 日K線...")
        ticker = yf.Ticker(ticker_id)
        df = ticker.history(period=period)
        df.index = df.index.tz_localize(None)
        df.to_csv(cache_file)
        time.sleep(1)
    
    # 抓配息
    if os.path.exists(div_cache):
        div_df = pd.read_csv(div_cache, index_col=0, parse_dates=True)
    else:
        print(f"  抓取 {etf_id} 配息紀錄...")
        ticker = yf.Ticker(ticker_id)
        div_df = ticker.dividends
        if not div_df.empty:
            div_df.index = div_df.index.tz_localize(None)
        div_df.to_csv(div_cache)
        time.sleep(1)
    
    return df, div_df


if __name__ == "__main__":
    all_data = {}
    all_divs = {}
    
    for etf_id in ETF_LIST:
        print(f"\n{'='*50}")
        print(f"ETF: {etf_id} {ETF_LIST[etf_id]['name']}")
        print(f"{'='*50}")
        
        df, div_df = fetch_etf_data(etf_id)
        all_data[etf_id] = df
        all_divs[etf_id] = div_df
        
        print(f"  K線: {len(df)} 筆 ({df.index[0].date()} ~ {df.index[-1].date()})")
        if not div_df.empty:
            # div_df 可能是 Series 或 DataFrame，統一處理
            div_series = div_df.iloc[:, 0] if isinstance(div_df, pd.DataFrame) else div_df
            print(f"  配息次數: {len(div_series)}")
            print(f"  配息金額: {div_series.values}")
            print(f"  配息日期:")
            for date, amt in div_series.items():
                print(f"    {date.date()}: {amt:.4f}")
        else:
            print("  無配息紀錄")
    
    # 儲存合併資料
    print("\n" + "="*50)
    print("配息交錯分析")
    print("="*50)
    for etf_id, div_df in all_divs.items():
        if not div_df.empty:
            div_series = div_df.iloc[:, 0] if isinstance(div_df, pd.DataFrame) else div_df
            months = [d.month for d in div_series.index]
            print(f"{etf_id} {ETF_LIST[etf_id]['name']:12s} 配息月份: {sorted(set(months))} 共{len(div_series)}次")
        else:
            print(f"{etf_id} {ETF_LIST[etf_id]['name']:12s} 無配息紀錄")
