"""
台股高息ETF輪動回測引擎
======================
標的：0050 / 0056 / 00878 / 00927
期間：過去5年
初始資金：100萬

策略核心：配息事件驅動 + 技術面確認
=====================================

【進場訊號】
  1. 配息前 15 個交易日內進場（享受填息行情）
  2. 確認條件：收盤站上 20MA（月線之上才買）

【加碼訊號】
  1. 除息後 5~15 個交易日內加碼（利用貼息時的低點承接）
  2. 確認條件：RSI < 50（尚未過熱，有上漲空間）
  3. 最多加碼 1 次，加碼金額 = 剩餘資金的 50%

【部分出場訊號】
  1. RSI > 80（嚴重超買）→ 賣出 50% 持倉（落袋為安）
  2. 確認條件：收盤價同時跌破 5MA（短線轉弱確認）

【完全出場訊號】
  1. 收盤跌破 60MA（季線）達 3 個交易日 → 趨勢反轉，全數出場
  2. 或 RSI > 85 且連續 3 天收黑K → 極端過熱出場

資金輪動邏輯：
  - 四支ETF配息月份交錯：1月(0050/0056/00927) 2月(00878) 4月(0056/00927) 5月(00878)...
  - 配息前買入 → 配息後視情況加碼或等待下一檔配息前進場
  - 資金優先分配到「最接近配息日」的ETF
"""
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import time
from datetime import datetime, timedelta

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
PRICE_CACHE = os.path.join(CACHE_DIR, "etf_prices")

ETF_INFO = {
    "0050": {"name": "元大台灣50", "suffix": ".TW", "div_months": [1, 7]},
    "0056": {"name": "元大高股息", "suffix": ".TW", "div_months": [1, 4, 7, 10]},
    "00878": {"name": "國泰永續高股息", "suffix": ".TW", "div_months": [2, 5, 8, 11]},
    "00927": {"name": "群益台灣精選高息", "suffix": ".TW", "div_months": [1, 4, 7, 10]},
}

# ── 回測參數 ──
INITIAL_CAPITAL = 1_000_000
FEE_RATE = 0.001425       # 手續費 0.1425%
TAX_RATE = 0.003           # 證交稅 0.3%（ETF為0.1%）
ETF_TAX_RATE = 0.001       # ETF 賣出稅 0.1%
ENTRY_DAYS_BEFORE_DIV = 15  # 配息前15天內進場
ADD_DAYS_AFTER_DIV = 15     # 配息後15天內加碼
ADD_RATIO = 0.50            # 加碼比例（剩餘資金的50%）
PARTIAL_SELL_RATIO = 0.50   # 部分出場賣50%


def load_price(etf_id):
    """載入快取的價量資料"""
    f = os.path.join(PRICE_CACHE, f"{etf_id}_5y.csv")
    df = pd.read_csv(f, index_col=0, parse_dates=True)
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    return df


def load_dividends(etf_id):
    """載入配息紀錄"""
    f = os.path.join(PRICE_CACHE, f"{etf_id}_dividends.csv")
    df = pd.read_csv(f, index_col=0, parse_dates=True)
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    # 可能是 Series 或 DataFrame，統一轉 Series
    if isinstance(df, pd.DataFrame):
        s = df.iloc[:, 0]
    else:
        s = df
    return s


def calc_indicators(df):
    """計算技術指標"""
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    # RSI 14
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # 連續黑K
    df["is_red"] = df["Close"] > df["Open"]
    df["is_black"] = df["Close"] < df["Open"]
    df["black_streak"] = df["is_black"].rolling(3).sum()

    return df


def get_dividend_dates(etf_id, start_date, end_date):
    """取得某 ETF 在日期範圍內的配息日"""
    divs = load_dividends(etf_id)
    mask = (divs.index >= start_date) & (divs.index <= end_date)
    return divs[mask]


def find_ex_div_date(price_df, div_date):
    """
    找到實際除息日：配息公告日附近，找到成交量異常或價格跳空下跌的日子
    簡化處理：用 yfinance 的 dividends 日期作為除息日近似
    """
    # yfinance 的 dividends 日期就是除息日
    return div_date


