"""
單檔 ETF 策略優化回測框架
==========================
對每檔 ETF 跑多種參數組合，找出勝率最高 + 報酬最大的進出場策略

策略模板（每股獨立優化）：
  進場：配息前 N 天內 + 站上 MA_X + RSI < Y
  加碼：配息後 M 天內 + RSI < Z
  部分出場：RSI > A + 跌破 MA_B
  完全出場：跌破季線 P% 或 RSI > C + 連 N 黑K

可調參數：
  entry_days_before: [5, 10, 15, 20, 25]
  entry_ma: [5, 10, 20]
  entry_rsi_max: [50, 55, 60, 65]
  add_days_after: [5, 10, 15, 20]
  add_rsi_max: [35, 40, 45, 50]
  partial_rsi: [70, 75, 80, 85]
  sell_ma: [5, 10]
  stop_loss_pct: [2, 3, 5]  (跌破季線 %)
"""
import yfinance as yf
import pandas as pd
import numpy as np
import os
import time
import json
from itertools import product

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache", "etf_prices")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

ETF_INFO = {
    "0050": {"name": "元大台灣50", "suffix": ".TW"},
    "0056": {"name": "元大高股息", "suffix": ".TW"},
    "00878": {"name": "國泰永續高股息", "suffix": ".TW"},
    "00927": {"name": "群益台灣精選高息", "suffix": ".TW"},
}

INITIAL_CAPITAL = 1_000_000
FEE_RATE = 0.001425
ETF_TAX_RATE = 0.001
BUY_RATIO = 0.65
ADD_RATIO = 0.50
PARTIAL_SELL_RATIO = 0.50


