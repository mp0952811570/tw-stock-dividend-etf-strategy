"""
台股高息ETF 四檔獨立策略回測儀表板（Streamlit Cloud 版）
每檔 ETF 有各自獨立優化的進出場策略
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import json
import os
import time

st.set_page_config(page_title="台股高息ETF獨立策略回測", page_icon="📊", layout="wide")

COLORS = {
    "bg": "#1a1f2e", "card": "#222838", "text": "#e8e8e8",
    "gold": "#f0b90b", "orange": "#ff9f43", "red": "#e74c3c", "green": "#27ae60",
    "blue_gray": "#576574", "purple": "#a55eea",
}

ETF_INFO = {
    "0050": {"name": "元大台灣50", "suffix": ".TW", "color": "#f0b90b"},
    "0056": {"name": "元大高股息", "suffix": ".TW", "color": "#ff9f43"},
    "00878": {"name": "國泰永續高股息", "suffix": ".TW", "color": "#e74c3c"},
    "00927": {"name": "群益台灣精選高息", "suffix": ".TW", "color": "#27ae60"},
}

# ── Best strategies from optimization ──
BEST_STRATEGIES = {
    "0050": {
        "entry_days": 20, "entry_ma": 20, "entry_rsi_max": 65,
        "add_days": 10, "add_rsi_max": 50,
        "partial_rsi": 75, "sell_ma": 5,
        "stop_loss_pct": 3, "extreme_rsi": 85, "extreme_black": 3,
        "win_rate": 92.9, "total_return": 13.36, "annual": 2.63,
        "max_dd": 5.48, "trades": 14, "wins": 13, "losses": 1,
    },
    "0056": {
        "entry_days": 15, "entry_ma": 20, "entry_rsi_max": 55,
        "add_days": 10, "add_rsi_max": 50,
        "partial_rsi": 80, "sell_ma": 5,
        "stop_loss_pct": 3, "extreme_rsi": 85, "extreme_black": 3,
        "win_rate": 100.0, "total_return": 26.63, "annual": 5.02,
        "max_dd": 4.08, "trades": 12, "wins": 12, "losses": 0,
    },
    "00878": {
        "entry_days": 15, "entry_ma": 20, "entry_rsi_max": 65,
        "add_days": 10, "add_rsi_max": 40,
        "partial_rsi": 75, "sell_ma": 5,
        "stop_loss_pct": 3, "extreme_rsi": 85, "extreme_black": 3,
        "win_rate": 87.9, "total_return": 2.65, "annual": 0.54,
        "max_dd": 9.95, "trades": 33, "wins": 29, "losses": 4,
    },
    "00927": {
        "entry_days": 10, "entry_ma": 20, "entry_rsi_max": 55,
        "add_days": 10, "add_rsi_max": 40,
        "partial_rsi": 80, "sell_ma": 5,
        "stop_loss_pct": 3, "extreme_rsi": 85, "extreme_black": 3,
        "win_rate": 100.0, "total_return": 78.92, "annual": 21.25,
        "max_dd": 8.35, "trades": 9, "wins": 9, "losses": 0,
    },
}

INITIAL_CAPITAL = 1_000_000
FEE_RATE = 0.001425
ETF_TAX_RATE = 0.001
BUY_RATIO = 0.65
ADD_RATIO = 0.50
PARTIAL_SELL_RATIO = 0.50

st.markdown(f"""
<style>
    .stApp {{ background-color: {COLORS['bg']}; }}
    .main .block-container {{ padding-top: 1.5rem; }}
    .metric-card {{ background-color: {COLORS['card']}; border-radius: 10px; padding: 18px; border-left: 4px solid {COLORS['gold']}; }}
    .metric-card h3 {{ color: {COLORS['blue_gray']}; font-size: 0.8rem; margin: 0; text-transform: uppercase; letter-spacing: 1px; }}
    .metric-card .value {{ font-size: 1.7rem; font-weight: bold; margin-top: 5px; }}
    .metric-card .sub {{ color: {COLORS['blue_gray']}; font-size: 0.8rem; margin-top: 3px; }}
    .strategy-box {{ background-color: {COLORS['card']}; border-radius: 12px; padding: 20px; margin: 10px 0; border: 1px solid rgba(240,185,11,0.2); }}
    h1, h2, h3 {{ color: {COLORS['text']} !important; }}
    h1 {{ border-bottom: 2px solid {COLORS['gold']}; padding-bottom: 10px; }}
    th {{ background-color: {COLORS['card']} !important; color: {COLORS['gold']} !important; }}
    td {{ color: {COLORS['text']} !important; }}
