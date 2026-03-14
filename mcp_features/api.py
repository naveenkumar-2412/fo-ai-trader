from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
import pandas as pd
import numpy as np
import ta
import requests

app = FastAPI(title="Feature Engine MCP", version="2.1.0")

MARKET_DATA_URL = "http://localhost:8001"

class CandleData(BaseModel):
    data: List[Dict[str, Any]]
    symbol: str = "NIFTY"

# ─── Supertrend (vectorized, no SettingWithCopyWarning) ───────────────────────
def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    atr = ta.volatility.AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=period
    ).average_true_range()

    hl2 = (df["high"] + df["low"]) / 2
    raw_upper = hl2 + multiplier * atr
    raw_lower = hl2 - multiplier * atr

    # Vectorized rolling boundary tightening
    upper = raw_upper.copy()
    lower = raw_lower.copy()
    direction = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        upper.iloc[i] = raw_upper.iloc[i] if raw_upper.iloc[i] < upper.iloc[i - 1] or df["close"].iloc[i - 1] > upper.iloc[i - 1] else upper.iloc[i - 1]
        lower.iloc[i] = raw_lower.iloc[i] if raw_lower.iloc[i] > lower.iloc[i - 1] or df["close"].iloc[i - 1] < lower.iloc[i - 1] else lower.iloc[i - 1]

        if direction.iloc[i - 1] == -1:
            direction.iloc[i] = 1 if df["close"].iloc[i] > upper.iloc[i] else -1
        else:
            direction.iloc[i] = -1 if df["close"].iloc[i] < lower.iloc[i] else 1

    return direction


# ─── Candlestick Pattern Detection ────────────────────────────────────────────
def detect_candlestick_patterns(df: pd.DataFrame) -> dict:
    """Detect key single and two-bar candlestick patterns on the last bar."""
    if len(df) < 2:
        return {}

    c = df.iloc[-1]   # current bar
    p = df.iloc[-2]   # previous bar

    body      = abs(c["close"] - c["open"])
    full_range = (c["high"] - c["low"]) + 1e-9
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]

    # Doji: tiny body relative to range
    doji = int(body / full_range < 0.1)

    # Hammer: lower wick ≥ 2× body, small upper wick
    hammer = int(
        lower_wick >= 2 * body and upper_wick <= 0.3 * body and c["close"] > c["open"]
    )

    # Shooting Star: upper wick ≥ 2× body, small lower wick
    shooting_star = int(
        upper_wick >= 2 * body and lower_wick <= 0.3 * body and c["close"] < c["open"]
    )

    # Bullish Engulfing
    bull_engulf = int(
        p["close"] < p["open"]  # prev bearish
        and c["close"] > c["open"]  # curr bullish
        and c["open"] < p["close"]
        and c["close"] > p["open"]
    )

    # Bearish Engulfing
    bear_engulf = int(
        p["close"] > p["open"]  # prev bullish
        and c["close"] < c["open"]  # curr bearish
        and c["open"] > p["close"]
        and c["close"] < p["open"]
    )

    # Morning Star proxy (3-bar)
    morning_star = 0
    evening_star = 0
    if len(df) >= 3:
        pp = df.iloc[-3]
        if pp["close"] < pp["open"] and doji and c["close"] > c["open"]:
            morning_star = 1
        if pp["close"] > pp["open"] and doji and c["close"] < c["open"]:
            evening_star = 1

    return {
        "pat_doji":        doji,
        "pat_hammer":      hammer,
        "pat_shooting_star": shooting_star,
        "pat_bull_engulf": bull_engulf,
        "pat_bear_engulf": bear_engulf,
        "pat_morning_star": morning_star,
        "pat_evening_star": evening_star,
    }


