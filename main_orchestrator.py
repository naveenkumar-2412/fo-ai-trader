import requests
import time
from datetime import datetime
import json

# Internal service endpoints
MARKET_DATA_URL = "http://localhost:8001"
FEATURE_URL = "http://localhost:8002"
PREDICTION_URL = "http://localhost:8003"
STRATEGY_URL = "http://localhost:8004"
RISK_URL = "http://localhost:8005"
EXECUTION_URL = "http://localhost:8006"

# In-memory store for tracking the current open trade
active_trade = None

def save_live_state(data: dict):
    with open("live_state.json", "w") as f:
        json.dump(data, f)

def run_trading_cycle():
    global active_trade
    cycle_time = datetime.now()
    print(f"\n--- Cycle Start: {cycle_time.strftime('%H:%M:%S')} ---")
    symbol = "NIFTY"
    
    # 1. Fetch Market Data
    print("1. Fetching market data...")
    res = requests.get(f"{MARKET_DATA_URL}/candles?symbol={symbol}&timeframe=1m") # Changed to 1m for faster action near open
    if res.status_code != 200:
        print("Market data error:", res.text)
        return
        
    try:
        candles = res.json()["data"]
        current_price = candles[-1]["close"]
    except KeyError:
        print("Market data format error")
        return
        
    # State tracking payload
    current_state = {
        "timestamp": cycle_time.isoformat(),
        "is_live": True,
        "symbol": symbol,
        "current_price": current_price,
        "active_trade": None,
        "prediction": None,
    }
    
    # --- IF TRADE IS OPEN, MONITOR FOR EXIT ---
    if active_trade is not None:
        print(f"Tracking Open Trade: {active_trade['order']['order_id']} | Entry: {active_trade['order']['entry_price']} | Current Price: {current_price}")
        
        entry = active_trade['order']['entry_price']
        action = active_trade['order']['action']
        sl_pct = active_trade['sl_pct']
        target_pct = active_trade['target_pct']
        
        # Calculate Option PnL roughly tracking spot variations for simulation
        if "BUY" in action:
            price_change_pct = (current_price - active_trade['spot_at_entry']) / active_trade['spot_at_entry']
            if "PUT" in action:
                price_change_pct = -price_change_pct
            simulated_option_pnl_pct = price_change_pct * 10
        else: # SELLING
            price_change_pct = (current_price - active_trade['spot_at_entry']) / active_trade['spot_at_entry']
            if "PUT" in action:
                simulated_option_pnl_pct = price_change_pct * 10
            elif "CALL" in action:
                simulated_option_pnl_pct = -price_change_pct * 10
            else: # STRANGLE
                simulated_option_pnl_pct = -abs(price_change_pct) * 10 + 0.05
                
        # Update live state active trade tracking
        current_state["active_trade"] = {
            "order_id": active_trade['order']['order_id'],
            "symbol": active_trade['order']['symbol'],
            "action": action,
            "entry_price": entry,
            "qty": active_trade['order']['qty'],
            "current_pnl_pct": round(simulated_option_pnl_pct * 100, 2),
            "simulated_pnl_amount": round(entry * active_trade['order']['qty'] * simulated_option_pnl_pct, 2)
        }
                
        trigger_exit = False
        # Check Triggers
        if simulated_option_pnl_pct >= target_pct:
            print("   => TARGET HIT! Initiating Exit.")
            trigger_exit = True
        elif simulated_option_pnl_pct <= -sl_pct:
            print("   => STOP LOSS HIT! Initiating Exit.")
            trigger_exit = True
        else:
            time_held = (datetime.now() - active_trade['entry_time']).seconds / 60
            if time_held >= active_trade['time_exit_mins']:
                print("   => TIME EXIT HIT! Initiating Exit.")
                trigger_exit = True
            else:
                print(f"   => Trade ongoing. PnL %: {simulated_option_pnl_pct*100:.2f}%. SL at -{sl_pct*100}%, Tgt at {target_pct*100}%")
                save_live_state(current_state)
                return
                
        # Execute Exit
        if trigger_exit:
            exit_price = entry * (1 + simulated_option_pnl_pct)
            res = requests.post(f"{EXECUTION_URL}/exit_order", json={"order_id": active_trade['order']['order_id'], "exit_price": round(exit_price, 2)})
            if res.status_code == 200:
                closed_info = res.json()["order"]
                print(f"   => ORDER CLOSED. Realized PNL: ₹{closed_info['pnl']}")
                with open("trade_log.jsonl", "a") as f:
                    f.write(json.dumps({"time": str(datetime.now()), "order": closed_info}) + "\n")
                requests.post(f"{RISK_URL}/update_pnl", json={"pnl": closed_info["pnl"]})
            active_trade = None
            current_state["active_trade"] = None
        
        save_live_state(current_state)
        return

    # --- IF NO TRADE OPEN, SEEK ENTRY ---
    # 2. Generate Features
    print("2. Generating features...")
    res = requests.post(f"{FEATURE_URL}/generate_features", json={"data": candles})
    if res.status_code != 200:
        print("Feature engine error:", res.text)
        save_live_state(current_state)
        return
    features = res.json()["features"]

    # 3. Predict Trend
    res = requests.post(f"{PREDICTION_URL}/predict", json={"features": features})
    if res.status_code != 200:
        print("Prediction engine error:", res.text)
        save_live_state(current_state)
        return
        
    prediction_data = res.json()
    confidence = prediction_data["confidence"]
    print(f"3. AI Predicting trend... -> {prediction_data['trend'].upper()} (Conf: {confidence})")
    
    current_state["prediction"] = {
        "trend": prediction_data['trend'],
        "confidence": confidence,
        "features": {k: v for k, v in features.items() if k in ['vwap_dist', 'rsi', 'volume_spike', 'oi_change_pct']}
    }
    
    # Save the prediction state whether trade executes or not
    save_live_state(current_state)

    if confidence <= 0.65:
        print("   => Skipping trade (Confidence low).")
        return

    # 4. Get Strategy Signal
    payload = {
        "prediction": prediction_data["prediction"],
        "trend": prediction_data["trend"],
        "confidence": confidence,
        "features": features
    }
    res = requests.post(f"{STRATEGY_URL}/generate_signal", json=payload)
    if res.status_code != 200:
        print("Strategy endpoint error")
        return
        
    signal_data = res.json()["signal"]
    
    if signal_data == "no_trade" or type(signal_data) is str:
        print(f"   => No trade signal generated.")
        return

    # 5. Risk Manager Check & Qty Calc
    res = requests.get(f"{RISK_URL}/check_allowed")
    if not res.json().get('allowed', False):
         print(f"   => Blocked by Risk Manager: {res.json().get('reason')}")
         return
         
    res = requests.post(f"{RISK_URL}/calculate_quantity", json={"action": signal_data["action"], "sl_pct": signal_data["sl_pct"]})
    qty = res.json()["quantity"]
    
    if qty <= 0:
        print(f"   => Blocked: Calculated quantity is 0. Reason: {res.json().get('reason', 'Margin or Risk limit')}")
        return
        
    print(f"   => Approved Signal: {signal_data['action']} | Qty: {qty}")
    
    # 6. Execution
    instrument = f"{symbol} CE" if "CALL" in signal_data["action"] else (f"{symbol} PE" if "PUT" in signal_data["action"] else f"{symbol} STRANGLE")
    order_payload = {
        "symbol": instrument,
        "qty": qty,
        "action": signal_data["action"]
    }
    
    res = requests.post(f"{EXECUTION_URL}/place_order", json=order_payload)
    if res.status_code == 200:
        order_info = res.json()["order"]
        print(f"   => ORDER PLACED: {order_info}")
        
        with open("trade_log.jsonl", "a") as f:
            f.write(json.dumps({"time": str(datetime.now()), "order": order_info}) + "\n")
            
        # Store in state to monitor next cycle
        active_trade = {
            'order': order_info,
            'entry_time': datetime.now(),
            'spot_at_entry': current_price,
            'sl_pct': signal_data["sl_pct"],
            'target_pct': signal_data["target_pct"],
            'time_exit_mins': signal_data["time_exit_mins"]
        }
        
        current_state["active_trade"] = {
            "order_id": active_trade['order']['order_id'],
            "symbol": active_trade['order']['symbol'],
            "action": signal_data["action"],
            "entry_price": order_info['entry_price'],
            "qty": active_trade['order']['qty'],
            "current_pnl_pct": 0,
            "simulated_pnl_amount": 0
        }
        save_live_state(current_state)
    else:
        print("Execution failed:", res.text)

if __name__ == "__main__":
    print("Starting AI F&O Trading Orchestrator...")
    while True:
        try:
            run_trading_cycle()
        except Exception as e:
            print("Error in loop:", e)
        # Sleep for 15 seconds so we fetch data at roughly 4 times per minute
        # Safe enough for Yahoo finance without hammering the single spot API.
        print("Sleeping for 15 seconds to avoid rate limits while remaining reasonably live...")
        time.sleep(15)
