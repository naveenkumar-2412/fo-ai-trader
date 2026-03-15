"""
AI F&O Trading Orchestrator v3.0
- Multi-timeframe confluence (1m + 5m + 15m: ≥2/3 must agree)
- News impact filter via mcp_news
- Event bus publishing at every key stage
- Telegram alerts via mcp_notifications
- Multi-position tracking (up to 2 simultaneous, different instruments)
- Trailing stop monitoring per position
- BANKNIFTY / FINIFTY / NIFTY CLI support
- --dry-run mode for paper trading
"""
import requests
import time
import json
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="AI F&O Orchestrator v3")
parser.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "FINIFTY"])
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--max-positions", type=int, default=2)
args, _ = parser.parse_known_args()

SYMBOL       = args.symbol
DRY_RUN      = args.dry_run
MAX_POSITIONS= args.max_positions

# ─── Service endpoints ─────────────────────────────────────────────────────────
MARKET_URL    = "http://localhost:8001"
FEATURE_URL   = "http://localhost:8002"
PREDICTION_URL= "http://localhost:8003"
STRATEGY_URL  = "http://localhost:8004"
RISK_URL      = "http://localhost:8005"
EXECUTION_URL = "http://localhost:8006"
DASHBOARD_URL = "http://localhost:8007"
NEWS_URL      = "http://localhost:8008"
EVENT_BUS_URL = "http://localhost:8009"
NOTIFY_URL    = "http://localhost:8010"

LIVE_STATE_FILE = "live_state.json"
TRADE_LOG_FILE  = "trade_log.jsonl"

# Multi-position store: list of dicts
active_trades: list = []


