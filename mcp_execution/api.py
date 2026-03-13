from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import random
import math
from datetime import datetime

app = FastAPI(title="Execution MCP", version="2.0.0")


class OrderData(BaseModel):
    symbol: str
    qty: int
    order_type: str = "MARKET"
    action: str
    estimated_premium: Optional[float] = None  # From risk manager
    atr_pct: Optional[float] = 0.7


class ExitData(BaseModel):
    order_id: str
    exit_price: Optional[float] = None
    reason: Optional[str] = "MANUAL"


# Register of all placed orders
active_orders: dict = {}
closed_orders: list = []


def simulate_realistic_premium(action: str, atr_pct: float = 0.7, estimated_premium: Optional[float] = None) -> float:
    """
    Simulate realistic NSE options entry premium.
    Uses estimated_premium if available, otherwise derives from ATR.

    Also includes a slippage factor (0.5–2.0% of premium for market orders).
    """
    spot_approx = 22500
    if estimated_premium and estimated_premium > 0:
        base_premium = estimated_premium
    else:
        # ATM option premium ~ 2x daily ATR of the underlying (very rough Black-Scholes proxy)
        base_premium = (atr_pct / 100) * spot_approx * 2.0
        base_premium = max(40.0, base_premium)

    # Market order slippage: 0.5% to 1.5% of premium
    slippage_pct = random.uniform(0.005, 0.015)
    premium_with_slippage = base_premium * (1 + slippage_pct)
    return round(premium_with_slippage, 2)


def compute_brokerage(qty: int, premium: float, is_sell: bool) -> float:
    """
    Simulate realistic NSE F&O brokerage + taxes.
    Roughly:
      - Zerodha style: flat ₹20/order + 0.03% of turnover
      - STT on sell side only (0.05% of premium * qty)
      - GST 18% on brokerage
    """
    turnover = premium * qty
    brokerage = min(20.0, 0.0003 * turnover)
    stt = 0.0005 * turnover if is_sell else 0.0
    gst = brokerage * 0.18
    sebi = turnover * 0.000001  # SEBI charges
    return round(brokerage + stt + gst + sebi, 2)


@app.post("/place_order")
def place_order(payload: OrderData):
    try:
        action = payload.action
        is_sell = action.startswith("SELL")
        atr_pct = payload.atr_pct or 0.7

        entry_premium = simulate_realistic_premium(action, atr_pct, payload.estimated_premium)
        brokerage = compute_brokerage(payload.qty, entry_premium, is_sell)

        order_id = f"ORD_{random.randint(10000, 99999)}"
        placed_at = datetime.now().isoformat()

        order = {
            "order_id": order_id,
            "symbol": payload.symbol,
            "qty": payload.qty,
            "action": action,
            "order_type": payload.order_type,
            "entry_price": entry_premium,
            "entry_brokerage": brokerage,
            "status": "OPEN",
            "placed_at": placed_at,
            "message": (
                f"{'SELL' if is_sell else 'BUY'} {payload.qty} units of "
                f"{payload.symbol} @ ₹{entry_premium:.2f} (slippage incl.)"
            ),
        }
        active_orders[order_id] = order

        return {"status": "success", "order": order}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/exit_order")
def exit_order(payload: ExitData):
    order = active_orders.get(payload.order_id)
    if not order or order["status"] != "OPEN":
        raise HTTPException(status_code=404, detail="Active order not found")

    try:
        entry = order["entry_price"]
        qty = order["qty"]
        action = order["action"]
        is_sell = action.startswith("SELL")

        if payload.exit_price is not None:
            exit_price = payload.exit_price
        else:
            # Default fallback: drift price ±15% from entry (random)
            drift = random.uniform(-0.15, 0.15)
            exit_price = round(entry * (1 + drift), 2)

        exit_brokerage = compute_brokerage(qty, exit_price, not is_sell)

        # PnL calculation:
        # BUY options: profit when price goes up = (exit - entry) * qty
        # SELL options: profit when price goes down = (entry - exit) * qty
        raw_pnl = (exit_price - entry) * qty
        if is_sell:
            raw_pnl = -raw_pnl

        total_costs = order["entry_brokerage"] + exit_brokerage
        net_pnl = round(raw_pnl - total_costs, 2)

        order.update({
            "status": "CLOSED",
            "exit_price": exit_price,
            "exit_brokerage": exit_brokerage,
            "total_brokerage": round(total_costs, 2),
            "gross_pnl": round(raw_pnl, 2),
            "pnl": net_pnl,
            "exit_reason": payload.reason,
            "closed_at": datetime.now().isoformat(),
        })
        closed_orders.append(order)
        del active_orders[payload.order_id]

        return {"status": "success", "order": order}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/positions")
def get_positions():
    return {"status": "success", "active_orders": list(active_orders.values())}


@app.get("/history")
def get_history(limit: int = 20):
    return {"status": "success", "closed_orders": closed_orders[-limit:]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
