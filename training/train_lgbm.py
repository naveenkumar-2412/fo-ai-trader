import lightgbm as lgb
import pandas as pd
import numpy as np
import joblib
import os
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import classification_report, accuracy_score

print("Starting LightGBM Training Process (v2)...")

# ─── 1. Generate Richer Simulated Historical Data ──────────────────────────
n_samples = 20000
np.random.seed(42)

# ── Simulated market state variables ──────────────────────────────────────
spot = 22000
spot_series = [spot]
for _ in range(n_samples - 1):
    spot_series.append(spot_series[-1] * (1 + np.random.normal(0, 0.001)))

spot_arr = np.array(spot_series)
close = spot_arr
high = close + np.random.uniform(5, 50, n_samples)
low = close - np.random.uniform(5, 50, n_samples)
volume = np.random.randint(5000, 100000, n_samples)

# ── Compute real indicator values from synthetic OHLCV ─────────────────────
import ta

df_raw = pd.DataFrame({"open": close * 0.999, "high": high, "low": low, "close": close, "volume": volume.astype(float)})

# Returns
df_raw['return_1'] = df_raw['close'].pct_change(1)
df_raw['return_3'] = df_raw['close'].pct_change(3)
df_raw['return_5'] = df_raw['close'].pct_change(5)
df_raw['body_size'] = abs(df_raw['close'] - df_raw['open']) / (df_raw['open'] + 1e-9) * 100
df_raw['hl_range'] = (df_raw['high'] - df_raw['low']) / (df_raw['low'] + 1e-9) * 100
df_raw['upper_wick'] = (df_raw['high'] - df_raw[['open', 'close']].max(axis=1)) / (df_raw['close'] + 1e-9) * 100
df_raw['lower_wick'] = (df_raw[['open', 'close']].min(axis=1) - df_raw['low']) / (df_raw['close'] + 1e-9) * 100

# Momentum
df_raw['rsi'] = ta.momentum.RSIIndicator(close=df_raw['close'], window=14).rsi()
df_raw['stoch_k'] = ta.momentum.StochasticOscillator(high=df_raw['high'], low=df_raw['low'], close=df_raw['close'], window=14).stoch()
df_raw['williams_r'] = ta.momentum.WilliamsRIndicator(high=df_raw['high'], low=df_raw['low'], close=df_raw['close'], lbp=14).williams_r()

# Trend
macd = ta.trend.MACD(close=df_raw['close'])
df_raw['macd'] = macd.macd()
df_raw['macd_diff'] = macd.macd_diff()
df_raw['ema9'] = ta.trend.EMAIndicator(close=df_raw['close'], window=9).ema_indicator()
df_raw['ema21'] = ta.trend.EMAIndicator(close=df_raw['close'], window=21).ema_indicator()
df_raw['ema_cross'] = (df_raw['ema9'] - df_raw['ema21']) / (df_raw['ema21'] + 1e-9) * 100
adx = ta.trend.ADXIndicator(high=df_raw['high'], low=df_raw['low'], close=df_raw['close'], window=14)
df_raw['adx'] = adx.adx()
df_raw['adx_pos'] = adx.adx_pos()
df_raw['adx_neg'] = adx.adx_neg()

# Volatility
df_raw['atr'] = ta.volatility.AverageTrueRange(high=df_raw['high'], low=df_raw['low'], close=df_raw['close'], window=14).average_true_range()
df_raw['atr_pct'] = df_raw['atr'] / (df_raw['close'] + 1e-9) * 100
bb = ta.volatility.BollingerBands(close=df_raw['close'], window=20, window_dev=2)
df_raw['bb_width'] = (bb.bollinger_hband() - bb.bollinger_lband()) / (bb.bollinger_mavg() + 1e-9) * 100
df_raw['bb_position'] = (df_raw['close'] - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband() + 1e-9)

# Volume
df_raw['obv'] = ta.volume.OnBalanceVolumeIndicator(close=df_raw['close'], volume=df_raw['volume']).on_balance_volume()
df_raw['obv_slope'] = df_raw['obv'].diff(3)
df_raw['volume_ratio'] = df_raw['volume'] / (df_raw['volume'].rolling(10).mean() + 1e-9)
df_raw['volume_spike'] = (df_raw['volume_ratio'] > 1.8).astype(int)

# VWAP
df_raw['typical_price'] = (df_raw['high'] + df_raw['low'] + df_raw['close']) / 3
df_raw['vwap'] = (df_raw['typical_price'] * df_raw['volume']).cumsum() / (df_raw['volume'].cumsum() + 1e-9)
df_raw['vwap_dist'] = (df_raw['close'] - df_raw['vwap']) / (df_raw['vwap'] + 1e-9) * 100
df_raw['price_above_vwap'] = (df_raw['close'] > df_raw['vwap']).astype(int)

# Regime
df_raw['rolling_std_10'] = df_raw['close'].pct_change().rolling(10).std() * 100
df_raw['high_volatility'] = (df_raw['rolling_std_10'] > df_raw['rolling_std_10'].rolling(30).mean()).astype(int)

# F&O proxies
df_raw['oi_change_pct'] = np.random.uniform(-15, 15, n_samples)
df_raw['pcr'] = np.random.uniform(0.5, 1.8, n_samples)

df_raw = df_raw.fillna(0)