</style>
""", unsafe_allow_html=True)


def calc_indicators(df, ma_entry=20, ma_sell=5):
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


def run_single_backtest(etf_id, strategy):
    """跑單檔回測"""
    info = ETF_INFO[etf_id]
    t = yf.Ticker(f"{etf_id}{info['suffix']}")
    df = t.history(period="5y")
    if df.empty:
        return None
    df.index = df.index.tz_localize(None)
    divs = t.dividends
    if not divs.empty:
        divs.index = divs.index.tz_localize(None)
    div_dates = sorted(divs.index.tolist()) if not divs.empty else []

    p = strategy
    ma_col = f"MA{p['entry_ma']}"
    sell_ma_col = f"MA{p['sell_ma']}"
    df = calc_indicators(df)

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

        total_val = capital + (position * close if position > 0 else 0)
        daily_values.append({"date": current_date, "value": round(total_val, 0)})

        # Partial sell
        if position > 0 and prev["RSI"] > p["partial_rsi"] and close < prev[sell_ma_col]:
            sell_shares = int(position * PARTIAL_SELL_RATIO / 1000) * 1000
            if sell_shares >= 1000:
                proceeds = sell_shares * close * (1 - FEE_RATE - ETF_TAX_RATE)
                capital += proceeds
                position -= sell_shares
                pnl = (close - cost_price) * sell_shares
                trades.append({"date": str(current_date.date()), "action": "PARTIAL_SELL",
                               "price": round(close, 2), "shares": sell_shares,
                               "reason": f"RSI={prev['RSI']:.0f}>{p['partial_rsi']}+跌破MA{p['sell_ma']}",
                               "pnl": round(pnl, 0)})

        # Full sell
        if position > 0 and i >= 3:
            below_ma60 = all(df.iloc[i-j]["Close"] < df.iloc[i-j]["MA60"] * (1 - p["stop_loss_pct"]/100) for j in range(3))
            extreme = prev["RSI"] > p["extreme_rsi"] and prev["black_streak"] >= p["extreme_black"]
            if below_ma60 or extreme:
                proceeds = position * close * (1 - FEE_RATE - ETF_TAX_RATE)
                capital += proceeds
                pnl = proceeds - cost_price * position
                trades.append({"date": str(current_date.date()), "action": "FULL_SELL",
                               "price": round(close, 2), "shares": position,
                               "reason": f"跌破季線{p['stop_loss_pct']}%連3天" if below_ma60 else f"RSI>{p['extreme_rsi']}+連{p['extreme_black']}黑K",
                               "pnl": round(pnl, 0)})
                position = 0
                cost_price = 0
                has_added = False

        # Buy
        if position == 0 and div_dates:
            future = [d for d in div_dates if d > current_date]
            if future:
                days_to = (future[0] - current_date).days
                if 3 <= days_to <= p["entry_days"]:
                    if prev["Close"] > prev[ma_col] and prev["RSI"] < p["entry_rsi_max"] and prev["RSI"] > 30 and capital > 10000:
                        buy_amt = capital * BUY_RATIO
                        shares = int(buy_amt / (close * (1 + FEE_RATE)))
                        if shares > 0:
                            cost = shares * close * (1 + FEE_RATE)
                            capital -= cost
                            position = shares
                            cost_price = close
                            has_added = False
                            trades.append({"date": str(current_date.date()), "action": "BUY",
                                           "price": round(close, 2), "shares": shares,
                                           "reason": f"配息前{days_to}天+MA{p['entry_ma']}+RSI={prev['RSI']:.0f}"})

        # Add
        if position > 0 and not has_added and div_dates:
            past = [d for d in div_dates if d < current_date]
            if past:
                days_after = (current_date - past[-1]).days
                if 3 <= days_after <= p["add_days"]:
                    if prev["RSI"] < p["add_rsi_max"] and prev["RSI"] > 25 and capital > 10000:
                        add_amt = capital * ADD_RATIO
                        shares = int(add_amt / (close * (1 + FEE_RATE)))
                        if shares > 0:
                            cost = shares * close * (1 + FEE_RATE)
                            capital -= cost
                            total_cost = cost_price * position + close * shares
                            position += shares
                            cost_price = total_cost / position
                            has_added = True
                            trades.append({"date": str(current_date.date()), "action": "ADD",
                                           "price": round(close, 2), "shares": shares,
                                           "reason": f"配息後{days_after}天+RSI={prev['RSI']:.0f}"})

    final_close = df.iloc[-1]["Close"]
    final_value = capital + (position * final_close if position > 0 else 0)

    sell_trades = [t for t in trades if t["action"] in ("PARTIAL_SELL", "FULL_SELL")]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    losses = [t for t in sell_trades if t["pnl"] <= 0]

    total_ret = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (len(wins) / len(sell_trades) * 100) if sell_trades else 0

    dv = pd.DataFrame(daily_values).set_index("date")
    dv["peak"] = dv["value"].cummax()
    dv["dd"] = (dv["value"] - dv["peak"]) / dv["peak"] * 100
    max_dd = abs(dv["dd"].min()) if len(dv) > 0 else 0

    years = len(df) / 252
    annual = ((final_value / INITIAL_CAPITAL) ** (1/years) - 1) * 100 if years > 0 else 0

    return {
        "final_value": round(final_value, 0),
        "total_return_pct": round(total_ret, 2),
        "annual_return_pct": round(annual, 2),
        "win_rate": round(win_rate, 1),
        "num_trades": len(sell_trades),
        "wins": len(wins),
        "losses": len(losses),
        "max_drawdown_pct": round(max_dd, 2),
        "trades": trades,
        "daily_values": [{"date": str(d["date"].date()) if hasattr(d["date"], "date") else str(d["date"]), "value": d["value"]} for d in daily_values],
        "price_dates": [str(d.date()) if hasattr(d, "date") else str(d) for d in df.index],
        "price_close": df["Close"].tolist(),
    }


# ── Title ──
st.markdown("""
# 📊 台股高息ETF 四檔獨立策略回測

