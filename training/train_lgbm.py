"""
Incremental model retraining script.
Usage:
  python training/train_lgbm.py            # trains on synthetic data only
  python training/train_lgbm.py --retrain  # appends real trade log data and retrains
"""
import argparse
import os
import shutil
import json
import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from lightgbm import LGBMClassifier

parser = argparse.ArgumentParser()
parser.add_argument("--retrain", action="store_true", help="Append real trade log to synthetic data")
parser.add_argument("--capital", type=float, default=500_000)
args = parser.parse_args()

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

MODEL_DIR  = os.path.join(os.path.dirname(__file__), "..", "models")
MODEL_FILE = os.path.join(MODEL_DIR, "lgbm_model.pkl")
os.makedirs(MODEL_DIR, exist_ok=True)

TRADE_LOG = os.path.join(os.path.dirname(__file__), "..", "trade_log.jsonl")


# ─── Synthetic data generator ─────────────────────────────────────────────────
def generate_synthetic_data(n=5000, seed=42):
    np.random.seed(seed)
    regime = np.random.choice(["trend_up", "trend_down", "range", "volatile"], n,
                               p=[0.25, 0.25, 0.35, 0.15])
    rows = []
    for r in regime:
        rsi       = np.random.normal(60 if r=="trend_up" else (40 if r=="trend_down" else 50), 12)
        adx       = np.random.normal(30 if r in ("trend_up","trend_down") else 15, 8)
        macd_diff = np.random.normal(0.5 if r=="trend_up" else (-0.5 if r=="trend_down" else 0), 0.3)
        supertrend= 1 if r=="trend_up" else (-1 if r=="trend_down" else np.random.choice([-1,1]))
        atr_pct   = np.random.normal(1.5 if r=="volatile" else 0.7, 0.25)

        label = 2 if (r=="trend_up"  and rsi>55 and macd_diff>0) else \
                0 if (r=="trend_down" and rsi<45 and macd_diff<0) else 1

        rows.append({
            "return_1": np.random.normal(0.002 if r=="trend_up" else -0.002, 0.005),
            "return_3": np.random.normal(0.006 if r=="trend_up" else -0.006, 0.01),
            "return_5": np.random.normal(0.010 if r=="trend_up" else -0.010, 0.015),
            "body_size": np.random.uniform(0.1, 1.5), "hl_range": np.random.uniform(0.5, 2.0),
            "upper_wick": np.random.uniform(0,1), "lower_wick": np.random.uniform(0,1),
            "rsi": np.clip(rsi,10,90), "stoch_k": np.clip(np.random.normal(50,20),0,100),
            "williams_r": np.random.uniform(-100,0),
            "macd": np.random.normal(0,1), "macd_diff": macd_diff,
            "ema_cross": np.random.normal(0,0.05), "adx": np.clip(adx,5,60),
            "adx_pos": np.random.uniform(10,40), "adx_neg": np.random.uniform(10,40),
            "supertrend": float(supertrend), "atr": np.random.uniform(50,200),
            "atr_pct": np.clip(atr_pct,0.1,3.0),
            "bb_width": np.random.uniform(2,10), "bb_position": np.random.uniform(0,1),
            "volume_spike": int(np.random.rand()>0.85), "volume_ratio": np.random.uniform(0.5,3),
            "obv_slope": np.random.normal(0,1000), "vwap_dist": np.random.normal(0,0.5),
            "price_above_vwap": int(r=="trend_up" or np.random.rand()>0.5),
            "high_volatility": int(r=="volatile"),
            "oi_change_pct": np.random.uniform(-10,10), "pcr": np.random.uniform(0.5,1.8),
            "__label__": label,
        })
    return pd.DataFrame(rows)


# ─── Load real trade data ─────────────────────────────────────────────────────
def load_real_trade_data():
    if not os.path.exists(TRADE_LOG):
        return None
    rows = []
    with open(TRADE_LOG) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                order = entry.get("order", {})
                signal = entry.get("signal", {})
                features = signal.get("features", {}) if signal else {}
                if not features or "pnl" not in order:
                    continue
                pnl = order["pnl"]
                label = 2 if pnl > 500 else (0 if pnl < -300 else 1)
                row = {col: float(features.get(col, 0)) for col in FEATURE_COLS}
                row["__label__"] = label
                rows.append(row)
            except Exception:
                pass
    if not rows:
        return None
    df = pd.DataFrame(rows)
    print(f"Loaded {len(df)} real trade samples from trade_log.jsonl")
    return df


# ─── Training ────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  LightGBM Training  |  {'RETRAIN' if args.retrain else 'SYNTHETIC'}")
print(f"{'='*50}")

df_synth = generate_synthetic_data(n=5000)
print(f"Synthetic data: {len(df_synth)} rows")

if args.retrain:
    df_real = load_real_trade_data()
    if df_real is not None:
        df = pd.concat([df_synth, df_real], ignore_index=True)
        df = df.sample(frac=1, random_state=42).reset_index(drop=True)
        print(f"Combined dataset: {len(df)} rows")
    else:
        print("No real trade data found — using synthetic only")
        df = df_synth
else:
    df = df_synth

X = df[FEATURE_COLS].fillna(0)
y = df["__label__"]

model = LGBMClassifier(
    n_estimators=400, learning_rate=0.04, max_depth=6,
    num_leaves=31, subsample=0.8, colsample_bytree=0.8,
    class_weight="balanced", random_state=42,
    n_jobs=-1, verbosity=-1,
)

tscv = TimeSeriesSplit(n_splits=5)
fold_scores = []
for fold, (train_idx, val_idx) in enumerate(tscv.split(X), 1):
    Xtr, Xvl = X.iloc[train_idx], X.iloc[val_idx]
    ytr, yvl = y.iloc[train_idx], y.iloc[val_idx]
    model.fit(Xtr, ytr,
              eval_set=[(Xvl, yvl)],
              callbacks=[])
    score = model.score(Xvl, yvl)
    fold_scores.append(score)
    print(f"  Fold {fold}: Accuracy = {score:.4f}")

print(f"\n  Mean Accuracy: {sum(fold_scores)/len(fold_scores):.4f}")

# Final fit on full data
model.fit(X, y)

# Feature importance
imp_vals  = model.feature_importances_
imp_ranked = sorted(zip(FEATURE_COLS, imp_vals), key=lambda x: x[1], reverse=True)[:10]
print("\n  Top 10 Features:")
for name, imp in imp_ranked:
    print(f"    {name:<25} {imp:.0f}")

# Save
stamp = datetime.now().strftime("%Y%m%d_%H%M")
ts_path = os.path.join(MODEL_DIR, f"lgbm_model_{stamp}.pkl")
joblib.dump(model, MODEL_FILE)
shutil.copy(MODEL_FILE, ts_path)
print(f"\n  Saved: {MODEL_FILE}")
print(f"  Backup: {ts_path}")
