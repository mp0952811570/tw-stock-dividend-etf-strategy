"""
Step 3: 回測引擎 — 進場/加碼/出場策略

策略邏輯：
==========
進場條件（買入信號）：
  - 收盤價突破月線(20MA)且月線上彎
  - 且 RSI < 60（不是過熱區）

加碼條件：
  - 持有後回測到季線(60MA)附近反彈（觸碰季線 ±2% 範圍後收紅K）
  - 最多加碼 1 次

出場條件（賣出信號）：
  - 收盤價跌破季線(60MA)達 3% 以下 → 止損出場
  - 或 RSI > 80 且收盤跌破月線 → 獲利了結

回測參數：
  - 初始資金：100 萬
  - 每次買入：總資金的 50%（加碼後滿倉）
  - 手續費：0.1425%（買賣各一次）
  - 交易稅：0.3%（賣出時）
"""
import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
PRICE_CACHE_DIR = os.path.join(CACHE_DIR, "prices")
os.makedirs(PRICE_CACHE_DIR, exist_ok=True)

# ── 參數 ──────────────────────────────────────────────────
INITIAL_CAPITAL = 1_000_000  # 100萬
BUY_RATIO = 0.50              # 首次買入比例
ADD_RATIO = 0.40              # 加碼比例（剩餘資金的40%）
FEE_RATE = 0.001425           # 手續費
TAX_RATE = 0.003              # 證交稅
PERIOD = "5y"                 # 回測5年


def fetch_price_history(stock_id, stock_type="twse", force_refresh=False):
    """抓5年日K線，有快取"""
    cache_file = os.path.join(PRICE_CACHE_DIR, f"{stock_id}.csv")
    if os.path.exists(cache_file) and not force_refresh:
        mtime = os.path.getmtime(cache_file)
        if time.time() - mtime < 86400:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if len(df) > 100:  # 至少要有足夠的數據
                return df

    suffix = ".TW" if stock_type == "twse" else ".TWO"
    ticker = yf.Ticker(f"{stock_id}{suffix}")
    hist = ticker.history(period=PERIOD)

    if hist.empty or len(hist) < 60:
        return None

    hist = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
    hist.index = hist.index.tz_localize(None)
    hist.to_csv(cache_file)
    return hist


def calculate_indicators(df):
    """計算技術指標"""
    # 移動平均線
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    
    # 月線上彎判斷（今天的MA20 > 昨天的MA20）
    df["MA20_rising"] = df["MA20"] > df["MA20"].shift(1)

    # RSI（14日）
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # 季線 ±2% 帶
    df["MA60_upper"] = df["MA60"] * 1.02
    df["MA60_lower"] = df["MA60"] * 0.98

    return df


