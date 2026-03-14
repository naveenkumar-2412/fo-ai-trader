from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime
import json

app = FastAPI(title="Strategy MCP", version="2.1.0")

SIGNAL_LOG = "../signal_log.jsonl"


def log_signal(signal_type: str, action: str, reason: str, features: dict):
    try:
        entry = {
            "time":   datetime.now().isoformat(),
            "type":   signal_type,
            "action": action,
            "reason": reason,
            "key_features": {k: features.get(k) for k in ["adx", "rsi", "pcr", "atr_pct", "supertrend", "confidence"]},
        }
        with open(SIGNAL_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


class PredictionData(BaseModel):
    prediction: int
    trend: str
    confidence: float
    features: Dict[str, Any]


def _no_trade(reason: str, features: dict) -> dict:
    log_signal("NO_TRADE", "no_trade", reason, features)
    return {"status": "success", "signal": "no_trade", "reason": reason}


def get_vix_regime(atr_pct: float) -> str:
    if atr_pct > 1.2: return "high"
    if atr_pct < 0.5: return "low"
    return "normal"


def select_instrument(trend: str, vix_regime: str, adx: float, pcr: float) -> dict:
    """
    Instrument selection combining trend, VIX regime, PCR sentiment, and ADX.
    - PCR > 1.3 = put-heavy = bearish sentiment → confirm SELL_PUT on bullish (contrarian premium)
    - PCR < 0.8 = call-heavy = bullish sentiment → confirm SELL_CALL on bearish
    """
    if trend == "bullish":
        if vix_regime == "high":
            return {"side": "SELL", "instrument": "SELL_PUT",  "strike_offset": -1}
        if adx >= 25:
            action = "BUY_CALL" if pcr < 1.3 else "SELL_PUT"
            return {"side": "BUY" if action == "BUY_CALL" else "SELL", "instrument": action, "strike_offset": 0}
        return {"side": "SELL", "instrument": "SELL_PUT", "strike_offset": -1}

    elif trend == "bearish":
        if vix_regime == "high":
            return {"side": "SELL", "instrument": "SELL_CALL", "strike_offset": 1}
        if adx >= 25:
            action = "BUY_PUT" if pcr > 0.8 else "SELL_CALL"
            return {"side": "BUY" if action == "BUY_PUT" else "SELL", "instrument": action, "strike_offset": 0}
        return {"side": "SELL", "instrument": "SELL_CALL", "strike_offset": 1}

    else:  # neutral
        if vix_regime == "high":
            return {"side": "SELL", "instrument": "SELL_STRANGLE", "strike_offset": 2}
        if vix_regime == "normal" and adx < 20:
            # Range-bound + normal vol → Iron Condor (sell strangle + wider wings)
            return {"side": "SELL", "instrument": "IRON_CONDOR", "strike_offset": 3}
        return {"side": "SKIP", "instrument": "no_trade", "strike_offset": 0}


@app.post("/generate_signal")
def generate_signal(payload: PredictionData):
    try:
        features  = payload.features
        trend     = payload.trend
        conf      = payload.confidence
        adx       = float(features.get("adx", 0))
        atr_pct   = float(features.get("atr_pct", 0.7))
        pcr       = float(features.get("pcr", 1.0))
        bb_pos    = float(features.get("bb_position", 0.5))
        supertrend= float(features.get("supertrend", 0))
        obv_slope = float(features.get("obv_slope", 0))

        features["confidence"] = conf  # for logging

        # ── Rule 1: Confidence ────────────────────────────────────────────────
        if conf <= 0.65:
            return _no_trade("Low confidence (< 65%)", features)

        # ── Rule 2: ADX strength ──────────────────────────────────────────────
        if trend in ("bullish", "bearish") and adx < 20:
            return _no_trade(f"Weak trend (ADX={adx:.1f})", features)

        # ── Rule 3: Supertrend alignment ──────────────────────────────────────
        if trend == "bullish" and supertrend == -1:
            return _no_trade("Supertrend bearish vs bullish prediction", features)
        if trend == "bearish" and supertrend == 1:
            return _no_trade("Supertrend bullish vs bearish prediction", features)

        # ── Rule 4: OBV confirmation ──────────────────────────────────────────
        if trend == "bullish" and obv_slope < 0:
            return _no_trade("OBV declining, no volume support for bull", features)
        if trend == "bearish" and obv_slope > 0:
            return _no_trade("OBV rising, no volume support for bear", features)

        # ── Rule 5: VIX / extreme volatility ─────────────────────────────────
        vix_regime = get_vix_regime(atr_pct)
        if atr_pct > 2.0:
            return _no_trade(f"Extreme volatility (ATR%={atr_pct:.2f})", features)

        # ── Rule 6: BB extreme ────────────────────────────────────────────────
        if trend == "bullish" and bb_pos > 0.95:
            return _no_trade("Price at BB upper extreme; reversal risk", features)
        if trend == "bearish" and bb_pos < 0.05:
            return _no_trade("Price at BB lower extreme; bounce risk", features)

        # ── Rule 7: PCR extreme filter ────────────────────────────────────────
        # Don't buy calls when everyone is already bullish (PCR < 0.7 = crowded)
        if trend == "bullish" and pcr < 0.7:
            return _no_trade("PCR too low (crowded bullish); prefer selling", features)
        # Don't buy puts when everyone is already bearish
        if trend == "bearish" and pcr > 1.6:
            return _no_trade("PCR too high (crowded bearish); prefer selling", features)

        # ── Rule 8: Trading hours ─────────────────────────────────────────────
        now = datetime.now().time()
        t = lambda h, m: datetime.strptime(f"{h}:{m}", "%H:%M").time()
        if not ((t(9,20) <= now <= t(11,30)) or (t(14,0) <= now <= t(15,15))):
            return _no_trade("Outside trading hours (9:20-11:30, 14:00-15:15)", features)

        # ── Instrument selection ──────────────────────────────────────────────
        sel = select_instrument(trend, vix_regime, adx, pcr)
        if sel["side"] == "SKIP":
            return _no_trade("Neutral + low vol: no opportunity", features)

        action = sel["instrument"]
        side   = sel["side"]

        # ── SL / Target ───────────────────────────────────────────────────────
        if side == "BUY":
            sl_pct     = 0.25 if vix_regime == "high" else 0.30
            target_pct = 0.70 if conf > 0.80 else 0.50
            time_mins  = 20
            trail_activate_pct = 0.30
        elif "CONDOR" in action:
            sl_pct, target_pct, time_mins = 0.35, 0.50, 90
            trail_activate_pct = 0.25
        elif "STRANGLE" in action:
            sl_pct, target_pct, time_mins = 0.40, 0.55, 60
            trail_activate_pct = 0.30
        else:  # SELL single leg
            sl_pct, target_pct, time_mins = 0.45, 0.70, 45
            trail_activate_pct = 0.35

        # High confidence → extend target up to 20%
        if conf >= 0.85:
            target_pct = min(target_pct * 1.20, 0.90)

        signal = {
            "action":             action,
            "side":               side,
            "strike_offset":      sel["strike_offset"],
            "sl_pct":             round(sl_pct, 3),
            "target_pct":         round(target_pct, 3),
            "time_exit_mins":     time_mins,
            "trail_activate_pct": round(trail_activate_pct, 3),
            "vix_regime":         vix_regime,
            "adx":                round(adx, 1),
            "pcr":                round(pcr, 3),
            "atr_pct":            round(atr_pct, 3),
            "confidence":         round(conf, 4),
        }

        log_signal("TRADE", action, "All filters passed", features)
        return {"status": "success", "signal": signal}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
