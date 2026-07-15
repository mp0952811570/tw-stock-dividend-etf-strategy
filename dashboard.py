"""
台股高息ETF配息輪動回測儀表板
Streamlit + Plotly 互動式視覺化
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

# ── 頁面設定 ──
st.set_page_config(
    page_title="台股高息ETF配息輪動回測",
    page_icon="📊",
    layout="wide",
)

# ── 暖色金融風配色（不用藍紫色！）──
COLORS = {
    "bg": "#1a1f2e",
    "card": "#222838",
    "text": "#e8e8e8",
    "gold": "#f0b90b",
    "orange": "#ff9f43",
    "red": "#e74c3c",       # 台股：紅漲
    "green": "#27ae60",     # 台股：綠跌
    "blue_gray": "#576574",
    "purple_warn": "#a55eea",
}

ETF_COLORS = {
    "0050": "#f0b90b",   # 金黃
    "0056": "#ff9f43",   # 暖橙
    "00878": "#e74c3c",  # 紅
    "00927": "#27ae60",  # 綠（台股綠跌，這裡只做識別色）
}

ETF_NAMES = {
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "00878": "國泰永續高股息",
    "00927": "群益台灣精選高息",
}

# ── 載入回測結果 ──
@st.cache_data
def load_results():
    f = os.path.join(os.path.dirname(__file__), "backtest_etf_results.json")
    with open(f, "r", encoding="utf-8") as fh:
        return json.load(fh)

@st.cache_data
def load_price_data():
    """載入四支ETF的價格資料"""
    cache_dir = os.path.join(os.path.dirname(__file__), "cache", "etf_prices")
    data = {}
    for etf_id in ["0050", "0056", "00878", "00927"]:
        f = os.path.join(cache_dir, f"{etf_id}_5y.csv")
        if os.path.exists(f):
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            data[etf_id] = df
    return data

# ── 自訂 CSS ──
st.markdown(f"""
<style>
    .stApp {{ background-color: {COLORS['bg']}; }}
    .main .block-container {{ padding-top: 1.5rem; }}
    
    /* 卡片樣式 */
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
        color: {COLORS['text']};
        font-size: 1.8rem;
        font-weight: bold;
        margin-top: 5px;
    }}
    .metric-card .sub {{
        color: {COLORS['blue_gray']};
        font-size: 0.8rem;
        margin-top: 3px;
    }}
    
    /* 標題 */
    h1, h2, h3 {{ color: {COLORS['text']} !important; }}
    h1 {{ border-bottom: 2px solid {COLORS['gold']}; padding-bottom: 10px; }}
    
    /* 表格 */
    .dataframe {{ color: {COLORS['text']}; }}
    th {{ background-color: {COLORS['card']} !important; color: {COLORS['gold']} !important; }}
    td {{ color: {COLORS['text']} !important; }}

    /* 側邊欄 */
    .css-1d391kg {{ background-color: {COLORS['card']} !important; }}
    
    /* 標籤文字 */
    .stMetric label {{ color: {COLORS['blue_gray']} !important; }}
    
    /* Plotly chart 底色 */
    .js-plotly-plot .plot-container .svg-container {{
        background-color: transparent !important;
    }}
</style>
""", unsafe_allow_html=True)


# ── 載入資料 ──
result = load_results()
price_data = load_price_data()

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
    x=dv_df.index,
    y=dv_df["value"],
    mode="lines",
    name="總市值",
    line=dict(color=COLORS["gold"], width=2),
    fill="tozeroy",
    fillcolor=f"rgba(240, 185, 11, 0.08)",
))

# 標記初始資金線
fig.add_hline(
    y=1000000,
    line_dash="dash",
    line_color=COLORS["blue_gray"],
    annotation_text="初始資金 100萬",
)

fig.update_layout(
    template="plotly_dark",
    paper_bgcolor=COLORS["bg"],
    plot_bgcolor=COLORS["bg"],
    font=dict(color=COLORS["text"], size=12),
    xaxis=dict(gridcolor=COLORS["card"]),
    yaxis=dict(gridcolor=COLORS["card"], tickformat=","),
    height=450,
    showlegend=False,
)
st.plotly_chart(fig, use_container_width=True)

# ── 個別ETF表現 ──
st.markdown("## 🏆 個別ETF表現")

per_etf = result["per_etf"]
etf_summary = []
for etf_id, stats in per_etf.items():
    etf_summary.append({
        "ETF": etf_id,
        "名稱": stats["name"],
        "賣出次數": stats["sells"],
        "獲利次數": stats["wins"],
        "虧損次數": stats["sells"] - stats["wins"],
        "勝率": f"{stats['win_rate']}%",
    })

etf_df = pd.DataFrame(etf_summary)
st.dataframe(etf_df, use_container_width=True, hide_index=True)

# ── 四支ETF價格走勢疊圖（標準化）──
st.markdown("## 📊 四支ETF價格走勢（標準化 = 起點為100）")

fig_prices = go.Figure()
for etf_id, df in price_data.items():
    normalized = (df["Close"] / df["Close"].iloc[0]) * 100
    fig_prices.add_trace(go.Scatter(
        x=df.index,
        y=normalized,
        mode="lines",
        name=f"{etf_id} {ETF_NAMES.get(etf_id, '')}",
        line=dict(color=ETF_COLORS.get(etf_id, "#888"), width=1.5),
    ))

fig_prices.update_layout(
    template="plotly_dark",
    paper_bgcolor=COLORS["bg"],
    plot_bgcolor=COLORS["bg"],
    font=dict(color=COLORS["text"], size=12),
    xaxis=dict(gridcolor=COLORS["card"]),
    yaxis=dict(gridcolor=COLORS["card"]),
    height=400,
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
    ),
)
st.plotly_chart(fig_prices, use_container_width=True)

# ── 交易紀錄表 ──
st.markdown("## 📋 完整交易紀錄")

trades = result["trades"]
trades_df = pd.DataFrame(trades)

# 處理可選欄位
display_cols = [c for c in ["date", "etf", "action", "price", "shares", "amount", "reason", "pnl"] if c in trades_df.columns]
trades_display = trades_df[display_cols].copy()
trades_display.columns = ["日期", "ETF", "動作", "價格", "股數", "金額", "原因", "損益"][:len(display_cols)]

# 動作著色
action_colors = {
    "BUY": COLORS["red"],       # 台股紅漲=買入
    "ADD": COLORS["orange"],    # 加碼=暖橙
    "PARTIAL_SELL": COLORS["purple_warn"],
    "FULL_SELL": COLORS["green"], # 台股綠跌=賣出
}

st.dataframe(
    trades_display,
    use_container_width=True,
    hide_index=True,
    height=500,
)

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

# 統計買賣次數
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
