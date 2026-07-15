"""
台股高息ETF配息輪動回測儀表板（Streamlit Cloud 自給自足版）
- 啟動時自動抓資料 + 跑回測，不需要本地預先產生的 json
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import yfinance as yf
import json
import os
import time
from datetime import datetime

# ── 頁面設定 ──
st.set_page_config(
    page_title="台股高息ETF配息輪動回測",
    page_icon="📊",
    layout="wide",
)

# ── 暖色金融風配色 ──
COLORS = {
    "bg": "#1a1f2e",
    "card": "#222838",
    "text": "#e8e8e8",
    "gold": "#f0b90b",
    "orange": "#ff9f43",
    "red": "#e74c3c",
    "green": "#27ae60",
    "blue_gray": "#576574",
    "purple_warn": "#a55eea",
}

ETF_COLORS = {
    "0050": "#f0b90b",
    "0056": "#ff9f43",
    "00878": "#e74c3c",
    "00927": "#27ae60",
}

ETF_NAMES = {
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "00878": "國泰永續高股息",
    "00927": "群益台灣精選高息",
}

ETF_INFO = {
    "0050": {"name": "元大台灣50", "suffix": ".TW"},
    "0056": {"name": "元大高股息", "suffix": ".TW"},
    "00878": {"name": "國泰永續高股息", "suffix": ".TW"},
    "00927": {"name": "群益台灣精選高息", "suffix": ".TW"},
}

# ── 回測參數 ──
INITIAL_CAPITAL = 1_000_000
FEE_RATE = 0.001425
ETF_TAX_RATE = 0.001
ENTRY_DAYS_BEFORE_DIV = 15
ADD_DAYS_AFTER_DIV = 15
ADD_RATIO = 0.50
PARTIAL_SELL_RATIO = 0.50

# ── CSS ──
st.markdown(f"""
<style>
    .stApp {{ background-color: {COLORS['bg']}; }}
    .main .block-container {{ padding-top: 1.5rem; }}
    .metric-card {{
        background-color: {COLORS['card']};
        border-radius: 10px;
        padding: 18px;
        border-left: 4px solid {COLORS['gold']};
    }}
    .metric-card h3 {{
        color: {COLORS['blue_gray']};
        font-size: 0.85rem;
        margin: 0;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    .metric-card .value {{
        font-size: 1.8rem;
        font-weight: bold;
        margin-top: 5px;
    }}
    .metric-card .sub {{
        color: {COLORS['blue_gray']};
        font-size: 0.8rem;
        margin-top: 3px;
    }}
    h1, h2, h3 {{ color: {COLORS['text']} !important; }}
    h1 {{ border-bottom: 2px solid {COLORS['gold']}; padding-bottom: 10px; }}
    .dataframe {{ color: {COLORS['text']}; }}
    th {{ background-color: {COLORS['card']} !important; color: {COLORS['gold']} !important; }}
    td {{ color: {COLORS['text']} !important; }}
    .stMetric label {{ color: {COLORS['blue_gray']} !important; }}
    .js-plotly-plot .plot-container .svg-container {{
        background-color: transparent !important;
    }}
</style>
""", unsafe_allow_html=True)


# ── 資料抓取 + 回測（全部 cached）──
@st.cache_data(ttl=3600, show_spinner="📥 正在從 Yahoo Finance 抓取 4 支 ETF 五年價量+配息資料...")
def fetch_all_data():
    etf_data = {}
    div_data = {}
    for etf_id in ETF_INFO:
        info = ETF_INFO[etf_id]
        ticker_id = f"{etf_id}{info['suffix']}"
        t = yf.Ticker(ticker_id)
        df = t.history(period="5y")
        df.index = df.index.tz_localize(None)
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()

        divs = t.dividends
        if not divs.empty:
            divs.index = divs.index.tz_localize(None)

        etf_data[etf_id] = df
        div_data[etf_id] = divs
        time.sleep(0.5)
    return etf_data, div_data


def calc_indicators(df):
    df = df.copy()
    df["MA5"] = df["Close"].rolling(5).mean()
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


@st.cache_data(ttl=3600, show_spinner="⚙️ 正在跑 5 年配息輪動回測...")
def run_backtest():
    etf_data, div_data = fetch_all_data()

    # 計算指標
    for etf_id in etf_data:
        etf_data[etf_id] = calc_indicators(etf_data[etf_id])

    # 建配息日查找表
    div_lookup = {}
    for etf_id, divs in div_data.items():
        if not divs.empty:
            div_lookup[etf_id] = sorted(divs.index.tolist())
        else:
            div_lookup[etf_id] = []

    # 統一日期軸
    all_dates = sorted(set().union(*[set(df.index) for df in etf_data.values()]))

    capital = INITIAL_CAPITAL
    positions = {}
    trades = []
    daily_values = []

    for i, current_date in enumerate(all_dates):
        if i < 60:
            continue

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

        # 每日總市值
        total_value = capital
        for etf_id, pos in positions.items():
            if etf_id in daily_prices:
                total_value += pos["shares"] * daily_prices[etf_id]["Close"]
        daily_values.append({"date": current_date, "value": round(total_value, 0)})

        # 部分出場
        for etf_id in list(positions.keys()):
            if etf_id not in daily_prices or etf_id not in daily_prev:
                continue
            row = daily_prices[etf_id]
            prev = daily_prev[etf_id]
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

        # 完全出場
        for etf_id in list(positions.keys()):
            if etf_id not in daily_prices or etf_id not in daily_prev:
                continue
            df = etf_data[etf_id]
            idx = df.index.get_loc(current_date)
            if idx < 4:
                continue
            row = daily_prices[etf_id]
            prev = daily_prev[etf_id]
            below_ma60_3days = all(
                df.iloc[idx - j]["Close"] < df.iloc[idx - j]["MA60"] * 0.97
                for j in range(3)
            )
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

        # 配息前進場
        for etf_id in ETF_INFO:
            if etf_id in positions or etf_id not in daily_prices:
                continue
            div_dates = div_lookup.get(etf_id, [])
            future_divs = [d for d in div_dates if d > current_date]
            if not future_divs:
                continue
            next_div = future_divs[0]
            days_to_div = (next_div - current_date).days
            if 5 <= days_to_div <= 25:
                row = daily_prices[etf_id]
                prev = daily_prev.get(etf_id)
                if prev is None:
                    continue
                entry_ok = prev["Close"] > prev["MA20"] and prev["RSI"] < 65 and prev["RSI"] > 30
                if entry_ok and capital > 10000:
                    buy_ratio = 0.50 if positions else 0.70
                    buy_amount = capital * buy_ratio
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

        # 配息後加碼
        for etf_id in list(positions.keys()):
            if etf_id not in daily_prices:
                continue
            pos = positions[etf_id]
            if pos["added"]:
                continue
            div_dates = div_lookup.get(etf_id, [])
            past_divs = [d for d in div_dates if d < current_date]
            if not past_divs:
                continue
            last_div = past_divs[-1]
            days_after_div = (current_date - last_div).days
            if 5 <= days_after_div <= 20:
                row = daily_prices[etf_id]
                prev = daily_prev.get(etf_id)
                if prev is None:
                    continue
                add_ok = prev["RSI"] < 50 and prev["RSI"] > 25
                if add_ok and capital > 10000:
                    add_amount = capital * ADD_RATIO
                    shares = int(add_amount / (row["Close"] * (1 + FEE_RATE)))
                    if shares > 0:
                        cost = shares * row["Close"] * (1 + FEE_RATE)
                        capital -= cost
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

    # 最終計算
    final_date = all_dates[-1]
    final_value = capital
    for etf_id, pos in positions.items():
        df = etf_data[etf_id]
        if final_date in df.index:
            final_price = df.loc[final_date, "Close"]
        else:
            final_price = df.iloc[-1]["Close"]
        final_value += pos["shares"] * final_price

    sell_trades = [t for t in trades if t["action"] in ("PARTIAL_SELL", "FULL_SELL")]
    win_trades = [t for t in sell_trades if t.get("pnl", 0) > 0]
    lose_trades = [t for t in sell_trades if t.get("pnl", 0) <= 0]
    total_return = ((final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
    win_rate = (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0
    avg_win = np.mean([t["pnl"] for t in win_trades]) if win_trades else 0
    avg_loss = np.mean([t["pnl"] for t in lose_trades]) if lose_trades else 0

    dv_df = pd.DataFrame(daily_values).set_index("date")
    dv_df["peak"] = dv_df["value"].cummax()
    dv_df["dd"] = (dv_df["value"] - dv_df["peak"]) / dv_df["peak"] * 100
    max_dd = abs(dv_df["dd"].min())

    years = len(all_dates) / 252
    annual_return = ((final_value / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0

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

    return {
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
        "profit_factor": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
        "per_etf": per_etf,
        "trades": trades,
        "daily_values": [{"date": str(d["date"].date()) if hasattr(d["date"], "date") else str(d["date"]), "value": d["value"]} for d in daily_values],
        "etf_data_for_chart": {etf_id: {"dates": [str(d.date()) if hasattr(d, "date") else str(d) for d in df.index], "close": df["Close"].tolist()} for etf_id, df in etf_data.items()},
    }


# ── 執行回測 ──
result = run_backtest()

# ── 標題 ──
st.markdown("""
# 📊 台股高息ETF配息輪動回測系統
**0050 / 0056 / 00878 / 00927** | 過去5年回測 | 配息事件驅動策略
""")

# ── 核心指標卡片 ──
col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.markdown(f"""
    <div class="metric-card">
        <h3>💰 最終市值</h3>
        <div class="value" style="color:{COLORS['gold']}">{result['final_value']:,.0f}</div>
        <div class="sub">初始 1,000,000</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    ret_color = COLORS["red"] if result["total_return_pct"] > 0 else COLORS["green"]
    st.markdown(f"""
    <div class="metric-card">
        <h3>📈 總報酬率</h3>
        <div class="value" style="color:{ret_color}">{result['total_return_pct']:+.1f}%</div>
        <div class="sub">年化 {result['annual_return_pct']:+.1f}%</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="metric-card">
        <h3>🎯 勝率</h3>
        <div class="value" style="color:{COLORS['gold']}">{result['win_rate']:.1f}%</div>
        <div class="sub">{result['wins']}勝 {result['losses']}敗 / {result['total_trades']}次</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    st.markdown(f"""
    <div class="metric-card">
        <h3>📉 最大回撤</h3>
        <div class="value" style="color:{COLORS['green']}">{result['max_drawdown_pct']:.1f}%</div>
        <div class="sub">5年內最大跌幅</div>
    </div>
    """, unsafe_allow_html=True)

with col5:
    st.markdown(f"""
    <div class="metric-card">
        <h3>⚖️ 盈虧比</h3>
        <div class="value" style="color:{COLORS['gold']}">{result['profit_factor']:.2f}</div>
        <div class="sub">平均賺{result['avg_win']:,.0f} / 賠{result['avg_loss']:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# ── 資金曲線 ──
st.markdown("## 📈 資金成長曲線")

dv_df = pd.DataFrame(result["daily_values"])
dv_df["date"] = pd.to_datetime(dv_df["date"])
dv_df = dv_df.set_index("date")

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=dv_df.index, y=dv_df["value"], mode="lines", name="總市值",
    line=dict(color=COLORS["gold"], width=2),
    fill="tozeroy", fillcolor="rgba(240, 185, 11, 0.08)",
))
fig.add_hline(y=1000000, line_dash="dash", line_color=COLORS["blue_gray"],
              annotation_text="初始資金 100萬")
fig.update_layout(
    template="plotly_dark", paper_bgcolor=COLORS["bg"], plot_bgcolor=COLORS["bg"],
    font=dict(color=COLORS["text"], size=12),
    xaxis=dict(gridcolor=COLORS["card"]),
    yaxis=dict(gridcolor=COLORS["card"], tickformat=","), height=450, showlegend=False)
st.plotly_chart(fig, use_container_width=True)

# ── 個別ETF表現 ──
st.markdown("## 🏆 個別ETF表現")
per_etf = result["per_etf"]
etf_summary = []
for etf_id, stats in per_etf.items():
    etf_summary.append({
        "ETF": etf_id, "名稱": stats["name"], "賣出次數": stats["sells"],
        "獲利次數": stats["wins"], "虧損次數": stats["sells"] - stats["wins"],
        "勝率": f"{stats['win_rate']}%",
    })
st.dataframe(pd.DataFrame(etf_summary), use_container_width=True, hide_index=True)

# ── 四支ETF價格走勢疊圖 ──
st.markdown("## 📊 四支ETF價格走勢（標準化 = 起點為100）")
fig_prices = go.Figure()
for etf_id, data in result["etf_data_for_chart"].items():
    closes = data["close"]
    if closes:
        first = closes[0]
        normalized = [(c / first) * 100 for c in closes]
        fig_prices.add_trace(go.Scatter(
            x=data["dates"], y=normalized, mode="lines",
            name=f"{etf_id} {ETF_NAMES.get(etf_id, '')}",
            line=dict(color=ETF_COLORS.get(etf_id, "#888"), width=1.5)))
fig_prices.update_layout(
    template="plotly_dark", paper_bgcolor=COLORS["bg"], plot_bgcolor=COLORS["bg"],
    font=dict(color=COLORS["text"], size=12),
    xaxis=dict(gridcolor=COLORS["card"]), yaxis=dict(gridcolor=COLORS["card"]),
    height=400, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
st.plotly_chart(fig_prices, use_container_width=True)

# ── 交易紀錄表 ──
st.markdown("## 📋 完整交易紀錄")
trades = result["trades"]
trades_df = pd.DataFrame(trades)
display_cols = [c for c in ["date", "etf", "action", "price", "shares", "amount", "reason", "pnl"] if c in trades_df.columns]
trades_display = trades_df[display_cols].copy()
col_names_zh = ["日期", "ETF", "動作", "價格", "股數", "金額", "原因", "損益"]
trades_display.columns = col_names_zh[:len(display_cols)]
st.dataframe(trades_display, use_container_width=True, hide_index=True, height=500)

# ── 策略說明 ──
st.markdown("---")
st.markdown("""
## 📖 策略說明

### 進場訊號（BUY）
1. **配息前 15~25 天窗口**內啟動監控
2. 確認條件：**收盤站上 20MA（月線之上）** + **RSI 介於 30~65**（不過熱也不超跌）
3. 資金分配：首單 50%~70%，保留剩餘資金供加碼或其他ETF輪動

### 加碼訊號（ADD）
1. **除息後 5~20 天窗口**內觸發
2. 確認條件：**RSI < 50**（未過熱，有上漲空間）
3. 加碼金額：剩餘資金的 50%，每檔最多加碼 1 次

### 部分出場（PARTIAL_SELL）
1. **RSI > 80**（嚴重超買）+ **收盤跌破 5MA**（短線轉弱確認）
2. 賣出 50% 持倉，落袋為安

### 完全出場（FULL_SELL）
1. 連續 3 天收盤 **跌破季線（60MA）3% 以上** → 趨勢反轉
2. 或 **RSI > 85** + **連 3 天黑K** → 極端過熱

### 配息交錯輪動節奏
| 月份 | 進場/加碼的 ETF |
|------|-----------------|
| 1月 | 0050, 0056, 00927 |
| 2月 | 00878 |
| 4月 | 0056, 00927 |
| 5月 | 00878 |
| 7月 | 0050, 0056, 00927 |
| 8月 | 00878 |
| 10月 | 0056, 00927 |
| 11月 | 00878 |

⚠️ **免責聲明**：回測結果不代表未來績效。此系統僅供研究參考，不構成投資建議。
""")

buy_count = len([t for t in trades if t["action"] == "BUY"])
add_count = len([t for t in trades if t["action"] == "ADD"])
psell_count = len([t for t in trades if t["action"] == "PARTIAL_SELL"])
fsell_count = len([t for t in trades if t["action"] == "FULL_SELL"])

st.markdown(f"""
### 📊 交易統計
| 動作 | 次數 |
|------|------|
| 📌 進場 BUY | {buy_count} |
| ➕ 加碼 ADD | {add_count} |
| 🟡 部分出場 PARTIAL_SELL | {psell_count} |
| 🔴 完全出場 FULL_SELL | {fsell_count} |
| **總交易** | **{len(trades)}** |
""")
