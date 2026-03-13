from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
import joblib
import os
import random

app = FastAPI(title="Prediction MCP", version="1.0.0")

class FeatureData(BaseModel):
    features: Dict[str, Any]

# Attempt to load LightGBM model
MODEL_PATH = "../models/lgbm_model.pkl"
model = None
if os.path.exists(MODEL_PATH):
    try:
        model = joblib.load(MODEL_PATH)
        print("Model loaded successfully")
    except Exception as e:
        print(f"Failed to load model: {e}")

@app.post("/predict")
def predict(payload: FeatureData):
    try:
        features = payload.features
        
        # If no real model, mock the prediction
        if model is None:
            # Random mock prediction for testing purposes
            prediction = random.choice([1, 0, -1])
            confidence = random.uniform(0.5, 0.95)
        else:
            # We assume a classification model where predict_proba is available
            import numpy as np
            import pandas as pd
            features_df = pd.DataFrame([features])
            
            # Predict probabilities
            probs = model.predict_proba(features_df)[0]
            # Assuming classes are [-1, 0, 1] mapped to indices [0, 1, 2]
            # Probabilities for: Bearish, Neutral, Bullish
            pred_class_idx = np.argmax(probs)
            confidence = probs[pred_class_idx]
            
            class_map = {0: -1, 1: 0, 2: 1}
            prediction = class_map[pred_class_idx]

        trend_map = {1: "bullish", 0: "neutral", -1: "bearish"}
        
        return {
            "status": "success",
            "prediction": prediction,
            "trend": trend_map.get(prediction, "neutral"),
            "confidence": round(confidence, 4)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