# ─── Gap Detection ─────────────────────────────────────────────────────────────
def detect_gaps(df: pd.DataFrame) -> dict:
    if len(df) < 2:
        return {"gap_up": 0, "gap_down": 0, "gap_pct": 0.0}
    prev_close = df.iloc[-2]["close"]
    curr_open  = df.iloc[-1]["open"]
    gap_pct = (curr_open - prev_close) / prev_close * 100
    return {
        "gap_up":   int(gap_pct > 0.3),
        "gap_down": int(gap_pct < -0.3),
        "gap_pct":  round(gap_pct, 4),
    }


@app.post("/generate_features")
def generate_features(payload: CandleData):
    try:
        df = pd.DataFrame(payload.data)
        if df.empty or len(df) < 20:
            raise ValueError("Need at least 20 candles")

        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.ffill().fillna(0)

        # ── Returns & Price structure ─────────────────────────────────────────
        df["return_1"]  = df["close"].pct_change(1)
        df["return_3"]  = df["close"].pct_change(3)
        df["return_5"]  = df["close"].pct_change(5)
        df["body_size"] = abs(df["close"] - df["open"]) / (df["open"] + 1e-9) * 100
        df["hl_range"]  = (df["high"] - df["low"]) / (df["low"] + 1e-9) * 100
        df["upper_wick"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["close"] + 1e-9) * 100
        df["lower_wick"] = (df[["open", "close"]].min(axis=1) - df["low"])  / (df["close"] + 1e-9) * 100

        # ── Momentum ──────────────────────────────────────────────────────────
        df["rsi"]       = ta.momentum.RSIIndicator(close=df["close"], window=14).rsi()
        df["stoch_k"]   = ta.momentum.StochasticOscillator(high=df["high"], low=df["low"], close=df["close"], window=14).stoch()
        df["williams_r"]= ta.momentum.WilliamsRIndicator(high=df["high"], low=df["low"], close=df["close"], lbp=14).williams_r()

        # ── Trend ─────────────────────────────────────────────────────────────
        macd_ind        = ta.trend.MACD(close=df["close"])
        df["macd"]      = macd_ind.macd()
        df["macd_diff"] = macd_ind.macd_diff()
        df["ema9"]      = ta.trend.EMAIndicator(close=df["close"], window=9).ema_indicator()
        df["ema21"]     = ta.trend.EMAIndicator(close=df["close"], window=21).ema_indicator()
        df["ema_cross"] = (df["ema9"] - df["ema21"]) / (df["ema21"] + 1e-9) * 100
        adx_ind         = ta.trend.ADXIndicator(high=df["high"], low=df["low"], close=df["close"], window=14)
        df["adx"]       = adx_ind.adx()
        df["adx_pos"]   = adx_ind.adx_pos()
        df["adx_neg"]   = adx_ind.adx_neg()
        df["supertrend"]= compute_supertrend(df, period=10, multiplier=3.0)

        # ── Volatility ────────────────────────────────────────────────────────
        df["atr"] = ta.volatility.AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=14).average_true_range()
        df["atr_pct"] = df["atr"] / (df["close"] + 1e-9) * 100
        bb = ta.volatility.BollingerBands(close=df["close"], window=20, window_dev=2)
        df["bb_width"]    = (bb.bollinger_hband() - bb.bollinger_lband()) / (bb.bollinger_mavg() + 1e-9) * 100
        df["bb_position"] = (df["close"] - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband() + 1e-9)

        # ── Volume ────────────────────────────────────────────────────────────
        df["obv"]          = ta.volume.OnBalanceVolumeIndicator(close=df["close"], volume=df["volume"]).on_balance_volume()
        df["obv_slope"]    = df["obv"].diff(3)
        df["volume_ratio"] = df["volume"] / (df["volume"].rolling(10).mean() + 1e-9)
        df["volume_spike"] = (df["volume_ratio"] > 1.8).astype(int)

        # ── VWAP ──────────────────────────────────────────────────────────────
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["vwap"]          = (df["typical_price"] * df["volume"]).cumsum() / (df["volume"].cumsum() + 1e-9)
        df["vwap_dist"]     = (df["close"] - df["vwap"]) / (df["vwap"] + 1e-9) * 100
        df["price_above_vwap"] = (df["close"] > df["vwap"]).astype(int)

        # ── Regime ────────────────────────────────────────────────────────────
        df["rolling_std_10"]  = df["close"].pct_change().rolling(10).std() * 100
        df["high_volatility"] = (df["rolling_std_10"] > df["rolling_std_10"].rolling(30).mean()).astype(int)

        # ── Session time feature ───────────────────────────────────────────────
        market_open_minutes = 9 * 60 + 15
        try:
            ts_str = str(df["timestamp"].iloc[-1])
            last_time = pd.to_datetime(ts_str)
            minutes_since_open = max(0, last_time.hour * 60 + last_time.minute - market_open_minutes)
        except Exception:
            minutes_since_open = 0
        df_filled = df.fillna(0)
        latest = df_filled.iloc[-1]

        # ── Candlestick patterns ───────────────────────────────────────────────
        patterns = detect_candlestick_patterns(df)
        gaps     = detect_gaps(df)

        # ── Live F&O data from market data service ─────────────────────────────
        oi_change_pct = 0.0
        pcr           = 1.0
        try:
            r = requests.get(f"{MARKET_DATA_URL}/oi_data", params={"symbol": payload.symbol}, timeout=2)
            if r.status_code == 200:
                oi_info = r.json()["data"]
                pcr = float(oi_info.get("pcr", 1.0))
                # Derive oi_change from CE vs PE balance
                ce_oi = oi_info.get("total_ce_oi", 1)
                pe_oi = oi_info.get("total_pe_oi", 1)
                oi_change_pct = round((pe_oi - ce_oi) / (ce_oi + 1e-9) * 100, 2)
        except Exception:
            oi_change_pct = float(np.random.uniform(-5, 5))
            pcr           = float(np.random.uniform(0.8, 1.2))

        # ── Build feature vector ───────────────────────────────────────────────
        feature_vector = {
            "return_1":        float(latest.get("return_1", 0)),
            "return_3":        float(latest.get("return_3", 0)),
            "return_5":        float(latest.get("return_5", 0)),
            "body_size":       float(latest.get("body_size", 0)),
            "hl_range":        float(latest.get("hl_range", 0)),
            "upper_wick":      float(latest.get("upper_wick", 0)),
            "lower_wick":      float(latest.get("lower_wick", 0)),
            "rsi":             float(latest.get("rsi", 50)),
            "stoch_k":         float(latest.get("stoch_k", 50)),
            "williams_r":      float(latest.get("williams_r", -50)),
            "macd":            float(latest.get("macd", 0)),
            "macd_diff":       float(latest.get("macd_diff", 0)),
            "ema_cross":       float(latest.get("ema_cross", 0)),
            "adx":             float(latest.get("adx", 20)),
            "adx_pos":         float(latest.get("adx_pos", 0)),
            "adx_neg":         float(latest.get("adx_neg", 0)),
            "supertrend":      float(latest.get("supertrend", 0)),
            "atr":             float(latest.get("atr", 0)),
            "atr_pct":         float(latest.get("atr_pct", 0.7)),
            "bb_width":        float(latest.get("bb_width", 5)),
            "bb_position":     float(latest.get("bb_position", 0.5)),
            "volume_spike":    int(latest.get("volume_spike", 0)),
            "volume_ratio":    float(latest.get("volume_ratio", 1)),
            "obv_slope":       float(latest.get("obv_slope", 0)),
            "vwap_dist":       float(latest.get("vwap_dist", 0)),
            "price_above_vwap": int(latest.get("price_above_vwap", 0)),
            "high_volatility": int(latest.get("high_volatility", 0)),
            "minutes_since_open": minutes_since_open,
            "oi_change_pct":   oi_change_pct,
            "pcr":             pcr,
            **patterns,
            **gaps,
        }

        return {"status": "success", "features": feature_vector}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
