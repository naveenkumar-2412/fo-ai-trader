from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import date
import math

app = FastAPI(title="Risk Manager MCP", version="2.0.0")

# ─── Daily state reset per calendar day ────────────────────────────────────
_today = date.today()
daily_state = {
    "date": str(_today),
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "consecutive_losses": 0,
    "daily_pnl": 0.0,
    "daily_pnl_pct": 0.0,
    "capital": 500000,          # Starting capital: 5L
    "max_trades": 8,
    "max_daily_loss_pct": -3.0, # Max 3% daily loss cap
    "max_consec_losses": 3,
    "max_exposure_pct": 30.0,   # Max 30% capital exposed at any time
    "current_exposure": 0.0,
}

def _auto_reset():
    """Reset daily counters if a new trading day begins."""
    global daily_state
    today = str(date.today())
    if daily_state["date"] != today:
        daily_state.update({
            "date": today,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "consecutive_losses": 0,
            "daily_pnl": 0.0,
            "daily_pnl_pct": 0.0,
            "max_trades": 8,
            "current_exposure": 0.0,
        })


class TradeResult(BaseModel):
    pnl: float
    exposure_released: float = 0.0


class SignalValidate(BaseModel):
    action: str
    sl_pct: float
    atr_pct: float = 0.7        # From feature engine


@app.get("/check_allowed")
def check_trading_allowed():
    _auto_reset()
    allowed = True
    reason = None

    if daily_state["total_trades"] >= daily_state["max_trades"]:
        allowed = False
        reason = f"Max trades limit reached ({daily_state['max_trades']} trades)."

    if daily_state["consecutive_losses"] >= daily_state["max_consec_losses"]:
        allowed = False
        reason = f"Hit {daily_state['max_consec_losses']} consecutive losses — cooling down."

    if daily_state["daily_pnl_pct"] <= daily_state["max_daily_loss_pct"]:
        allowed = False
        reason = f"Daily loss limit breached ({daily_state['daily_pnl_pct']:.2f}%)."

    exposure_used_pct = (daily_state["current_exposure"] / daily_state["capital"]) * 100
    if exposure_used_pct >= daily_state["max_exposure_pct"]:
        allowed = False
        reason = f"Max capital exposure ({exposure_used_pct:.1f}%) reached."

    return {
        "allowed": allowed,
        "reason": reason,
        "daily_state": {
            "trades": daily_state["total_trades"],
            "daily_pnl_pct": round(daily_state["daily_pnl_pct"], 3),
            "exposure_pct": round(exposure_used_pct, 2),
            "consecutive_losses": daily_state["consecutive_losses"],
        }
    }


@app.post("/calculate_quantity")
def calculate_quantity(payload: SignalValidate):
    """
    Kelly Criterion-based position sizing with margin guard.

    Kelly f* = (p * b - q) / b
        p = historical win_rate (default 0.60 if no trades yet)
        q = 1 - p
        b = average win / average loss ratio (default 1.5)

    We use fractional Kelly (25% of full Kelly) to reduce variance.
    """
    _auto_reset()
    is_selling = "SELL" in payload.action
    capital = daily_state["capital"]

    # ── Kelly sizing ────────────────────────────────────────────────────────
    total = daily_state["total_trades"]
    wins = daily_state["winning_trades"]
    p = (wins / total) if total >= 5 else 0.60   # Fall back to 60% prior
    q = 1 - p
    b = 1.5  # Reward/Risk ratio baseline

    kelly_f = (p * b - q) / b
    kelly_f = max(0.0, min(kelly_f, 0.25))  # Clamp 0–25%
    fractional_kelly = kelly_f * 0.25        # Use 25% of full Kelly

    risk_amount = capital * fractional_kelly

    # ── ATR-adjusted premium estimate ──────────────────────────────────────
    # Approx. ATM premium ~ 2x ATR of the underlying (rough heuristic)
    atr_pct = payload.atr_pct
    spot_approx = 22500  # approx NIFTY spot
    premium = max(40, (atr_pct / 100) * spot_approx * 2.0)

    risk_per_unit = premium * payload.sl_pct
    lot_size = 50
    risk_per_lot = risk_per_unit * lot_size

    lots_by_risk = max(1, int(risk_amount // risk_per_lot)) if risk_per_lot > 0 else 1

    # ── Margin guard for selling ────────────────────────────────────────────
    if is_selling:
        margin_per_lot = 130000 if "STRANGLE" in payload.action else 105000
        max_lots_by_margin = max(0, int((capital * 0.30 - daily_state["current_exposure"]) // margin_per_lot))
        if max_lots_by_margin == 0:
            return {"quantity": 0, "risk_amount": 0, "reason": "Insufficient free margin for option selling"}
        lots = min(lots_by_risk, max_lots_by_margin)
        exposure = lots * margin_per_lot
    else:
        # Buying: exposure is only the premium paid
        max_lots_by_exposure = max(1, int((capital * 0.10) // (premium * lot_size)))
        lots = min(lots_by_risk, max_lots_by_exposure)
        exposure = lots * premium * lot_size

    qty = lots * lot_size
    actual_risk = lots * risk_per_lot

    # Track exposure
    daily_state["current_exposure"] += exposure

    return {
        "quantity": qty,
        "lots": lots,
        "risk_amount": round(actual_risk, 2),
        "exposure_added": round(exposure, 2),
        "kelly_fraction": round(fractional_kelly, 4),
        "estimated_premium": round(premium, 2),
    }


@app.post("/update_pnl")
def update_pnl(payload: TradeResult):
    _auto_reset()
    pnl = payload.pnl

    daily_state["total_trades"] += 1
    daily_state["daily_pnl"] += pnl
    daily_state["daily_pnl_pct"] += (pnl / daily_state["capital"]) * 100
    daily_state["current_exposure"] = max(0.0, daily_state["current_exposure"] - payload.exposure_released)

    if pnl > 0:
        daily_state["winning_trades"] += 1
        daily_state["consecutive_losses"] = 0
    else:
        daily_state["losing_trades"] += 1
        daily_state["consecutive_losses"] += 1

    daily_state["capital"] += pnl

    return {"status": "success", "daily_state": daily_state}


@app.get("/status")
def get_status():
    _auto_reset()
    return {"status": "success", "daily_state": daily_state}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
