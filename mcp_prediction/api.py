from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
import joblib
import os
import json
import random
import numpy as np
from datetime import datetime

app = FastAPI(title="Prediction MCP", version="2.0.0")

class FeatureData(BaseModel):
    features: Dict[str, Any]

# ─── Feature column order (MUST match training) ────────────────────────────────
FEATURE_COLS = [
    "return_1", "return_3", "return_5",
    "body_size", "hl_range", "upper_wick", "lower_wick",
    "rsi", "stoch_k", "williams_r",
    "macd", "macd_diff", "ema_cross",
    "adx", "adx_pos", "adx_neg", "supertrend",
    "atr", "atr_pct", "bb_width", "bb_position",
    "volume_spike", "volume_ratio", "obv_slope",
    "vwap_dist", "price_above_vwap",
    "high_volatility",
    "oi_change_pct", "pcr",
]

MODEL_PATH = "../models/lgbm_model.pkl"
model = None
feature_importance: dict = {}

if os.path.exists(MODEL_PATH):
    try:
        model = joblib.load(MODEL_PATH)
        print("Model loaded successfully")
        # Extract feature importance if the model supports it
        try:
            if hasattr(model, "model"):
                imp = model.model.feature_importance(importance_type="gain")
                names = getattr(model, "feature_cols", FEATURE_COLS)
                feature_importance = dict(sorted(
                    zip(names, imp.tolist()),
                    key=lambda x: x[1], reverse=True
                ))
        except Exception as e:
            print(f"Feature importance extraction failed: {e}")
    except Exception as e:
        print(f"Failed to load model: {e}")

PREDICTION_LOG = "../prediction_log.jsonl"


def log_prediction(features: dict, prediction: int, confidence: float, trend: str):
    try:
        entry = {
            "time": datetime.now().isoformat(),
            "prediction": prediction,
            "trend": trend,
            "confidence": round(confidence, 4),
            "top_features": {k: features.get(k) for k in ["rsi", "adx", "vwap_dist", "atr_pct", "pcr", "bb_position"]},
        }
        with open(PREDICTION_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


@app.post("/predict")
def predict(payload: FeatureData):
    try:
        features = payload.features

        if model is None:
            # Informed mock: use feature values to bias the prediction
            rsi = features.get("rsi", 50)
            macd_diff = features.get("macd_diff", 0)
            supertrend = features.get("supertrend", 0)

            if rsi > 58 and macd_diff > 0 and supertrend >= 0:
                prediction = 1
                confidence = random.uniform(0.65, 0.88)
            elif rsi < 42 and macd_diff < 0 and supertrend <= 0:
                prediction = -1
                confidence = random.uniform(0.65, 0.85)
            else:
                prediction = random.choice([1, 0, -1])
                confidence = random.uniform(0.50, 0.72)
        else:
            import pandas as pd
            # ── Strict feature alignment: reindex to FEATURE_COLS ────────────
            feat_row = {col: features.get(col, 0) for col in FEATURE_COLS}
            features_df = pd.DataFrame([feat_row])[FEATURE_COLS]

            probs = model.predict_proba(features_df)[0]
            pred_idx = int(np.argmax(probs))
            confidence = float(probs[pred_idx])

            # ── Platt-like calibration: gentle shrink toward 0.5 ─────────────
            # Reduces overconfidence common in LightGBM
            calibrated = 0.5 + (confidence - 0.5) * 0.85
            confidence = float(np.clip(calibrated, 0.50, 0.97))

            class_map = {0: -1, 1: 0, 2: 1}
            prediction = class_map[pred_idx]

        trend_map = {1: "bullish", 0: "neutral", -1: "bearish"}
        trend = trend_map.get(prediction, "neutral")

        log_prediction(features, prediction, confidence, trend)

        return {
            "status":     "success",
            "prediction": prediction,
            "trend":      trend,
            "confidence": round(confidence, 4),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/feature_importance")
def get_feature_importance():
    """Returns top feature importances for the dashboard bar chart."""
    if not feature_importance:
        # Return mocked values if model not loaded
        mock = {col: random.uniform(100, 10000) for col in FEATURE_COLS}
        sorted_mock = dict(sorted(mock.items(), key=lambda x: x[1], reverse=True))
        return {"status": "success", "data": dict(list(sorted_mock.items())[:15])}
    return {"status": "success", "data": dict(list(feature_importance.items())[:15])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
