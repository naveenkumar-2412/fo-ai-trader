from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime

app = FastAPI(title="Strategy MCP", version="2.0.0")


class PredictionData(BaseModel):
    prediction: int
    trend: str
    confidence: float
    features: Dict[str, Any]


def get_vix_regime(atr_pct: float) -> str:
    """
    Proxy VIX regime from normalized ATR.
    In real usage, plug in India VIX from NSE API.
    """
    if atr_pct > 1.2:
        return "high"
    elif atr_pct < 0.5:
        return "low"
    return "normal"


def select_strike_type(trend: str, vix_regime: str, adx: float) -> dict:
    """
    Decide option instrument type and select near/far strikes based on
    market conditions.
    - Buying: use near-the-money options when trend is strong + high ADX
    - Selling: use slightly OTM options to collect more premium
    - Strangle: use wider strikes when VIX is high / flat market
    """
    if trend == "bullish":
        if vix_regime == "high" or adx < 20:
            # Avoid buying in high VIX; prefer selling put
            return {"side": "SELL", "instrument": "SELL_PUT", "strike_offset": -1}
        else:
            return {"side": "BUY", "instrument": "BUY_CALL", "strike_offset": 0}

    elif trend == "bearish":
        if vix_regime == "high" or adx < 20:
            return {"side": "SELL", "instrument": "SELL_CALL", "strike_offset": 1}
        else:
            return {"side": "BUY", "instrument": "BUY_PUT", "strike_offset": 0}
    else:
        # Neutral: sell a strangle only if volatility is elevated (premium collection)
        if vix_regime in ("normal", "high"):
            return {"side": "SELL", "instrument": "SELL_STRANGLE", "strike_offset": 2}
        return {"side": "SKIP", "instrument": "no_trade", "strike_offset": 0}


@app.post("/generate_signal")
def generate_signal(payload: PredictionData):
    """
    Converts prediction into a trade signal with advanced regime filtering,
    trend strength confirmation, and strike selection.
    """
    try:
        features = payload.features

        # ── Rule 1: Minimum Confidence Threshold ───────────────────────────────
        if payload.confidence <= 0.65:
            return {"status": "success", "signal": "no_trade", "reason": "Low model confidence (< 65%)"}

        # ── Rule 2: Minimum Trend Strength (ADX ≥ 20 for directional trades) ──
        adx = features.get("adx", 0)
        trend = payload.trend

        if trend in ("bullish", "bearish") and adx < 20:
            return {"status": "success", "signal": "no_trade", "reason": f"Trend too weak (ADX={adx:.1f} < 20)"}

        # ── Rule 3: Supertrend Alignment Check ─────────────────────────────────
        supertrend = features.get("supertrend", 0)
        if trend == "bullish" and supertrend == -1:
            return {"status": "success", "signal": "no_trade", "reason": "Supertrend is bearish; conflicts with bullish prediction"}
        if trend == "bearish" and supertrend == 1:
            return {"status": "success", "signal": "no_trade", "reason": "Supertrend is bullish; conflicts with bearish prediction"}

        # ── Rule 4: OBV Slope Confirmation ─────────────────────────────────────
        obv_slope = features.get("obv_slope", 0)
        if trend == "bullish" and obv_slope < 0:
            return {"status": "success", "signal": "no_trade", "reason": "OBV declining, no volume confirmation for bull trend"}
        if trend == "bearish" and obv_slope > 0:
            return {"status": "success", "signal": "no_trade", "reason": "OBV rising, no volume confirmation for bear trend"}

        # ── Rule 5: VIX Regime Filter ───────────────────────────────────────────
        atr_pct = features.get("atr_pct", 0.7)
        vix_regime = get_vix_regime(atr_pct)

        # If volatility is extremely high (>2.0%), skip all trades; risk of whipsaws
        if atr_pct > 2.0:
            return {"status": "success", "signal": "no_trade", "reason": "Extreme volatility detected (ATR% > 2.0); skipping trade"}

        # ── Rule 6: BB Position Extremes for Reversal / Breakout ───────────────
        bb_position = features.get("bb_position", 0.5)
        if trend == "bullish" and bb_position > 0.95:
            return {"status": "success", "signal": "no_trade", "reason": "Price at upper BB extreme; risk of rejection"}
        if trend == "bearish" and bb_position < 0.05:
            return {"status": "success", "signal": "no_trade", "reason": "Price at lower BB extreme; risk of bounce"}

        # ── Rule 7: Market Hours Filter ────────────────────────────────────────
        now = datetime.now().time()
        open_time = datetime.strptime("09:20", "%H:%M").time()
        first_end = datetime.strptime("11:30", "%H:%M").time()
        second_start = datetime.strptime("14:00", "%H:%M").time()
        close_time = datetime.strptime("15:15", "%H:%M").time()
        time_valid = (open_time <= now <= first_end) or (second_start <= now <= close_time)

        if not time_valid:
            return {"status": "success", "signal": "no_trade", "reason": "Outside valid trading hours (9:20-11:30, 14:00-15:15)"}

        # ── Strike & Instrument Selection ──────────────────────────────────────
        selection = select_strike_type(trend, vix_regime, adx)
        if selection["side"] == "SKIP":
            return {"status": "success", "signal": "no_trade", "reason": "Neutral trend with low volatility; no premium opportunity"}

        action = selection["instrument"]
        side = selection["side"]

        # ── SL/Target Parameters based on VIX regime and side ─────────────────
        if side == "BUY":
            # Buyers: tighter SL to protect premium cost, wider target
            sl_pct = 0.25 if vix_regime == "high" else 0.30
            target_pct = 0.70 if payload.confidence > 0.80 else 0.50
            time_exit_mins = 20
        elif side == "SELL":
            if "STRANGLE" in action:
                sl_pct = 0.40   # Strangle: wider SL for the combined position
                target_pct = 0.50
                time_exit_mins = 60
            else:
                sl_pct = 0.45   # Single leg sell: 45% SL on premium received
                target_pct = 0.70
                time_exit_mins = 45
        else:
            sl_pct, target_pct, time_exit_mins = 0.30, 0.60, 25

        # ── Confidence Multiplier on Target ───────────────────────────────────
        # Higher AI confidence → try to capture more profit
        if payload.confidence >= 0.85:
            target_pct = min(target_pct * 1.2, 0.90)

        signal = {
            "action": action,
            "side": side,
            "strike_offset": selection["strike_offset"],
            "sl_pct": round(sl_pct, 3),
            "target_pct": round(target_pct, 3),
            "time_exit_mins": time_exit_mins,
            "vix_regime": vix_regime,
            "adx": round(adx, 1),
            "confidence": round(payload.confidence, 4),
        }

        return {"status": "success", "signal": signal}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
