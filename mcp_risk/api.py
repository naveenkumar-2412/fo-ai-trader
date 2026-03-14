from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from datetime import date
import math

app = FastAPI(title="Risk Manager MCP", version="2.1.0")

# ─── Per-instrument config ─────────────────────────────────────────────────────
INSTRUMENT_CONFIG = {
    "NIFTY":     {"lot_size": 50,  "span_margin":  90_000, "exposure_margin": 15_000},
    "BANKNIFTY": {"lot_size": 15,  "span_margin": 130_000, "exposure_margin": 20_000},
    "FINIFTY":   {"lot_size": 40,  "span_margin":  60_000, "exposure_margin": 10_000},
    "DEFAULT":   {"lot_size": 50,  "span_margin": 100_000, "exposure_margin": 15_000},
}

# ─── Daily state (auto-resets per calendar day) ────────────────────────────────
_today = str(date.today())
daily_state = {
    "date":               _today,
    "total_trades":       0,
    "winning_trades":     0,
    "losing_trades":      0,
    "consecutive_losses": 0,
    "daily_pnl":          0.0,
    "daily_pnl_pct":      0.0,
    "capital":            500_000,   # Starting: 5 Lakhs
    "peak_capital":       500_000,
    "current_exposure":   0.0,
    # Guardrails
    "max_trades":         8,
    "max_daily_loss_pct": -3.0,
    "max_consec_losses":  3,
    "max_exposure_pct":   30.0,
}


def _auto_reset():
    global daily_state
    today = str(date.today())
    if daily_state["date"] != today:
        daily_state.update({
            "date":               today,
            "total_trades":       0,
            "winning_trades":     0,
            "losing_trades":      0,
            "consecutive_losses": 0,
            "daily_pnl":          0.0,
            "daily_pnl_pct":      0.0,
            "current_exposure":   0.0,   # ← full reset on new day
        })


class TradeResult(BaseModel):
    pnl: float
    exposure_released: float = 0.0


class SignalValidate(BaseModel):
    action:  str
    sl_pct:  float
    atr_pct: float = Field(default=0.7, description="From the strategy signal")


# ─── Helpers ───────────────────────────────────────────────────────────────────
def _instrument_from_action(action: str) -> str:
    if "BANK" in action: return "BANKNIFTY"
    if "FIN"  in action: return "FINIFTY"
    return "NIFTY"


def _kelly_fraction(p: float) -> float:
    """Fractional Kelly (25% of full Kelly, clamped to 0–25%)."""
    q = 1 - p
    b = 1.5   # avg win / avg loss ratio assumption
    kelly = max(0.0, (p * b - q) / b)
    return min(kelly * 0.25, 0.25)


# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/check_allowed")
def check_trading_allowed():
    _auto_reset()
    allowed, reason = True, None
    cap = daily_state["capital"]

    if daily_state["total_trades"] >= daily_state["max_trades"]:
        allowed, reason = False, f"Max {daily_state['max_trades']} trades/day reached"

    elif daily_state["consecutive_losses"] >= daily_state["max_consec_losses"]:
        allowed, reason = False, f"{daily_state['max_consec_losses']} consecutive losses — cooling down"

    elif daily_state["daily_pnl_pct"] <= daily_state["max_daily_loss_pct"]:
        allowed, reason = False, f"Daily loss limit ({daily_state['daily_pnl_pct']:.2f}%) breached"

    else:
        used_pct = (daily_state["current_exposure"] / cap) * 100 if cap > 0 else 0
        if used_pct >= daily_state["max_exposure_pct"]:
            allowed, reason = False, f"Exposure limit ({used_pct:.1f}%) reached"

    return {
        "allowed": allowed,
        "reason":  reason,
        "snapshot": {
            "trades_today":       daily_state["total_trades"],
            "trades_left":        daily_state["max_trades"] - daily_state["total_trades"],
            "daily_pnl_pct":      round(daily_state["daily_pnl_pct"], 3),
            "exposure_pct":       round((daily_state["current_exposure"] / daily_state["capital"]) * 100, 2) if daily_state["capital"] > 0 else 0,
            "consecutive_losses": daily_state["consecutive_losses"],
        }
    }