# ─── Helpers ───────────────────────────────────────────────────────────────────
def _get(url, params=None, timeout=5):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _post(url, data=None, timeout=5):
    try:
        r = requests.post(url, json=data, timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def save_live_state(data: dict):
    try:
        data["updated_at"] = datetime.now().isoformat()
        with open(LIVE_STATE_FILE, "w") as f:
            json.dump(data, f, default=str)
    except Exception as e:
        print(f"  [WARN] live_state: {e}")


def log_trade(entry: dict):
    try:
        with open(TRADE_LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def publish_event(event_type: str, stage: str, payload: dict):
    """Fire-and-forget to the event bus."""
    _post(f"{EVENT_BUS_URL}/publish", {
        "event_type": event_type,
        "symbol":     SYMBOL,
        "stage":      stage,
        "payload":    payload,
    })


def notify(event: str, order: dict, pnl: float = 0, pnl_pct: float = 0,
           reason: str = "", confidence: float = 0):
    """Send Telegram trade alert."""
    _post(f"{NOTIFY_URL}/trade_alert", {
        "event":      event,
        "symbol":     SYMBOL,
        "action":     order.get("action", ""),
        "qty":        order.get("qty", 0),
        "price":      order.get("entry_price", 0) if event == "ENTRY" else order.get("exit_price", 0),
        "pnl":        pnl,
        "pnl_pct":    pnl_pct,
        "order_id":   order.get("order_id"),
        "confidence": confidence,
        "reason":     reason,
    })


# ─── Startup health check ──────────────────────────────────────────────────────
def health_check() -> bool:
    checks = {
        "Market Data":   f"{MARKET_URL}/quote?symbol={SYMBOL}",
        "Features":      f"{FEATURE_URL}/docs",
        "Prediction":    f"{PREDICTION_URL}/docs",
        "Strategy":      f"{STRATEGY_URL}/docs",
        "Risk Manager":  f"{RISK_URL}/status",
        "Execution":     f"{EXECUTION_URL}/positions",
        "News":          f"{NEWS_URL}/health",
        "Event Bus":     f"{EVENT_BUS_URL}/health",
        "Notifications": f"{NOTIFY_URL}/status",
    }
    print(f"\n{'='*52}")
    print(f"  AI F&O Orchestrator v3  |  {SYMBOL}  |  {'DRY RUN' if DRY_RUN else 'LIVE'}")
    print(f"  Max positions: {MAX_POSITIONS}")
    print(f"{'='*52}")
    all_ok = True
    for name, url in checks.items():
        try:
            r = requests.get(url, timeout=3)
            ok = r.status_code == 200
        except Exception:
            ok = False
        print(f"  [{'OK' if ok else 'DOWN'}] {name}")
        if not ok and name in ("Market Data", "Features", "Prediction", "Strategy", "Risk Manager"):
            all_ok = False  # only core services are blocking
    print()
    return all_ok


# ─── Multi-Timeframe Confluence ────────────────────────────────────────────────
def get_mtf_confluence(symbol: str) -> dict:
    """
    Fetch features+prediction for 1m, 5m, 15m in parallel.
    Returns: {
      "agreement": "bullish"|"bearish"|"neutral"|"mixed",
      "score":  0-3 (how many timeframes agree),
      "predictions": {"1m": ..., "5m": ..., "15m": ...},
      "candles_1m": [...]
    }
    """
    # Get multi-timeframe candles
    mtf = _get(f"{MARKET_URL}/multi_timeframe", {"symbol": symbol}, timeout=15)
    if not mtf:
        return {"agreement": "mixed", "score": 0, "predictions": {}, "candles_1m": []}

    candles_map = mtf.get("candles", {})

    def _predict_for_tf(label):
        candles = candles_map.get(label, {}).get("data", [])
        if len(candles) < 20:
            return label, None
        feat_resp = _post(f"{FEATURE_URL}/generate_features",
                          {"data": candles, "symbol": symbol}, timeout=10)
        if not feat_resp:
            return label, None
        pred_resp = _post(f"{PREDICTION_URL}/predict",
                          {"features": feat_resp["features"]}, timeout=5)
        if not pred_resp:
            return label, None
        return label, {"trend": pred_resp["trend"], "confidence": pred_resp["confidence"],
                       "prediction": pred_resp["prediction"]}

    predictions = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_predict_for_tf, tf): tf for tf in ["1m", "5m", "15m"]}
        for fut in as_completed(futures):
            label, result = fut.result()
            predictions[label] = result

    # Count agreement
    trends = [v["trend"] for v in predictions.values() if v is not None]
    if not trends:
        return {"agreement": "mixed", "score": 0, "predictions": predictions,
                "candles_1m": candles_map.get("1m", {}).get("data", [])}

    from collections import Counter
    counts = Counter(trends)
    top_trend, top_count = counts.most_common(1)[0]
    agreement = top_trend if top_count >= 2 else "mixed"

    return {
        "agreement":  agreement,
        "score":      top_count,
        "predictions": predictions,
        "candles_1m": candles_map.get("1m", {}).get("data", []),
    }


# ─── News Filter ───────────────────────────────────────────────────────────────
def get_news_impact(symbol: str) -> float:
    """
    Returns 0.0–1.0 impact score from last 15 min news.
    High impact (≥0.5) = risky to trade → confidence penalty applied upstream.
    """
    resp = _get(f"{NEWS_URL}/summary", {"symbol": symbol, "lookback_minutes": 15})
    if resp and resp.get("data"):
        d = resp["data"]
        return float(d.get("avg_impact", 0.0))
    return 0.0


