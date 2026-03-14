import requests
import time
import json
import argparse
from datetime import datetime

# ─── CLI args ──────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="AI F&O Trading Orchestrator")
parser.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "FINIFTY"],
                    help="Instrument to trade (default: NIFTY)")
parser.add_argument("--dry-run", action="store_true", help="Log signals without placing orders")
args, _ = parser.parse_known_args()

SYMBOL    = args.symbol
DRY_RUN   = args.dry_run

# ─── Service endpoints ─────────────────────────────────────────────────────────
MARKET_URL    = "http://localhost:8001"
FEATURE_URL   = "http://localhost:8002"
PREDICTION_URL= "http://localhost:8003"
STRATEGY_URL  = "http://localhost:8004"
RISK_URL      = "http://localhost:8005"
EXECUTION_URL = "http://localhost:8006"

LIVE_STATE_FILE = "live_state.json"
TRADE_LOG_FILE  = "trade_log.jsonl"
SIGNAL_LOG_FILE = "signal_log.jsonl"

active_trade = None


# ─── Startup health check ──────────────────────────────────────────────────────
def health_check() -> bool:
    services = {
        "Market Data":  f"{MARKET_URL}/quote?symbol={SYMBOL}",
        "Feature Engine": f"{FEATURE_URL}/docs",
        "Prediction":   f"{PREDICTION_URL}/docs",
        "Strategy":     f"{STRATEGY_URL}/docs",
        "Risk Manager": f"{RISK_URL}/status",
        "Execution":    f"{EXECUTION_URL}/positions",
    }
    print(f"\n{'='*50}")
    print(f"  AI F&O Orchestrator — Symbol: {SYMBOL}")
    print(f"  Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"{'='*50}")
    all_ok = True
    for name, url in services.items():
        try:
            r = requests.get(url, timeout=3)
            ok = r.status_code == 200
        except Exception:
            ok = False
        icon = "✓" if ok else "✗"
        print(f"  [{icon}] {name}")
        if not ok:
            all_ok = False
    print()
    return all_ok


# ─── State helpers ─────────────────────────────────────────────────────────────
def save_live_state(data: dict):
    try:
        data["updated_at"] = datetime.now().isoformat()
        with open(LIVE_STATE_FILE, "w") as f:
            json.dump(data, f, default=str)
    except Exception as e:
        print(f"  [WARN] live_state save failed: {e}")


def log_trade(entry: dict):
    try:
        with open(TRADE_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


# ─── Trading cycle ─────────────────────────────────────────────────────────────
def run_trading_cycle():
    global active_trade

    now = datetime.now()
    print(f"\n--- Cycle: {now.strftime('%H:%M:%S')} | {SYMBOL} ---")

    # ── 1. Spot price (fast quote) ────────────────────────────────────────────
    try:
        r = requests.get(f"{MARKET_URL}/quote", params={"symbol": SYMBOL}, timeout=5)
        if r.status_code != 200:
            print("  [ERR] Quote fetch failed")
            return
        current_price = float(r.json()["price"])
    except Exception as e:
        print(f"  [ERR] Quote: {e}")
        return

    current_state = {
        "symbol":        SYMBOL,
        "current_price": current_price,
        "is_live":       True,
        "dry_run":       DRY_RUN,
        "timestamp":     now.isoformat(),
        "active_trade":  None,
        "prediction":    None,
    }

    # ── Monitor open trade ─────────────────────────────────────────────────────
    if active_trade is not None:
        entry      = active_trade["order"]["entry_price"]
        action     = active_trade["order"]["action"]
        sl_pct     = active_trade["sl_pct"]
        target_pct = active_trade["target_pct"]
        trail_act  = active_trade.get("trail_activate_pct", 0.35)
        is_sell    = "SELL" in action or "CONDOR" in action

        spot_entry = active_trade["spot_at_entry"]
        price_chg  = (current_price - spot_entry) / spot_entry

        # Option PnL simulation (delta-based proxy)
        if is_sell:
            if "PUT" in action:
                pnl_pct = price_chg * 10        # sell-put profits when spot rises
            elif "CALL" in action:
                pnl_pct = -price_chg * 10       # sell-call profits when spot falls
            else:  # STRANGLE / CONDOR
                pnl_pct = -abs(price_chg) * 10 + 0.05
        else:
            pnl_pct = price_chg * 10
            if "PUT" in action:
                pnl_pct = -pnl_pct

        # ── Trailing stop logic ────────────────────────────────────────────────
        if not active_trade.get("trailing_active") and pnl_pct >= trail_act:
            active_trade["trailing_active"] = True
            active_trade["trail_high"]      = pnl_pct
            print(f"  => Trailing stop ACTIVATED at +{pnl_pct*100:.1f}%")

        if active_trade.get("trailing_active"):
            if pnl_pct > active_trade.get("trail_high", pnl_pct):
                active_trade["trail_high"] = pnl_pct
            # Trail: exit if price drops >10% from the trailing high
            trail_sl = active_trade["trail_high"] - 0.10
            if pnl_pct <= trail_sl:
                print(f"  => TRAILING STOP HIT at +{pnl_pct*100:.1f}% (high was +{active_trade['trail_high']*100:.1f}%)")
                active_trade["_exit_reason"] = "TRAIL_SL"

        qty = active_trade["order"]["qty"]
        pnl_amount = round(entry * qty * pnl_pct, 2)

        current_state["active_trade"] = {
            "order_id":    active_trade["order"]["order_id"],
            "symbol":      active_trade["order"]["symbol"],
            "action":      action,
            "entry_price": entry,
            "qty":         qty,
            "pnl_pct":     round(pnl_pct * 100, 2),
            "pnl_amount":  pnl_amount,
        }

        # Exit triggers
        exit_reason = None
        if   active_trade.get("_exit_reason") == "TRAIL_SL":
            exit_reason = "TRAIL_SL"
        elif pnl_pct >= target_pct:
            exit_reason = "TARGET_HIT"
            print(f"  => TARGET HIT (+{pnl_pct*100:.1f}%)")
        elif pnl_pct <= -sl_pct:
            exit_reason = "SL_HIT"
            print(f"  => SL HIT ({pnl_pct*100:.1f}%)")
        else:
            elapsed = (now - active_trade["entry_time"]).total_seconds() / 60
            if elapsed >= active_trade["time_exit_mins"]:
                exit_reason = "TIME_EXIT"
                print(f"  => TIME EXIT after {elapsed:.0f}m")

        if exit_reason:
            if not DRY_RUN:
                exit_price = round(entry * (1 + pnl_pct), 2)
                held_min   = int((now - active_trade["entry_time"]).total_seconds() / 60)
                r = requests.post(
                    f"{EXECUTION_URL}/exit_order",
                    json={"order_id": active_trade["order"]["order_id"],
                          "exit_price": exit_price,
                          "reason": exit_reason,
                          "hold_minutes": held_min},
                    timeout=5
                )
                if r.status_code == 200:
                    closed = r.json()["order"]
                    net_pnl = closed["pnl"]
                    print(f"  => CLOSED | Net PnL: Rs{net_pnl:+.0f} | Reason: {exit_reason}")
                    log_trade({"time": now.isoformat(), "order": closed, "reason": exit_reason})
                    requests.post(f"{RISK_URL}/update_pnl",
                                  json={"pnl": net_pnl, "exposure_released": closed.get("entry_price", 0) * closed.get("qty", 1)},
                                  timeout=3)
            active_trade = None
            current_state["active_trade"] = None

        save_live_state(current_state)
        return

    # ── No open trade → seek entry ─────────────────────────────────────────────

    # 2. Full candle data for features
    try:
        r = requests.get(f"{MARKET_URL}/candles", params={"symbol": SYMBOL, "timeframe": "1m"}, timeout=10)
        candles = r.json()["data"]
    except Exception as e:
        print(f"  [ERR] Candles: {e}")
        save_live_state(current_state)
        return

    # 3. Generate features
    try:
        r = requests.post(f"{FEATURE_URL}/generate_features",
                          json={"data": candles, "symbol": SYMBOL}, timeout=10)
        features = r.json()["features"]
    except Exception as e:
        print(f"  [ERR] Features: {e}")
        save_live_state(current_state)
        return

    # 4. Predict
    try:
        r = requests.post(f"{PREDICTION_URL}/predict", json={"features": features}, timeout=5)
        pred_data = r.json()
        confidence = pred_data["confidence"]
        trend      = pred_data["trend"]
        print(f"  AI => {trend.upper()} (conf={confidence:.2f})")
        current_state["prediction"] = {
            "trend": trend, "confidence": confidence,
            "snapshot": {k: features.get(k) for k in ["rsi", "adx", "vwap_dist", "pcr", "atr_pct"]}
        }
        save_live_state(current_state)
    except Exception as e:
        print(f"  [ERR] Predict: {e}")
        save_live_state(current_state)
        return

    # 5. Strategy signal
    try:
        r = requests.post(f"{STRATEGY_URL}/generate_signal",
                          json={"prediction": pred_data["prediction"],
                                "trend": trend, "confidence": confidence,
                                "features": features}, timeout=5)
        signal_data = r.json().get("signal")
    except Exception as e:
        print(f"  [ERR] Strategy: {e}")
        return

    if signal_data == "no_trade" or not isinstance(signal_data, dict):
        reason = r.json().get("reason", "Filtered out")
        print(f"  => No trade: {reason}")
        return

    print(f"  Signal => {signal_data['action']} | SL:{signal_data['sl_pct']:.0%} Tgt:{signal_data['target_pct']:.0%}")

    # 6. Risk check + sizing
    try:
        r = requests.get(f"{RISK_URL}/check_allowed", timeout=3)
        risk = r.json()
        if not risk.get("allowed"):
            print(f"  => Risk block: {risk.get('reason')}")
            return

        r = requests.post(f"{RISK_URL}/calculate_quantity",
                          json={"action": signal_data["action"],
                                "sl_pct": signal_data["sl_pct"],
                                "atr_pct": signal_data.get("atr_pct", 0.7)}, timeout=3)
        qty_data = r.json()
        qty = qty_data.get("quantity", 0)
        if qty <= 0:
            print(f"  => Qty=0: {qty_data.get('reason', 'Margin/Risk limit')}")
            return
        print(f"  Qty => {qty} units | Kelly={qty_data.get('kelly_fraction', 0):.3f}")
    except Exception as e:
        print(f"  [ERR] Risk: {e}")
        return

    # 7. Place order
    instrument = f"{SYMBOL} CE" if "CALL" in signal_data["action"] else \
                 (f"{SYMBOL} PE" if "PUT" in signal_data["action"] else f"{SYMBOL} {signal_data['action']}")

    if DRY_RUN:
        print(f"  [DRY RUN] Would place: {instrument} x{qty}")
        return

    try:
        r = requests.post(f"{EXECUTION_URL}/place_order",
                          json={"symbol": instrument, "qty": qty,
                                "action": signal_data["action"],
                                "atr_pct": signal_data.get("atr_pct", 0.7),
                                "estimated_premium": qty_data.get("premium_est", 100)},
                          timeout=5)
        if r.status_code != 200:
            print(f"  [ERR] Execution failed: {r.text}")
            return
        order = r.json()["order"]
        print(f"  ORDER PLACED: {order['order_id']} @ Rs{order['entry_price']}")

        log_trade({"time": now.isoformat(), "order": order, "signal": signal_data})

        active_trade = {
            "order":              order,
            "entry_time":         now,
            "spot_at_entry":      current_price,
            "sl_pct":             signal_data["sl_pct"],
            "target_pct":         signal_data["target_pct"],
            "time_exit_mins":     signal_data["time_exit_mins"],
            "trail_activate_pct": signal_data.get("trail_activate_pct", 0.35),
            "trailing_active":    False,
            "trail_high":         0.0,
        }
        current_state["active_trade"] = {
            "order_id": order["order_id"], "symbol": instrument,
            "action": signal_data["action"], "entry_price": order["entry_price"],
            "qty": qty, "pnl_pct": 0, "pnl_amount": 0
        }
        save_live_state(current_state)

    except Exception as e:
        print(f"  [ERR] Order placement: {e}")


# ─── Main loop ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not health_check():
        print("[WARN] Some services are offline. Proceeding anyway...")

    print(f"Starting trading loop for {SYMBOL}...\n")
    while True:
        try:
            run_trading_cycle()
        except Exception as e:
            print(f"  [CRITICAL] Cycle error: {e}")
        print(f"  Sleeping 15s...")
        time.sleep(15)
