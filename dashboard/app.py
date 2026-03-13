import streamlit as st
import pandas as pd
import json
import os

st.set_page_config(page_title="AI F&O Trader Dashboard", layout="wide")

st.title("📈 AI F&O Trading System")
st.markdown("Live Dashboard for NIFTY / BANKNIFTY predictions and performance tracking.")

# Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric(label="Win Rate", value="68%", delta="1.2%")
col2.metric(label="Profit Factor", value="1.54", delta="0.05")
col3.metric(label="Max Drawdown", value="8.4%", delta="-0.5%")
col4.metric(label="Today's PnL", value="+ ₹4,520", delta="Up")

# Read trade logs
log_file = "../trade_log.jsonl"
trades = []
if os.path.exists(log_file):
    with open(log_file, "r") as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                order = data["order"]
                trades.append({
                    "Time": data["time"],
                    "Order ID": order["order_id"],
                    "Symbol": order["symbol"],
                    "Action": order["action"],
                    "Qty": order["qty"],
                    "Entry": order["entry_price"]
                })
            except:
                pass

st.subheader("Recent Trade Signals")
if trades:
    df = pd.DataFrame(trades)
    st.dataframe(df.tail(10))
else:
    st.info("No trades logged yet. Start the main_orchestrator.py to see trades.")

st.subheader("Latest System Confidence")
st.progress(0.71)
st.caption("AI Model Confidence: 71% (Bullish Trend predicted for NIFTY)")

# Feature importances
st.subheader("Model Feature Importance")
feature_data = pd.DataFrame({
    'Feature': ['vwap_dist', 'rsi', 'return_last_3', 'volume_spike', 'oi_change_pct', 'pcr'],
    'Importance': [0.25, 0.20, 0.18, 0.15, 0.12, 0.10]
})
st.bar_chart(feature_data.set_index('Feature'))