# ─── Position monitor (single position entry) ─────────────────────────────────
def monitor_position(pos: dict, current_price: float, now: datetime) -> dict | None:
    """
    Returns updated PnL dict. Returns special key '_exit_reason' if should exit.
    """
    entry     = pos["order"]["entry_price"]
    action    = pos["order"]["action"]
    sl_pct    = pos["sl_pct"]
    target_pct= pos["target_pct"]
    trail_act = pos.get("trail_activate_pct", 0.35)
    is_sell   = "SELL" in action or "CONDOR" in action

    spot_entry = pos["spot_at_entry"]
    price_chg  = (current_price - spot_entry) / spot_entry

    if is_sell:
        pnl_pct = (-abs(price_chg) * 10 + 0.05) if ("STRANGLE" in action or "CONDOR" in action) \
                  else (price_chg * 10 if "PUT" in action else -price_chg * 10)
    else:
        pnl_pct = price_chg * 10 if "CALL" in action else -price_chg * 10

    # Trailing stop
    if not pos.get("trailing_active") and pnl_pct >= trail_act:
        pos["trailing_active"] = True
        pos["trail_high"]      = pnl_pct
        print(f"  [{pos['order']['order_id']}] Trail activated at +{pnl_pct*100:.1f}%")

    if pos.get("trailing_active"):
        if pnl_pct > pos.get("trail_high", pnl_pct):
            pos["trail_high"] = pnl_pct
        if pnl_pct <= pos["trail_high"] - 0.10:
            pos["_exit_reason"] = "TRAIL_SL"

    # Fixed triggers
    exit_reason = None
    if   pos.get("_exit_reason"):        exit_reason = pos["_exit_reason"]
    elif pnl_pct >= target_pct:          exit_reason = "TARGET_HIT"
    elif pnl_pct <= -sl_pct:             exit_reason = "SL_HIT"
    else:
        elapsed = (now - pos["entry_time"]).total_seconds() / 60
        if elapsed >= pos["time_exit_mins"]:
            exit_reason = "TIME_EXIT"

    qty = pos["order"]["qty"]
    pnl_amount = round(entry * qty * pnl_pct, 2)
    pos["_current_pnl_pct"]    = round(pnl_pct * 100, 2)
    pos["_current_pnl_amount"] = pnl_amount
    if exit_reason:
        pos["_exit_reason"] = exit_reason
    return pos


# ─── Execute exit ──────────────────────────────────────────────────────────────
def execute_exit(pos: dict, reason: str, now: datetime):
    entry_price = pos["order"]["entry_price"]
    pnl_pct     = pos.get("_current_pnl_pct", 0) / 100
    exit_price  = round(entry_price * (1 + pnl_pct), 2)
    held_mins   = int((now - pos["entry_time"]).total_seconds() / 60)

    if DRY_RUN:
        print(f"  [DRY] Would exit {pos['order']['order_id']} reason={reason}")
        return None

    result = _post(f"{EXECUTION_URL}/exit_order", {
        "order_id":    pos["order"]["order_id"],
        "exit_price":  exit_price,
        "reason":      reason,
        "hold_minutes": held_mins,
    })
    if not result:
        return None

    closed = result["order"]
    net_pnl = closed["pnl"]
    print(f"  => CLOSED {closed['order_id']} | Net PnL: Rs{net_pnl:+,.0f} | {reason}")

    # Log + Update risk + Notify
    log_trade({"time": now.isoformat(), "order": closed, "reason": reason})
    _post(f"{RISK_URL}/update_pnl", {"pnl": net_pnl,
                                     "exposure_released": entry_price * pos["order"]["qty"]})
    notify(reason, closed, pnl=net_pnl, pnl_pct=net_pnl / (entry_price * pos["order"]["qty"] + 1e-9),
           reason=reason)
    publish_event("TRADE_CLOSED", "execution", {
        "order_id": closed["order_id"], "pnl": net_pnl, "reason": reason
    })
    return closed