每檔 ETF 有**獨立優化的進出場策略**，不混合運作。點選下方分頁查看各檔詳細回測結果~
""")

# ── Summary table ──
st.markdown("## 🏆 四檔策略總覽")

summary_data = []
for etf_id, s in BEST_STRATEGIES.items():
    summary_data.append({
        "ETF": etf_id, "名稱": ETF_INFO[etf_id]["name"],
        "勝率": f"{s['win_rate']}%", "總報酬": f"{s['total_return']}%",
        "年化": f"{s['annual']}%", "最大回撤": f"{s['max_dd']}%",
        "交易次數": s["trades"], "獲利": s["wins"], "虧損": s["losses"],
    })
st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True)

st.markdown("---")

# ── Per-ETF tabs ──
tab0050, tab0056, tab00878, tab00927 = st.tabs(["📊 0050", "📊 0056", "📊 00878", "📊 00927"])
tabs = {"0050": tab0050, "0056": tab0056, "00878": tab00878, "00927": tab00927}

for etf_id, tab in tabs.items():
    with tab:
        s = BEST_STRATEGIES[etf_id]
        color = ETF_INFO[etf_id]["color"]

        st.markdown(f"""
        ## {etf_id} {ETF_INFO[etf_id]['name']}

        <div class="strategy-box">
        <h3 style="color: {color};">📋 最佳策略參數</h3>
        <table style="width:100%; color: {COLORS['text']};">
        <tr><td style="padding:6px;"><b>📈 進場</b></td><td>配息前 <b>{s['entry_days']}</b> 天內 + 收盤站上 <b>MA{s['entry_ma']}</b> + RSI < <b>{s['entry_rsi_max']}</b> 且 > 30</td></tr>
        <tr><td style="padding:6px;"><b>➕ 加碼</b></td><td>配息後 <b>{s['add_days']}</b> 天內 + RSI < <b>{s['add_rsi_max']}</b> 且 > 25</td></tr>
        <tr><td style="padding:6px;"><b>🟡 部分出場</b></td><td>RSI > <b>{s['partial_rsi']}</b> + 收盤跌破 <b>MA{s['sell_ma']}</b> → 賣 50%</td></tr>
        <tr><td style="padding:6px;"><b>🔴 完全出場</b></td><td>連 3 天收盤跌破 <b>季線 {s['stop_loss_pct']}%</b> 或 RSI > <b>{s['extreme_rsi']}</b> + 連 3 黑K</td></tr>
        </table>
        </div>
        """, unsafe_allow_html=True)

        # Run backtest
        with st.spinner(f"正在跑 {etf_id} 5年回測..."):
            result = run_single_backtest(etf_id, s)

        if result is None:
            st.error("無法取得資料")
            continue

        # Metric cards
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("💰 最終市值", f"{result['final_value']:,.0f}", f"初始 100萬")
        with c2:
            ret_color = "inverse" if result["total_return_pct"] > 0 else "normal"
            st.metric("📈 總報酬", f"{result['total_return_pct']:+.1f}%", f"年化 {result['annual_return_pct']:+.1f}%")
        with c3:
            st.metric("🎯 勝率", f"{result['win_rate']}%", f"{result['wins']}W {result['losses']}L / {result['num_trades']}次")
        with c4:
            st.metric("📉 最大回撤", f"{result['max_drawdown_pct']:.1f}%")
        with c5:
            st.metric("📊 交易次數", f"{result['num_trades']}次")

        # Equity curve
        st.markdown(f"#### 📈 {etf_id} 資金成長曲線")
        dv_df = pd.DataFrame(result["daily_values"])
        dv_df["date"] = pd.to_datetime(dv_df["date"])
        dv_df = dv_df.set_index("date")

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dv_df.index, y=dv_df["value"], mode="lines",
            line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=f"rgba({int(color[1:3], 16)},{int(color[3:5], 16)},{int(color[5:7], 16)},0.08)",
        ))
        fig.add_hline(y=1000000, line_dash="dash", line_color=COLORS["blue_gray"], annotation_text="初始 100萬")
        fig.update_layout(
            template="plotly_dark", paper_bgcolor=COLORS["bg"], plot_bgcolor=COLORS["bg"],
            font=dict(color=COLORS["text"], size=12),
            xaxis=dict(gridcolor=COLORS["card"]),
            yaxis=dict(gridcolor=COLORS["card"], tickformat=","), height=350, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        # Price + RSI chart
        st.markdown(f"#### 📉 {etf_id} 股價走勢")
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x=result["price_dates"], y=result["price_close"], mode="lines",
            line=dict(color=color, width=1.5), name=f"{etf_id} 收盤價"))
        fig2.update_layout(
            template="plotly_dark", paper_bgcolor=COLORS["bg"], plot_bgcolor=COLORS["bg"],
            font=dict(color=COLORS["text"], size=12),
            xaxis=dict(gridcolor=COLORS["card"]),
            yaxis=dict(gridcolor=COLORS["card"]), height=300, showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

        # Trade log
        st.markdown(f"#### 📋 {etf_id} 交易紀錄")
        trades_df = pd.DataFrame(result["trades"])
        if not trades_df.empty:
            display_cols = [c for c in ["date", "action", "price", "shares", "reason", "pnl"] if c in trades_df.columns]
            trades_display = trades_df[display_cols].copy()
            zh_cols = {"date": "日期", "action": "動作", "price": "價格", "shares": "股數", "reason": "原因", "pnl": "損益"}
            trades_display.columns = [zh_cols.get(c, c) for c in display_cols]
            st.dataframe(trades_display, use_container_width=True, hide_index=True, height=300)

# ── Disclaimer ──
st.markdown("---")
st.markdown("""
### ⚠️ 免責聲明
- 回測結果不代表未來績效
- 0427 上市時間較短（2023年），回測樣本較少
- 00878 勝率 87.9% 但報酬偏低，因季配頻率高導致進出場次數多、手續費侵蝕獲利
- 00927 勝率 100% 但僅 9 次交易，樣本較少需持續觀察
- 此系統僅供研究參考，不構成投資建議
""")
