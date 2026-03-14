from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import random
import json
import os
import math
from datetime import datetime, date

app = FastAPI(title="Execution MCP", version="2.1.0")

STATE_FILE = "../execution_state.json"


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
                return s.get("active_orders", {}), s.get("closed_orders", [])
        except Exception:
            pass
    return {}, []


def _save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"active_orders": active_orders, "closed_orders": closed_orders[-200:]}, f, default=str, indent=2)
    except Exception as e:
        print(f"State save failed: {e}")


active_orders, closed_orders = _load_state()


# ─── Pydantic Models ───────────────────────────────────────────────────────────
class OrderData(BaseModel):
    symbol:            str
    qty:               int
    order_type:        str = "MARKET"
    action:            str
    estimated_premium: Optional[float] = None
    atr_pct:           Optional[float] = 0.7
    hold_minutes:      Optional[int]   = 0   # Time held so far (for theta decay)


class ExitData(BaseModel):
    order_id:   str
    exit_price: Optional[float] = None
    reason:     Optional[str]   = "MANUAL"
    hold_minutes: Optional[int] = 20


# ─── Helpers ───────────────────────────────────────────────────────────────────
def _premium(action: str, atr_pct: float, est: Optional[float]) -> float:
    if est and est > 0:
        base = est
    else:
        spot = 22500
        base = max(40.0, (atr_pct / 100) * spot * 2.0)
    slip = random.uniform(0.005, 0.015)
    return round(base * (1 + slip), 2)


def _brokerage(qty: int, price: float, is_sell: bool) -> float:
    tv = price * qty
    b  = min(20.0, 0.0003 * tv)
    stt = 0.0005 * tv if is_sell else 0.0
    return round(b + stt + b * 0.18 + tv * 1e-6, 2)


def _theta_decay_factor(hold_minutes: int, total_minutes: int = 45) -> float:
    """
    Models theta decay benefit for sellers:
    - At 0 min held: 0% of max theta collected
    - At target (e.g., 45 min): full theta collected = premium_sold * target_pct
    We use a square-root curve: slower decay initially, faster near expiry.
    """
    ratio = min(1.0, hold_minutes / max(total_minutes, 1))
    return round(math.sqrt(ratio), 4)


def _approximate_greeks(spot_approx: float, strike: float, atr_pct: float, dte_days: int = 7) -> dict:
    """Rough delta/gamma/theta from ATM-centered BS heuristics."""
    moneyness = (spot_approx - strike) / (spot_approx + 1e-9)
    delta_call = round(max(0.05, min(0.95, 0.5 + 0.4 * moneyness)), 3)
    delta_put  = round(delta_call - 1, 3)
    gamma      = round(max(0.002, 0.05 / (abs(moneyness) + 0.5)), 4)
    sigma_day  = atr_pct / 100
    theta_day  = round(-0.5 * sigma_day * spot_approx * gamma, 2)  # simplified
    return {"delta_call": delta_call, "delta_put": delta_put, "gamma": gamma, "theta_per_day": theta_day}


# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.post("/place_order")
def place_order(payload: OrderData):
    try:
        is_sell     = payload.action.startswith("SELL") or "CONDOR" in payload.action
        entry_price = _premium(payload.action, payload.atr_pct or 0.7, payload.estimated_premium)
        broker_in   = _brokerage(payload.qty, entry_price, is_sell)

        order_id = f"ORD-{date.today().strftime('%m%d')}-{random.randint(1000,9999)}"

        # Estimate ATM strike from action context
        spot_map = {"BANK": 48000, "FIN": 23000}
        spot = next((v for k, v in spot_map.items() if k in payload.symbol), 22500)
        strike = round(spot / 50) * 50  # ATM
        greeks = _approximate_greeks(spot, strike, payload.atr_pct or 0.7)

        order = {
            "order_id":    order_id,
            "symbol":      payload.symbol,
            "qty":         payload.qty,
            "action":      payload.action,
            "order_type":  payload.order_type,
            "entry_price": entry_price,
            "brokerage_in": broker_in,
            "greeks":      greeks,
            "status":      "OPEN",
            "placed_at":   datetime.now().isoformat(),
        }
        active_orders[order_id] = order
        _save_state()

        return {"status": "success", "order": order}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/exit_order")
def exit_order(payload: ExitData):
    order = active_orders.get(payload.order_id)
    if not order or order["status"] != "OPEN":
        raise HTTPException(status_code=404, detail="Order not found or already closed")

    try:
        entry     = order["entry_price"]
        qty       = order["qty"]
        is_sell   = order["action"].startswith("SELL") or "CONDOR" in order["action"]
        hold_mins = payload.hold_minutes or 20

        if payload.exit_price is not None:
            exit_price = payload.exit_price
        elif is_sell:
            # Theta decay for sellers: price decreases as time passes
            decay = _theta_decay_factor(hold_mins)
            exit_price = round(entry * (1 - 0.60 * decay + random.uniform(-0.05, 0.05)), 2)
        else:
            drift = random.uniform(-0.15, 0.15)
            exit_price = round(entry * (1 + drift), 2)

        broker_out = _brokerage(qty, exit_price, not is_sell)

        raw_pnl = (exit_price - entry) * qty
        if is_sell:
            raw_pnl = -raw_pnl  # sellers profit when premium falls

        net_pnl = round(raw_pnl - order["brokerage_in"] - broker_out, 2)

        order.update({
            "status":       "CLOSED",
            "exit_price":   exit_price,
            "brokerage_out": broker_out,
            "total_cost":   round(order["brokerage_in"] + broker_out, 2),
            "gross_pnl":    round(raw_pnl, 2),
            "pnl":          net_pnl,
            "exit_reason":  payload.reason,
            "closed_at":    datetime.now().isoformat(),
            "held_minutes": hold_mins,
        })

        closed_orders.append(order)
        del active_orders[payload.order_id]
        _save_state()

        return {"status": "success", "order": order}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/positions")
def get_positions():
    return {"status": "success", "active_orders": list(active_orders.values())}


@app.get("/history")
def get_history(limit: int = 20):
    return {"status": "success", "closed_orders": closed_orders[-limit:]}


@app.get("/today_summary")
def today_summary():
    today = date.today().isoformat()
    today_trades = [o for o in closed_orders if o.get("closed_at", "").startswith(today)]
    gross = sum(o.get("gross_pnl", 0) for o in today_trades)
    costs = sum(o.get("total_cost", 0) for o in today_trades)
    net   = gross - costs
    return {
        "status": "success",
        "data": {
            "date":         today,
            "trade_count":  len(today_trades),
            "gross_pnl":    round(gross, 2),
            "total_costs":  round(costs, 2),
            "net_pnl":      round(net, 2),
            "open_trades":  len(active_orders),
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