# ─── Main trading cycle ────────────────────────────────────────────────────────
def run_trading_cycle():
    global active_trades
    now = datetime.now()
    n_active = len(active_trades)
    print(f"\n--- {now.strftime('%H:%M:%S')} | {SYMBOL} | Open: {n_active}/{MAX_POSITIONS} ---")

    publish_event("CYCLE_START", "orchestrator", {"symbol": SYMBOL, "positions": n_active})

    # ── Step 1: Fast spot price ────────────────────────────────────────────────
    quote = _get(f"{MARKET_URL}/quote", {"symbol": SYMBOL})
    if not quote:
        print("  [ERR] Spot price failed")
        return
    current_price = float(quote["price"])

    current_state = {
        "symbol": SYMBOL, "current_price": current_price,
        "is_live": True, "dry_run": DRY_RUN,
        "timestamp": now.isoformat(),
        "active_trades": [], "prediction": None,
    }

    # ── Step 2: Monitor open positions ────────────────────────────────────────
    remaining = []
    for pos in active_trades:
        pos = monitor_position(pos, current_price, now)
        exit_reason = pos.get("_exit_reason")

        if exit_reason:
            print(f"  [{pos['order']['order_id']}] Exit triggered: {exit_reason}")
            execute_exit(pos, exit_reason, now)
        else:
            remaining.append(pos)
            current_state["active_trades"].append({
                "order_id":   pos["order"]["order_id"],
                "action":     pos["order"]["action"],
                "symbol":     pos["order"]["symbol"],
                "entry_price":pos["order"]["entry_price"],
                "qty":        pos["order"]["qty"],
                "pnl_pct":    pos.get("_current_pnl_pct", 0),
                "pnl_amount": pos.get("_current_pnl_amount", 0),
            })

    active_trades = remaining

    # If all slots full, just monitor
    if len(active_trades) >= MAX_POSITIONS:
        save_live_state(current_state)
        print(f"  Max positions ({MAX_POSITIONS}) active — monitoring only.")
        return

    # ── Step 3: News impact filter ────────────────────────────────────────────
    news_impact = get_news_impact(SYMBOL)
    if news_impact >= 0.7:
        reason = f"High-impact news detected (score={news_impact:.2f}) — skipping entry"
        print(f"  [NEWS] {reason}")
        publish_event("SIGNAL_FILTERED", "news_filter", {"reason": reason, "impact": news_impact})
        save_live_state(current_state)
        return

    # ── Step 4: Multi-timeframe confluence ───────────────────────────────────
    print("  Fetching multi-timeframe data (1m / 5m / 15m)...")
    mtf = get_mtf_confluence(SYMBOL)
    agreement = mtf["agreement"]
    score     = mtf["score"]
    print(f"  MTF agreement: {agreement.upper()} ({score}/3 timeframes)")

    candles_1m = mtf["candles_1m"]

    if agreement == "mixed" or score < 2:
        reason = f"No MTF confluence ({score}/3). Timeframes disagree."
        print(f"  [MTF] {reason}")
        publish_event("SIGNAL_FILTERED", "mtf_filter", {"reason": reason, "score": score})
        save_live_state(current_state)
        return

    # Use 1m prediction from MTF for features
    pred_1m = mtf["predictions"].get("1m")
    if not pred_1m:
        print("  [ERR] 1m prediction failed")
        save_live_state(current_state)
        return

    confidence = pred_1m["confidence"]
    trend      = pred_1m["trend"]
    print(f"  AI ({agreement}) | Conf: {confidence:.2f} | News impact: {news_impact:.2f}")

    current_state["prediction"] = {
        "trend":      trend,
        "confidence": confidence,
        "mtf_score":  score,
        "agreement":  agreement,
        "news_impact": news_impact,
    }
    save_live_state(current_state)

    # ── Step 5: Get features for strategy ────────────────────────────────────
    if not candles_1m:
        return

    feat_resp = _post(f"{FEATURE_URL}/generate_features", {"data": candles_1m, "symbol": SYMBOL})
    if not feat_resp:
        return
    features = feat_resp["features"]
    features["news_impact_score"] = float(news_impact)  # inject news into features

    # ── Step 6: Strategy signal ───────────────────────────────────────────────
    sig_resp = _post(f"{STRATEGY_URL}/generate_signal", {
        "prediction": pred_1m["prediction"],
        "trend": trend, "confidence": confidence, "features": features
    })
    if not sig_resp:
        return
    signal = sig_resp.get("signal")
    if signal == "no_trade" or not isinstance(signal, dict):
        reason = sig_resp.get("reason", "Strategy filter")
        print(f"  => No trade: {reason}")
        publish_event("SIGNAL_FILTERED", "strategy", {"reason": reason})
        return

    action = signal["action"]

    # Check we don't already have the same instrument open
    open_actions = [p["order"]["action"] for p in active_trades]
    if action in open_actions or any(action.split("_")[-1] in oa for oa in open_actions):
        print(f"  => Already have a {action} position open. Skipping duplicate.")
        return

    print(f"  Signal => {action} | SL:{signal['sl_pct']:.0%} Tgt:{signal['target_pct']:.0%}")

    # ── Step 7: Risk check + sizing ──────────────────────────────────────────
    risk_check = _get(f"{RISK_URL}/check_allowed")
    if not risk_check or not risk_check.get("allowed"):
        reason = risk_check.get("reason", "Risk block") if risk_check else "Risk MCP offline"
        print(f"  => Risk block: {reason}")
        publish_event("SIGNAL_FILTERED", "risk", {"reason": reason})
        return

    qty_resp = _post(f"{RISK_URL}/calculate_quantity", {
        "action":  action,
        "sl_pct":  signal["sl_pct"],
        "atr_pct": signal.get("atr_pct", 0.7),
    })
    qty = qty_resp.get("quantity", 0) if qty_resp else 0
    if qty <= 0:
        reason = qty_resp.get("reason", "Qty calc failed") if qty_resp else "Risk timeout"
        print(f"  => Qty=0: {reason}")
        return

    print(f"  Qty => {qty} | Kelly={qty_resp.get('kelly_fraction', 0):.3f}")

    # ── Step 8: Execute entry ─────────────────────────────────────────────────
    instr = f"{SYMBOL} {'CE' if 'CALL' in action else 'PE' if 'PUT' in action else action}"

    if DRY_RUN:
        print(f"  [DRY RUN] Would place: {instr} x{qty}")
        return

    order_resp = _post(f"{EXECUTION_URL}/place_order", {
        "symbol": instr, "qty": qty, "action": action,
        "atr_pct": signal.get("atr_pct", 0.7),
        "estimated_premium": qty_resp.get("premium_est", 100),
    })

    if not order_resp:
        print("  [ERR] Order placement failed")
        return

    order = order_resp["order"]
    print(f"  ORDER: {order['order_id']} @ Rs{order['entry_price']:.2f}")

    log_trade({"time": now.isoformat(), "order": order, "signal": signal,
               "mtf_score": score, "news_impact": news_impact})
    notify("ENTRY", order, confidence=confidence)
    publish_event("TRADE_PLACED", "execution", {
        "order_id": order["order_id"], "action": action, "qty": qty,
        "mtf_score": score, "confidence": confidence
    })

    new_pos = {
        "order":             order,
        "entry_time":        now,
        "spot_at_entry":     current_price,
        "sl_pct":            signal["sl_pct"],
        "target_pct":        signal["target_pct"],
        "time_exit_mins":    signal["time_exit_mins"],
        "trail_activate_pct":signal.get("trail_activate_pct", 0.35),
        "trailing_active":   False,
        "trail_high":        0.0,
    }
    active_trades.append(new_pos)

    current_state["active_trades"].append({
        "order_id": order["order_id"], "action": action,
        "symbol": instr, "entry_price": order["entry_price"],
        "qty": qty, "pnl_pct": 0, "pnl_amount": 0,
    })
    save_live_state(current_state)