@app.post("/calculate_quantity")
def calculate_quantity(payload: SignalValidate):
    _auto_reset()
    is_sell   = "SELL" in payload.action or "CONDOR" in payload.action
    capital   = daily_state["capital"]
    atr_pct   = max(0.1, payload.atr_pct)

    # Instrument lookup
    instr = _instrument_from_action(payload.action)
    cfg   = INSTRUMENT_CONFIG.get(instr, INSTRUMENT_CONFIG["DEFAULT"])
    lot_size = cfg["lot_size"]
    span     = cfg["span_margin"]

    # Kelly-based risk money
    total = daily_state["total_trades"]
    wins  = daily_state["winning_trades"]
    p     = (wins / total) if total >= 5 else 0.60
    frac  = _kelly_fraction(p)
    risk_cash = capital * frac

    # ATR-adjusted ATM premium estimate
    spot_map = {"NIFTY": 22500, "BANKNIFTY": 48000, "FINIFTY": 23000}
    spot_approx = spot_map.get(instr, 22500)
    premium = max(40.0, (atr_pct / 100) * spot_approx * 2.0)

    risk_per_lot = premium * payload.sl_pct * lot_size
    lots_by_risk = max(1, int(risk_cash // risk_per_lot)) if risk_per_lot > 0 else 1

    # Margin/exposure guard
    if is_sell:
        free_margin = capital * (daily_state["max_exposure_pct"] / 100) - daily_state["current_exposure"]
        max_lots_margin = max(0, int(free_margin // span))
        if max_lots_margin == 0:
            return {"quantity": 0, "lots": 0, "risk_amount": 0,
                    "reason": f"Insufficient free margin for {instr} selling"}
        lots = min(lots_by_risk, max_lots_margin)
        exposure = lots * span
    else:
        # Buying: exposure = premium paid
        max_lots_buy = max(1, int((capital * 0.10) // (premium * lot_size)))
        lots = min(lots_by_risk, max_lots_buy)
        exposure = lots * premium * lot_size

    qty = lots * lot_size
    actual_risk = lots * risk_per_lot

    daily_state["current_exposure"] += exposure

    return {
        "quantity":       qty,
        "lots":           lots,
        "risk_amount":    round(actual_risk, 2),
        "exposure_added": round(exposure, 2),
        "kelly_fraction": round(frac, 4),
        "premium_est":    round(premium, 2),
        "instrument":     instr,
    }


@app.post("/update_pnl")
def update_pnl(payload: TradeResult):
    _auto_reset()
    pnl = payload.pnl
    cap = daily_state["capital"]

    daily_state["total_trades"] += 1
    daily_state["daily_pnl"]    += pnl
    daily_state["daily_pnl_pct"]+= (pnl / cap) * 100 if cap > 0 else 0
    daily_state["current_exposure"] = max(0.0, daily_state["current_exposure"] - payload.exposure_released)
    daily_state["capital"] += pnl

    if daily_state["capital"] > daily_state["peak_capital"]:
        daily_state["peak_capital"] = daily_state["capital"]

    if pnl > 0:
        daily_state["winning_trades"]     += 1
        daily_state["consecutive_losses"] = 0
    else:
        daily_state["losing_trades"]      += 1
        daily_state["consecutive_losses"] += 1

    return {"status": "success", "daily_state": daily_state}


@app.get("/summary")
def get_summary():
    """Dashboard-friendly summary of the risk manager state."""
    _auto_reset()
    cap = daily_state["capital"]
    peak = daily_state["peak_capital"]
    total = daily_state["total_trades"]
    wins  = daily_state["winning_trades"]
    drawdown = ((peak - cap) / peak * 100) if peak > 0 else 0.0

    return {
        "status": "success",
        "data": {
            "capital":            round(cap, 2),
            "daily_pnl":          round(daily_state["daily_pnl"], 2),
            "daily_pnl_pct":      round(daily_state["daily_pnl_pct"], 3),
            "win_rate":           round((wins / total * 100) if total > 0 else 0, 2),
            "trades_today":       total,
            "trades_left":        daily_state["max_trades"] - total,
            "consecutive_losses": daily_state["consecutive_losses"],
            "exposure_pct":       round((daily_state["current_exposure"] / cap) * 100 if cap > 0 else 0, 2),
            "current_drawdown_pct": round(drawdown, 2),
        }
    }


@app.get("/status")
def get_status():
    _auto_reset()
    return {"status": "success", "daily_state": daily_state}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