# ── Feature columns matching the live feature engine ──────────────────────
FEATURE_COLS = [
    'return_1', 'return_3', 'return_5', 'body_size', 'hl_range', 'upper_wick', 'lower_wick',
    'rsi', 'stoch_k', 'williams_r',
    'macd', 'macd_diff', 'ema_cross', 'adx', 'adx_pos', 'adx_neg',
    'atr', 'atr_pct', 'bb_width', 'bb_position',
    'volume_spike', 'volume_ratio', 'obv_slope',
    'vwap_dist', 'price_above_vwap',
    'high_volatility',
    'oi_change_pct', 'pcr',
]

# ── Target: forward return over next 5 bars ────────────────────────────────
forward_return = df_raw['close'].pct_change(5).shift(-5)
# Bullish: >0.3%, Bearish: <-0.3%, Neutral: otherwise
conditions = [(forward_return > 0.003), (forward_return < -0.003)]
df_raw['target'] = np.select(conditions, [1, -1], default=0)

df_model = df_raw[FEATURE_COLS + ['target']].dropna()
X = df_model[FEATURE_COLS]
y = df_model['target']

# ── 2. Walk-Forward (Time-Series) Cross Validation ─────────────────────────
print(f"Dataset: {len(X)} samples, {len(FEATURE_COLS)} features")
print("Using TimeSeriesSplit (5 folds) for walk-forward validation...")

class_map = {-1: 0, 0: 1, 1: 2}
y_mapped = y.map(class_map)

tscv = TimeSeriesSplit(n_splits=5)
fold_accuracies = []
for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
    X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
    y_tr, y_val = y_mapped.iloc[train_idx], y_mapped.iloc[val_idx]

    fold_params = {
        'objective': 'multiclass',
        'num_class': 3,
        'metric': 'multi_error',
        'boosting_type': 'gbdt',
        'learning_rate': 0.05,
        'num_leaves': 63,
        'max_depth': 6,
        'feature_fraction': 0.7,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'min_child_samples': 30,
        'class_weight': 'balanced',
        'verbose': -1,
        'random_state': 42,
    }

    tr_data = lgb.Dataset(X_tr, label=y_tr)
    val_data = lgb.Dataset(X_val, label=y_val, reference=tr_data)

    fold_model = lgb.train(fold_params, tr_data, num_boost_round=300,
                           valid_sets=[val_data],
                           callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(period=-1)])

    preds = np.argmax(fold_model.predict(X_val), axis=1)
    acc = accuracy_score(y_val, preds)
    fold_accuracies.append(acc)
    print(f"  Fold {fold+1}: Accuracy = {acc:.4f}")

print(f"\nMean CV Accuracy: {np.mean(fold_accuracies):.4f} ± {np.std(fold_accuracies):.4f}")

# ── 3. Final Train on Full Dataset ─────────────────────────────────────────
print("\nTraining final model on all data...")
final_params = {
    'objective': 'multiclass',
    'num_class': 3,
    'metric': 'multi_error',
    'boosting_type': 'gbdt',
    'learning_rate': 0.03,
    'num_leaves': 63,
    'max_depth': 6,
    'feature_fraction': 0.7,
    'bagging_fraction': 0.8,
    'bagging_freq': 5,
    'min_child_samples': 30,
    'class_weight': 'balanced',
    'verbose': -1,
    'random_state': 42,
}

train_data = lgb.Dataset(X, label=y_mapped)
final_model = lgb.train(final_params, train_data, num_boost_round=400,
                        callbacks=[lgb.log_evaluation(period=50)])

# ── 4. Evaluation on held-out last 20% ─────────────────────────────────────
cutoff = int(len(X) * 0.8)
X_test, y_test_mapped = X.iloc[cutoff:], y_mapped.iloc[cutoff:]
y_pred = np.argmax(final_model.predict(X_test), axis=1)

print("\n--- Final Model Evaluation (Last 20% of data) ---")
print(f"Accuracy: {accuracy_score(y_test_mapped, y_pred):.4f}")
print(classification_report(y_test_mapped, y_pred, target_names=["Bearish", "Neutral", "Bullish"]))

# ── 5. Feature Importance ─────────────────────────────────────────────────
print("Top 10 Feature Importances:")
fi = pd.Series(final_model.feature_importance(importance_type='gain'), index=FEATURE_COLS).sort_values(ascending=False)
for feat, score in fi.head(10).items():
    print(f"  {feat:<25} {score:.0f}")

# ── 6. Save Model ─────────────────────────────────────────────────────────
os.makedirs("../models", exist_ok=True)
model_path = "../models/lgbm_model.pkl"

try:
    from sklearn.base import BaseEstimator, ClassifierMixin

    class LGBMWrapper(BaseEstimator, ClassifierMixin):
        def __init__(self, model, feature_cols):
            self.model = model
            self.feature_cols = feature_cols
            self.classes_ = np.array([-1, 0, 1])

        def predict_proba(self, X):
            return self.model.predict(X[self.feature_cols] if hasattr(X, 'columns') else X)

        def predict(self, X):
            probs = self.predict_proba(X)
            return self.classes_[np.argmax(probs, axis=1)]

    wrapped = LGBMWrapper(final_model, FEATURE_COLS)
    joblib.dump(wrapped, model_path)
    print(f"\nModel saved to {model_path}")
except Exception as e:
    print(f"Wrapper save failed: {e}. Saving native LightGBM model...")
    final_model.save_model("../models/lgbm_model.txt")
    print("Saved as ../models/lgbm_model.txt")