def backtest_single(df, stock_name=""):
    """
    回測一檔股票
    回傳交易紀錄 + 統計摘要
    """
    df = calculate_indicators(df)
    df = df.dropna(subset=["MA20", "MA60", "RSI"]).copy()
    df = df.reset_index(drop=True)

    capital = INITIAL_CAPITAL
    position = 0          # 持有股數
    buy_price = 0         # 成本價
    has_position = False
    has_added = False     # 是否已加碼
    trades = []           # 交易紀錄
    holdings_log = []     # 每日持倉市值紀錄

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        date = row.name if hasattr(row, 'name') else i
        close = row["Close"]
        high = row["High"]
        low = row["Low"]

        # ── 買入信號（無持倉時）──
        if not has_position:
            buy_signal = (
                prev["Close"] > prev["MA20"]           # 昨天收盤站上月線
                and prev["MA20_rising"]                  # 月線上彎
                and prev["RSI"] < 60                    # RSI未過熱
                and prev["Close"] > prev["MA60"]        # 站上季線（大趨勢向上）
            )
            if buy_signal:
                buy_amount = capital * BUY_RATIO
                shares = int(buy_amount / (close * (1 + FEE_RATE)) / 1000) * 1000
                if shares > 0:
                    cost = shares * close * (1 + FEE_RATE)
                    capital -= cost
                    position = shares
                    buy_price = close
                    has_position = True
                    trades.append({
                        "date": str(df.index[i].date()) if hasattr(df.index[i], 'date') else str(i),
                        "action": "BUY",
                        "price": round(close, 2),
                        "shares": shares,
                        "amount": round(cost, 0),
                        "reason": "月線上彎+站上季線+RSI<60",
                    })

        # ── 加碼信號（持倉但未加碼）──
        elif has_position and not has_added:
            # 回測到季線附近反彈：前一天觸碰季線帶、今天收紅K
            touched_ma60 = (
                prev["Low"] <= prev["MA60_upper"]
                and prev["Low"] >= prev["MA60_lower"]
            )
            bounce = row["Close"] > row["Open"]  # 今天收紅K
            still_uptrend = prev["Close"] > prev["MA60"]

            if touched_ma60 and bounce and still_uptrend:
                buy_amount = capital * ADD_RATIO
                shares = int(buy_amount / (close * (1 + FEE_RATE)) / 1000) * 1000
                if shares > 0:
                    cost = shares * close * (1 + FEE_RATE)
                    capital -= cost
                    # 更新平均成本
                    total_cost = buy_price * position + close * shares
                    position += shares
                    buy_price = total_cost / position
                    has_added = True
                    trades.append({
                        "date": str(df.index[i].date()) if hasattr(df.index[i], 'date') else str(i),
                        "action": "ADD",
                        "price": round(close, 2),
                        "shares": shares,
                        "amount": round(cost, 0),
                        "reason": "季線附近反彈加碼",
                    })

        # ── 賣出信號（持倉時）──
        if has_position:
            # 條件1：跌破季線達3%以上 → 止損
            stop_loss = close < (prev["MA60"] * 0.97)
            # 條件2：RSI過熱 + 跌破月線 → 獲利了結
            take_profit = (prev["RSI"] > 80) and (close < prev["MA20"])

            if stop_loss or take_profit:
                proceeds = position * close * (1 - FEE_RATE - TAX_RATE)
                capital += proceeds
                pnl = (close - buy_price) * position
                pnl_pct = ((close - buy_price) / buy_price) * 100
                trades.append({
                    "date": str(df.index[i].date()) if hasattr(df.index[i], 'date') else str(i),
                    "action": "SELL",
                    "price": round(close, 2),
                    "shares": position,
                    "amount": round(proceeds, 0),
                    "reason": "止損(跌破季線3%)" if stop_loss else "獲利了結(RSI>80+跌破月線)",
                    "pnl": round(pnl, 0),
                    "pnl_pct": round(pnl_pct, 2),
                })
                position = 0
                buy_price = 0
                has_position = False
                has_added = False

        # 紀錄每日總市值
        total_value = capital + (position * close if has_position else 0)
        holdings_log.append({
            "date": df.index[i],
            "total_value": round(total_value, 0),
            "close": close,
        })

    # ── 回測結束，如果還有持倉就用最後收盤算 ──
    if has_position:
        final_close = df.iloc[-1]["Close"]
        final_value = capital + position * final_close
        unrealized_pnl = (final_close - buy_price) * position
        unrealized_pct = ((final_close - buy_price) / buy_price) * 100
        trades.append({
            "date": str(df.index[-1].date()) if hasattr(df.index[-1], 'date') else "end",
            "action": "EOD_VAL",
            "price": round(final_close, 2),
            "shares": position,
            "unrealized_pnl": round(unrealized_pnl, 0),
            "unrealized_pct": round(unrealized_pct, 2),
        })
    else:
        final_value = capital

    # ── 統計 ──
    sell_trades = [t for t in trades if t["action"] == "SELL"]
    win_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]
    lose_trades = [t for t in sell_trades if t.get("pnl", 0) <= 0]

    total_return = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = np.mean([t["pnl"] for t in win_trades]) if win_trades else 0
    avg_loss = np.mean([t["pnl"] for t in lose_trades]) if lose_trades else 0

    # 最大回撤
    if holdings_log:
        hv = pd.DataFrame(holdings_log).set_index("date")
        hv["peak"] = hv["total_value"].cummax()
        hv["drawdown"] = (hv["total_value"] - hv["peak"]) / hv["peak"] * 100
        max_drawdown = abs(hv["drawdown"].min())
    else:
        max_drawdown = 0

    return {
        "stock_id": stock_name,
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_return, 2),
        "num_trades": len(sell_trades),
        "win_rate": round(win_rate, 1),
        "wins": len(win_trades),
        "losses": len(lose_trades),
        "avg_win": round(avg_win, 0),
        "avg_loss": round(avg_loss, 0),
        "max_drawdown_pct": round(max_drawdown, 2),
        "trades": trades,
    }


def run_backtest(stock_df):
    """跑全部股票的回測"""
    results = []
    total = len(stock_df)

    for idx, row in stock_df.iterrows():
        sid = row["stock_id"]
        sname = row["stock_name"]
        stype = row.get("type", "twse")

        print(f"[{idx+1}/{total}] {sid} {sname}...", end=" ")

        hist = fetch_price_history(sid, stype)
        if hist is None or len(hist) < 60:
            print("❌ 資料不足")
            continue

        result = backtest_single(hist, stock_name=f"{sid} {sname}")
        result["stock_id"] = sid
        result["stock_name"] = sname
        result["industry"] = row.get("industry", "")
        result["dividend_yield"] = row.get("dividend_yield", None)
        results.append(result)

        wr = result["win_rate"]
        ret = result["total_return_pct"]
        dd = result["max_drawdown_pct"]
        nt = result["num_trades"]
        print(f"勝率={wr}% 報酬={ret}% 回撤={dd}% 交易次數={nt}")

    return results


if __name__ == "__main__":
    from filter_stocks import filter_dividend_stocks
    import sys

    print("=" * 70)
    print("台股高殖利率存股回測系統")
    print("=" * 70)
    print()

    # 篩選
    df = filter_dividend_stocks(min_yield=4.0, max_yield=15.0, top_n=30)

    print()
    print("開始回測...")
    print("-" * 70)
    results = run_backtest(df)

    # 輸出摘要
    print()
    print("=" * 70)
    print("回測結果排名（依總報酬排序）")
    print("=" * 70)

    summary = []
    for r in results:
        summary.append({
            "股票": f"{r['stock_id']} {r['stock_name']}",
            "殖利率%": r["dividend_yield"],
            "總報酬%": r["total_return_pct"],
            "勝率%": r["win_rate"],
            "交易次數": r["num_trades"],
            "最大回撤%": r["max_drawdown_pct"],
            "獲利次數": r["wins"],
            "虧損次數": r["losses"],
        })

    result_df = pd.DataFrame(summary)
    result_df = result_df.sort_values("總報酬%", ascending=False)
    print(result_df.to_string(index=False))

    # 存檔
    OUT_FILE = os.path.join(os.path.dirname(__file__), "backtest_results.json")
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 詳細回測結果已存到 {OUT_FILE}")