def load_data(etf_id):
    """從快取載入價量 + 配息"""
    price_file = os.path.join(CACHE_DIR, f"{etf_id}_5y.csv")
    div_file = os.path.join(CACHE_DIR, f"{etf_id}_dividends.csv")

    df = pd.read_csv(price_file, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    div_df = pd.read_csv(div_file, index_col=0, parse_dates=True)
    if div_df.index.tz is not None:
        div_df.index = div_df.index.tz_localize(None)
    divs = div_df.iloc[:, 0] if isinstance(div_df, pd.DataFrame) else div_df

    return df, divs


def calc_indicators(df):
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    df["is_black"] = df["Close"] < df["Open"]
    df["black_streak"] = df["is_black"].rolling(3).sum()
    return df


def backtest(df, divs, params):
    """
    用指定參數跑回測
    params: dict with keys:
        entry_days_before, entry_ma, entry_rsi_max, entry_rsi_min,
        add_days_after, add_rsi_max, add_rsi_min,
        partial_rsi, sell_ma,
        stop_loss_pct, extreme_rsi, extreme_black
    """
    p = params
    df = calc_indicators(df)
    ma_col = f"MA{p['entry_ma']}"
    sell_ma_col = f"MA{p['sell_ma']}"

    div_dates = sorted(divs.index.tolist()) if not divs.empty else []

    capital = INITIAL_CAPITAL
    position = 0
    cost_price = 0
    has_added = False
    trades = []
    daily_values = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        close = row["Close"]
        current_date = df.index[i]

        # 每日市值
        total_val = capital + (position * close if position > 0 else 0)
        daily_values.append({"date": current_date, "value": round(total_val, 0)})

        # ── 部分出場 ──
        if position > 0 and prev["RSI"] > p["partial_rsi"] and close < prev[sell_ma_col]:
            sell_shares = int(position * PARTIAL_SELL_RATIO / 1000) * 1000
            if sell_shares >= 1000:
                proceeds = sell_shares * close * (1 - FEE_RATE - ETF_TAX_RATE)
                capital += proceeds
                position -= sell_shares
                pnl = (close - cost_price) * sell_shares
                trades.append({"action": "PARTIAL_SELL", "price": close, "shares": sell_shares, "pnl": pnl,
                               "reason": f"RSI={prev['RSI']:.0f}>{p['partial_rsi']}+跌破MA{p['sell_ma']}"})

        # ── 完全出場 ──
        if position > 0 and i >= 3:
            below_ma60_3d = all(
                df.iloc[i - j]["Close"] < df.iloc[i - j]["MA60"] * (1 - p["stop_loss_pct"] / 100)
                for j in range(3)
            )
            extreme = prev["RSI"] > p["extreme_rsi"] and prev["black_streak"] >= p["extreme_black"]
            if below_ma60_3d or extreme:
                proceeds = position * close * (1 - FEE_RATE - ETF_TAX_RATE)
                capital += proceeds
                pnl = proceeds - cost_price * position
                trades.append({"action": "FULL_SELL", "price": close, "shares": position, "pnl": pnl,
                               "reason": f"跌破季線{p['stop_loss_pct']}%連3天" if below_ma60_3d else f"RSI>{p['extreme_rsi']}+連{p['extreme_black']}黑K"})
                position = 0
                cost_price = 0
                has_added = False

        # ── 進場 ──
        if position == 0 and div_dates:
            future = [d for d in div_dates if d > current_date]
            if future:
                days_to = (future[0] - current_date).days
                if 3 <= days_to <= p["entry_days_before"]:
                    if (prev["Close"] > prev[ma_col] and
                        p["entry_rsi_min"] < prev["RSI"] < p["entry_rsi_max"] and
                        capital > 10000):
                        buy_amt = capital * BUY_RATIO
                        shares = int(buy_amt / (close * (1 + FEE_RATE)))
                        if shares > 0:
                            cost = shares * close * (1 + FEE_RATE)
                            capital -= cost
                            position = shares
                            cost_price = close
                            has_added = False
                            trades.append({"action": "BUY", "price": close, "shares": shares,
                                           "reason": f"配息前{days_to}天+站上MA{p['entry_ma']}+RSI={prev['RSI']:.0f}"})

        # ── 加碼 ──
        if position > 0 and not has_added and div_dates:
            past = [d for d in div_dates if d < current_date]
            if past:
                days_after = (current_date - past[-1]).days
                if 3 <= days_after <= p["add_days_after"]:
                    if (p["add_rsi_min"] < prev["RSI"] < p["add_rsi_max"] and capital > 10000):
                        add_amt = capital * ADD_RATIO
                        shares = int(add_amt / (close * (1 + FEE_RATE)))
                        if shares > 0:
                            cost = shares * close * (1 + FEE_RATE)
                            capital -= cost
                            total_cost = cost_price * position + close * shares
                            position += shares
                            cost_price = total_cost / position
                            has_added = True
                            trades.append({"action": "ADD", "price": close, "shares": shares,
                                           "reason": f"配息後{days_after}天+RSI={prev['RSI']:.0f}"})

    # 最終計算
    final_close = df.iloc[-1]["Close"]
    final_value = capital + (position * final_close if position > 0 else 0)

    sell_trades = [t for t in trades if t["action"] in ("PARTIAL_SELL", "FULL_SELL")]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    losses = [t for t in sell_trades if t["pnl"] <= 0]

    total_ret = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0

    dv = pd.DataFrame(daily_values).set_index("date")
    dv["peak"] = dv["value"].cummax()
    dv["dd"] = (dv["value"] - dv["peak"]) / dv["peak"] * 100
    max_dd = abs(dv["dd"].min()) if len(dv) > 0 else 0

    years = len(df) / 252
    annual = ((final_value / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0

    # 綜合評分：勝率*0.4 + 年化報酬*0.3 - 回撤*0.3（交易太少扣分）
    trade_penalty = 0.5 if len(sell_trades) < 5 else 1.0
    score = (win_rate * 0.4 + max(annual, 0) * 0.3 - max_dd * 0.3) * trade_penalty

    return {
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_ret, 2),
        "annual_return_pct": round(annual, 2),
        "win_rate": round(win_rate, 1),
        "num_trades": len(sell_trades),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": round(avg_win, 0),
        "avg_loss": round(avg_loss, 0),
        "max_drawdown_pct": round(max_dd, 2),
        "score": round(score, 2),
        "num_all_trades": len(trades),
    }


def optimize_etf(etf_id, max_combos=500):
    """
    對單檔 ETF 跑參數組合優化
    """
    info = ETF_INFO[etf_id]
    print(f"\n{'='*70}")
    print(f"🎯 優化 {etf_id} {info['name']}")
    print(f"{'='*70}")

    df, divs = load_data(etf_id)
    print(f"資料: {len(df)} 筆 ({df.index[0].date()} ~ {df.index[-1].date()}) | 配息: {len(divs)} 次")

    # 參數搜尋空間（精簡版 — 先跑粗搜）
    param_grid = {
        "entry_days_before": [10, 15, 20],
        "entry_ma": [5, 20],
        "entry_rsi_max": [55, 65],
        "entry_rsi_min": [30],
        "add_days_after": [10, 15],
        "add_rsi_max": [40, 50],
        "add_rsi_min": [25],
        "partial_rsi": [75, 80],
        "sell_ma": [5],
        "stop_loss_pct": [3],
        "extreme_rsi": [85],
        "extreme_black": [3],
    }

    # 生成組合
    keys = list(param_grid.keys())
    all_combos = list(product(*[param_grid[k] for k in keys]))

    print(f"參數組合數: {len(all_combos)}")

    results = []
    for i, combo in enumerate(all_combos):
        params = dict(zip(keys, combo))
        r = backtest(df, divs, params)
        r["params"] = params
        results.append(r)

        if (i + 1) % 100 == 0:
            print(f"  進度: {i+1}/{len(all_combos)}")

    # 找出最佳：優先勝率 > 70% 且報酬最高
    valid = [r for r in results if r["num_trades"] >= 3]  # 至少3次交易

    if not valid:
        valid = results

    # 排序：先看勝率高的，再看報酬
    # 但也要避免過少交易（不夠樣本）
    by_winrate = sorted(valid, key=lambda x: (-x["win_rate"], -x["annual_return_pct"]))
    by_return = sorted(valid, key=lambda x: (-x["annual_return_pct"], -x["win_rate"]))
    by_score = sorted(valid, key=lambda x: -x["score"])

    print(f"\n{'='*70}")
    print(f"📊 {etf_id} 最佳策略排名")
    print(f"{'='*70}")

    print(f"\n--- TOP 5 依綜合評分（勝率+報酬-回撤）---")
    for rank, r in enumerate(by_score[:5], 1):
        p = r["params"]
        print(f"  #{rank} 勝率={r['win_rate']}% 報酬={r['total_return_pct']}% 年化={r['annual_return_pct']}% "
              f"回撤={r['max_drawdown_pct']}% 交易={r['num_trades']}次 評分={r['score']}")
        print(f"      進場: 配息前{p['entry_days_before']}天+MA{p['entry_ma']}+RSI<{p['entry_rsi_max']}")
        print(f"      加碼: 配息後{p['add_days_after']}天+RSI<{p['add_rsi_max']}")
        print(f"      出場: RSI>{p['partial_rsi']}+跌破MA{p['sell_ma']} | 季線-{p['stop_loss_pct']}%或RSI>{p['extreme_rsi']}")

    print(f"\n--- TOP 5 依勝率（至少5次交易）---")
    enough_trades = [r for r in by_winrate if r["num_trades"] >= 5]
    for rank, r in enumerate(enough_trades[:5], 1):
        p = r["params"]
        print(f"  #{rank} 勝率={r['win_rate']}% 報酬={r['total_return_pct']}% 年化={r['annual_return_pct']}% "
              f"交易={r['num_trades']}次 回撤={r['max_drawdown_pct']}%")

    # 存最佳結果
    best = by_score[0]
    best["etf_id"] = etf_id
    best["etf_name"] = info["name"]
    best["all_results_count"] = len(results)
    best["params"] = best.pop("params")

    out_file = os.path.join(RESULTS_DIR, f"{etf_id}_best.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2, default=str)

    # 也存 top 10
    top10 = by_score[:10]
    for r in top10:
        r["params"] = {k: v for k, v in r.get("params", {}).items()}
    top10_file = os.path.join(RESULTS_DIR, f"{etf_id}_top10.json")
    with open(top10_file, "w", encoding="utf-8") as f:
        json.dump(top10, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ 最佳結果已存到 {out_file}")
    print(f"✅ TOP10 已存到 {top10_file}")

    return best, by_score[:10]


if __name__ == "__main__":
    import sys
    etf = sys.argv[1] if len(sys.argv) > 1 else "all"

    if etf == "all":
        for eid in ETF_INFO:
            optimize_etf(eid, max_combos=300)
    else:
        optimize_etf(etf, max_combos=500)