# ─── EOD Summary ───────────────────────────────────────────────────────────────
def send_eod_summary():
    metrics = _get(f"{DASHBOARD_URL}/api/metrics")
    risk    = _get(f"{RISK_URL}/summary")
    if metrics and risk:
        _post(f"{NOTIFY_URL}/cycle_summary", {
            "symbol":       SYMBOL,
            "total_trades": metrics.get("total_trades", 0),
            "win_rate":     metrics.get("win_rate", 0),
            "net_pnl":      metrics.get("net_pnl", 0),
            "max_drawdown": metrics.get("max_drawdown", 0),
            "capital":      risk.get("data", {}).get("capital", 0),
        })
        print("  EOD summary sent.")


# ─── Main loop ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not health_check():
        print("[WARN] Some core services may be down. Check above.\n")

    print(f"Starting trading loop — {SYMBOL}\n")
    last_eod = None

    while True:
        try:
            run_trading_cycle()

            # EOD summary at 15:30
            now_t = datetime.now()
            if now_t.hour == 15 and now_t.minute >= 30 and last_eod != now_t.date():
                send_eod_summary()
                last_eod = now_t.date()

        except Exception as e:
            print(f"  [CRITICAL] {e}")

        print(f"  Sleeping 15s...")
        time.sleep(15)
