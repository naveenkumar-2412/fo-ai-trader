from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import pandas as pd
import numpy as np
import ta

app = FastAPI(title="Feature Engine MCP", version="2.0.0")

class CandleData(BaseModel):
    data: List[Dict[str, Any]]

def compute_supertrend(df, period=10, multiplier=3.0):
    """Compute Supertrend indicator. Returns +1 (uptrend) or -1 (downtrend)."""
    atr = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=period
    ).average_true_range()

    hl2 = (df['high'] + df['low']) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = pd.Series(0, index=df.index)
    direction = pd.Series(1, index=df.index)  # 1=uptrend, -1=downtrend

    for i in range(1, len(df)):
        # Upper band
        if upper_band.iloc[i] < upper_band.iloc[i - 1] or df['close'].iloc[i - 1] > upper_band.iloc[i - 1]:
            upper_band.iloc[i] = upper_band.iloc[i]
        else:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        # Lower band
        if lower_band.iloc[i] > lower_band.iloc[i - 1] or df['close'].iloc[i - 1] < lower_band.iloc[i - 1]:
            lower_band.iloc[i] = lower_band.iloc[i]
        else:
            lower_band.iloc[i] = lower_band.iloc[i - 1]

        if supertrend.iloc[i - 1] == upper_band.iloc[i - 1]:
            direction.iloc[i] = -1 if df['close'].iloc[i] <= upper_band.iloc[i] else 1
        else:
            direction.iloc[i] = 1 if df['close'].iloc[i] >= lower_band.iloc[i] else -1

    return direction

@app.post("/generate_features")
def generate_features(payload: CandleData):
    try:
        df = pd.DataFrame(payload.data)
        if df.empty or len(df) < 15:
            raise ValueError("Need at least 15 candles for feature engineering")

        df = df.sort_values("timestamp").reset_index(drop=True)
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
        df = df.fillna(method='ffill').fillna(0)

        # 1. Price Return Features
        df['return_1'] = df['close'].pct_change(1)
        df['return_3'] = df['close'].pct_change(3)
        df['return_5'] = df['close'].pct_change(5)
        df['body_size'] = abs(df['close'] - df['open']) / (df['open'] + 1e-9) * 100
        df['hl_range'] = (df['high'] - df['low']) / (df['low'] + 1e-9) * 100
        df['upper_wick'] = (df['high'] - df[['open', 'close']].max(axis=1)) / (df['close'] + 1e-9) * 100
        df['lower_wick'] = (df[['open', 'close']].min(axis=1) - df['low']) / (df['close'] + 1e-9) * 100

        # 2. Momentum Indicators
        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        df['stoch_k'] = ta.momentum.StochasticOscillator(
            high=df['high'], low=df['low'], close=df['close'], window=14
        ).stoch()
        df['williams_r'] = ta.momentum.WilliamsRIndicator(
            high=df['high'], low=df['low'], close=df['close'], lbp=14
        ).williams_r()

        # 3. Trend Indicators
        macd = ta.trend.MACD(close=df['close'])
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        df['macd_diff'] = macd.macd_diff()

        df['ema9'] = ta.trend.EMAIndicator(close=df['close'], window=9).ema_indicator()
        df['ema21'] = ta.trend.EMAIndicator(close=df['close'], window=21).ema_indicator()
        df['ema_cross'] = (df['ema9'] - df['ema21']) / (df['ema21'] + 1e-9) * 100  # percentage gap

        # ADX - trend strength
        adx_indicator = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['adx'] = adx_indicator.adx()
        df['adx_pos'] = adx_indicator.adx_pos()
        df['adx_neg'] = adx_indicator.adx_neg()

        # Supertrend direction
        df['supertrend'] = compute_supertrend(df, period=10, multiplier=3.0)

        # 4. Volatility Indicators
        df['atr'] = ta.volatility.AverageTrueRange(
            high=df['high'], low=df['low'], close=df['close'], window=14
        ).average_true_range()
        df['atr_pct'] = df['atr'] / (df['close'] + 1e-9) * 100  # Normalized ATR

        bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
        df['bb_mid'] = bb.bollinger_mavg()
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / (df['bb_mid'] + 1e-9) * 100
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-9)

        # 5. Volume Indicators
        df['obv'] = ta.volume.OnBalanceVolumeIndicator(close=df['close'], volume=df['volume']).on_balance_volume()
        df['obv_slope'] = df['obv'].diff(3)
        df['volume_sma'] = df['volume'].rolling(10).mean()
        df['volume_spike'] = (df['volume'] > df['volume_sma'] * 1.8).astype(int)
        df['volume_ratio'] = df['volume'] / (df['volume_sma'] + 1e-9)

        # 6. VWAP & Distance
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (df['typical_price'] * df['volume']).cumsum() / (df['volume'].cumsum() + 1e-9)
        df['vwap_dist'] = (df['close'] - df['vwap']) / (df['vwap'] + 1e-9) * 100
        df['price_above_vwap'] = (df['close'] > df['vwap']).astype(int)

        # 7. Regime / Volatility State
        df['rolling_std_10'] = df['close'].pct_change().rolling(10).std() * 100
        df['high_volatility'] = (df['rolling_std_10'] > df['rolling_std_10'].rolling(30).mean()).astype(int)

        df = df.fillna(0)
        latest = df.iloc[-1]

        feature_vector = {
            # Price features
            "return_1": float(latest.get("return_1", 0)),
            "return_3": float(latest.get("return_3", 0)),
            "return_5": float(latest.get("return_5", 0)),
            "body_size": float(latest.get("body_size", 0)),
            "hl_range": float(latest.get("hl_range", 0)),
            "upper_wick": float(latest.get("upper_wick", 0)),
            "lower_wick": float(latest.get("lower_wick", 0)),
            # Momentum
            "rsi": float(latest.get("rsi", 50)),
            "stoch_k": float(latest.get("stoch_k", 50)),
            "williams_r": float(latest.get("williams_r", -50)),
            # Trend
            "macd": float(latest.get("macd", 0)),
            "macd_diff": float(latest.get("macd_diff", 0)),
            "ema_cross": float(latest.get("ema_cross", 0)),
            "adx": float(latest.get("adx", 20)),
            "adx_pos": float(latest.get("adx_pos", 0)),
            "adx_neg": float(latest.get("adx_neg", 0)),
            "supertrend": float(latest.get("supertrend", 0)),
            # Volatility
            "atr": float(latest.get("atr", 0)),
            "atr_pct": float(latest.get("atr_pct", 0)),
            "bb_width": float(latest.get("bb_width", 5)),
            "bb_position": float(latest.get("bb_position", 0.5)),
            # Volume
            "volume_spike": int(latest.get("volume_spike", 0)),
            "volume_ratio": float(latest.get("volume_ratio", 1)),
            "obv_slope": float(latest.get("obv_slope", 0)),
            # VWAP
            "vwap_dist": float(latest.get("vwap_dist", 0)),
            "price_above_vwap": int(latest.get("price_above_vwap", 0)),
            # Regime
            "high_volatility": int(latest.get("high_volatility", 0)),
            # F&O proxies (simulated until broker API)
            "oi_change_pct": float(np.random.uniform(-10, 10)),
            "pcr": float(np.random.uniform(0.7, 1.3)),
        }

        return {"status": "success", "features": feature_vector}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