def run_backtest():
    """主回測函數"""
    print("=" * 70)
    print("台股高息ETF配息輪動回測")
    print("=" * 70)

    # ── 載入所有ETF資料 ──
    etf_data = {}
    div_data = {}

    for etf_id in ETF_INFO:
        df = load_price(etf_id)
        df = calc_indicators(df)
        etf_data[etf_id] = df

        divs = get_dividend_dates(etf_id, df.index[0], df.index[-1])
        div_data[etf_id] = divs

        print(f"{etf_id} {ETF_INFO[etf_id]['name']:12s} | "
              f"K線: {len(df)} 筆 | 配息: {len(divs)} 次 | "
              f"期間: {df.index[0].date()} ~ {df.index[-1].date()}")

    # ── 合併所有價格到一個統一日期軸 ──
    all_dates = sorted(set().union(*[set(df.index) for df in etf_data.values()]))
    print(f"\n統一日期軸: {len(all_dates)} 個交易日")

    # ── 準備每個ETF的配息日列表 ──
    all_div_events = []
    for etf_id, divs in div_data.items():
        for div_date, div_amt in divs.items():
            all_div_events.append({
                "date": div_date,
                "etf": etf_id,
                "amount": div_amt,
            })
    all_div_events.sort(key=lambda x: x["date"])

    # 建立配息日查找表：etf_id -> sorted list of div dates
    div_lookup = {etf_id: sorted(divs.index.tolist()) for etf_id, divs in div_data.items()}

    # ── 回測狀態 ──
    capital = INITIAL_CAPITAL  # 現金
    positions = {}  # {etf_id: {"shares": int, "cost_price": float, "added": bool, "entry_date": date}}
    trades = []      # 交易紀錄
    daily_values = []  # 每日總市值

    # ── 逐日回測 ──
    for i, current_date in enumerate(all_dates):
        if i < 60:  # 跳過前60天（需要MA60）
            continue

        # 取得每個ETF在當日的數據
        daily_prices = {}
        daily_prev = {}
        for etf_id, df in etf_data.items():
            if current_date in df.index:
                idx = df.index.get_loc(current_date)
                if idx >= 1:
                    daily_prices[etf_id] = df.iloc[idx]
                    daily_prev[etf_id] = df.iloc[idx - 1]

        if not daily_prices:
            continue

        # ── 計算每日總市值 ──
        total_value = capital
        for etf_id, pos in positions.items():
            if etf_id in daily_prices:
                total_value += pos["shares"] * daily_prices[etf_id]["Close"]
        daily_values.append({
            "date": current_date,
            "value": round(total_value, 0),
        })

        # ── 策略1：檢查部分出場（RSI > 80 + 跌破5MA）──
        for etf_id in list(positions.keys()):
            if etf_id not in daily_prices:
                continue
            row = daily_prices[etf_id]
            prev = daily_prev.get(etf_id)
            if prev is None:
                continue

            # 部分出場：RSI > 80 且 跌破5MA → 賣50%
            if prev["RSI"] > 80 and row["Close"] < row["MA5"]:
                pos = positions[etf_id]
                sell_shares = int(pos["shares"] * PARTIAL_SELL_RATIO / 1000) * 1000
                if sell_shares >= 1000:
                    proceeds = sell_shares * row["Close"] * (1 - FEE_RATE - ETF_TAX_RATE)
                    capital += proceeds
                    pos["shares"] -= sell_shares
                    realized_pnl = (row["Close"] - pos["cost_price"]) * sell_shares
                    trades.append({
                        "date": str(current_date.date()),
                        "etf": etf_id,
                        "action": "PARTIAL_SELL",
                        "price": round(row["Close"], 2),
                        "shares": sell_shares,
                        "amount": round(proceeds, 0),
                        "reason": f"RSI={prev['RSI']:.0f}過熱+跌破5MA部分出場",
                        "pnl": round(realized_pnl, 0),
                    })
                    if pos["shares"] <= 0:
                        del positions[etf_id]

        # ── 策略2：完全出場（連3天低於季線3% 或 RSI>85+連3黑K）──
        for etf_id in list(positions.keys()):
            if etf_id not in daily_prices or etf_id not in daily_prev:
                continue

            df = etf_data[etf_id]
            idx = df.index.get_loc(current_date)
            if idx < 4:
                continue

            row = daily_prices[etf_id]
            # 檢查連3天收盤 < MA60 * 0.97
            below_ma60_3days = all(
                df.iloc[idx - j]["Close"] < df.iloc[idx - j]["MA60"] * 0.97
                for j in range(3)
            )
            # RSI > 85 + 連3黑K
            extreme_overheat = prev["RSI"] > 85 and prev["black_streak"] >= 3

            if below_ma60_3days or extreme_overheat:
                pos = positions[etf_id]
                sell_shares = pos["shares"]
                if sell_shares > 0:
                    proceeds = sell_shares * row["Close"] * (1 - FEE_RATE - ETF_TAX_RATE)
                    capital += proceeds
                    cost_total = pos["cost_price"] * sell_shares
                    realized_pnl = proceeds - cost_total
                    trades.append({
                        "date": str(current_date.date()),
                        "etf": etf_id,
                        "action": "FULL_SELL",
                        "price": round(row["Close"], 2),
                        "shares": sell_shares,
                        "amount": round(proceeds, 0),
                        "reason": "連3天跌破季線3%" if below_ma60_3days else "RSI>85+連3黑K極端過熱",
                        "pnl": round(realized_pnl, 0),
                    })
                    del positions[etf_id]

        # ── 策略3：配息前進場 ──
        # 對每個ETF，檢查是否在配息前15天窗口內
        for etf_id in ETF_INFO:
            if etf_id in positions:
                continue  # 已持有就跳過
            if etf_id not in daily_prices:
                continue

            div_dates = div_lookup.get(etf_id, [])
            # 找最近的未來配息日
            future_divs = [d for d in div_dates if d > current_date]
            if not future_divs:
                continue

            next_div = future_divs[0]
            days_to_div = (next_div - current_date).days

            # 在配息前 5~15 個交易日窗口內進場
            if 5 <= days_to_div <= 25:
                row = daily_prices[etf_id]
                prev = daily_prev.get(etf_id)
                if prev is None:
                    continue

                # 確認條件：收盤站上 20MA + RSI < 65
                entry_ok = (prev["Close"] > prev["MA20"] and prev["RSI"] < 65 and prev["RSI"] > 30)

                if entry_ok and capital > 10000:
                    # 資金分配：如果有其他持倉就買50%，否則買70%
                    buy_ratio = 0.50 if positions else 0.70
                    buy_amount = capital * buy_ratio
                    # ETF 可以零股交易，用整數股
                    shares = int(buy_amount / (row["Close"] * (1 + FEE_RATE)))
                    if shares > 0:
                        cost = shares * row["Close"] * (1 + FEE_RATE)
                        capital -= cost
                        positions[etf_id] = {
                            "shares": shares,
                            "cost_price": row["Close"],
                            "added": False,
                            "entry_date": current_date,
                        }
                        trades.append({
                            "date": str(current_date.date()),
                            "etf": etf_id,
                            "action": "BUY",
                            "price": round(row["Close"], 2),
                            "shares": shares,
                            "amount": round(cost, 0),
                            "reason": f"配息前{days_to_div}天進場(20MA上+RSI={prev['RSI']:.0f})",
                        })

        # ── 策略4：配息後加碼 ──
        for etf_id in list(positions.keys()):
            if etf_id not in daily_prices:
                continue
            pos = positions[etf_id]
            if pos["added"]:
                continue  # 已加碼過

            div_dates = div_lookup.get(etf_id, [])
            # 找最近的過去配息日
            past_divs = [d for d in div_dates if d < current_date]
            if not past_divs:
                continue

            last_div = past_divs[-1]
            days_after_div = (current_date - last_div).days

            # 配息後 5~15 天內加碼
            if 5 <= days_after_div <= 20:
                row = daily_prices[etf_id]
                prev = daily_prev.get(etf_id)
                if prev is None:
                    continue

                # 確認條件：RSI < 50（未過熱可承接）
                add_ok = prev["RSI"] < 50 and prev["RSI"] > 25

                if add_ok and capital > 10000:
                    add_amount = capital * ADD_RATIO
                    shares = int(add_amount / (row["Close"] * (1 + FEE_RATE)))
                    if shares > 0:
                        cost = shares * row["Close"] * (1 + FEE_RATE)
                        capital -= cost
                        # 更新平均成本
                        total_cost = pos["cost_price"] * pos["shares"] + row["Close"] * shares
                        pos["shares"] += shares
                        pos["cost_price"] = total_cost / pos["shares"]
                        pos["added"] = True
                        trades.append({
                            "date": str(current_date.date()),
                            "etf": etf_id,
                            "action": "ADD",
                            "price": round(row["Close"], 2),
                            "shares": shares,
                            "amount": round(cost, 0),
                            "reason": f"配息後{days_after_div}天加碼(RSI={prev['RSI']:.0f})",
                        })

    # ── 回測結束，計算最終市值 ──
    final_date = all_dates[-1]
    final_value = capital
    for etf_id, pos in positions.items():
        df = etf_data[etf_id]
        if final_date in df.index:
            final_price = df.loc[final_date, "Close"]
        else:
            final_price = df.iloc[-1]["Close"]
        final_value += pos["shares"] * final_price

    # ── 統計 ──
    sell_trades = [t for t in trades if t["action"] in ("PARTIAL_SELL", "FULL_SELL")]
    win_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]
    lose_trades = [t for t in sell_trades if t.get("pnl", 0) <= 0]

    total_return = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = np.mean([t["pnl"] for t in win_trades]) if win_trades else 0
    avg_loss = np.mean([t["pnl"] for t in lose_trades]) if lose_trades else 0

    # 最大回撤
    dv = pd.DataFrame(daily_values).set_index("date")
    dv["peak"] = dv["value"].cummax()
    dv["dd"] = (dv["value"] - dv["peak"]) / dv["peak"] * 100
    max_dd = abs(dv["dd"].min())

    # 年化報酬
    years = len(all_dates) / 252
    annual_return = ((final_value / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0

    # 個別ETF統計
    per_etf = {}
    for etf_id in ETF_INFO:
        etf_trades = [t for t in sell_trades if t["etf"] == etf_id]
        etf_wins = [t for t in etf_trades if t.get("pnl", 0) > 0]
        per_etf[etf_id] = {
            "name": ETF_INFO[etf_id]["name"],
            "sells": len(etf_trades),
            "wins": len(etf_wins),
            "win_rate": round(len(etf_wins) / len(etf_trades) * 100, 1) if etf_trades else 0,
        }

    print()
    print("=" * 70)
    print("📊 回測結果摘要")
    print("=" * 70)
    print(f"初始資金:     {INITIAL_CAPITAL:>14,}")
    print(f"最終市值:     {round(final_value):>14,}")
    print(f"總報酬率:     {round(total_return, 2):>13}%")
    print(f"年化報酬率:   {round(annual_return, 2):>13}%")
    print(f"最大回撤:     {round(max_dd, 2):>13}%")
    print(f"總交易次數:   {len(sell_trades):>14}")
    print(f"獲利次數:     {len(win_trades):>14}")
    print(f"虧損次數:     {len(lose_trades):>14}")
    print(f"勝率:         {round(win_rate, 1):>13}%")
    print(f"平均獲利:     {round(avg_win):>14,}")
    print(f"平均虧損:     {round(avg_loss):>14,}")
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    print(f"盈虧比:       {round(rr, 2):>14}")
    print()
    print("─ 個別ETF表現 ─")
    for etf_id, stats in per_etf.items():
        print(f"  {etf_id} {stats['name']:12s} | 賣出{stats['sells']}次 | 勝率{stats['win_rate']}%")

    # 存結果
    result = {
        "initial_capital": INITIAL_CAPITAL,
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "total_trades": len(sell_trades),
        "wins": len(win_trades),
        "losses": len(lose_trades),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 0),
        "avg_loss": round(avg_loss, 0),
        "profit_factor": round(rr, 2),
        "per_etf": per_etf,
        "trades": trades,
        "daily_values": [{"date": str(d["date"].date()), "value": d["value"]} for d in daily_values],
    }

    out_file = os.path.join(os.path.dirname(__file__), "backtest_etf_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 詳細結果已存到 {out_file}")
    print(f"✅ 交易紀錄共 {len(trades)} 筆")

    # 打印前20筆交易
    print()
    print("─ 前20筆交易紀錄 ─")
    for t in trades[:20]:
        print(f"  {t['date']} | {t['etf']} | {t['action']:12s} | "
              f"價={t.get('price',0):>7} | 股數={t.get('shares',0):>6} | {t.get('reason','')}")

    return result


if __name__ == "__main__":
    result = run_backtest()
