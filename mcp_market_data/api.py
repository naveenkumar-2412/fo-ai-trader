from fastapi import FastAPI, HTTPException, Query
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import math
import yfinance as yf
import os
from typing import Optional

app = FastAPI(title="Market Data MCP", version="2.0.0")

# ─── Ticker Map ───────────────────────────────────────────────────────────────
TICKER_MAP = {
    "NIFTY":     "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "FINIFTY":   "^CNXFIN",
}

LOT_SIZES = {"NIFTY": 50, "BANKNIFTY": 15, "FINIFTY": 40}

# ─── Simple In-Memory Cache ────────────────────────────────────────────────────
_cache: dict = {}
_last_live_fetch_ts: Optional[float] = None

ALLOW_MOCK_DATA = os.getenv("ALLOW_MOCK_DATA", "false").strip().lower() == "true"
CANDLE_CACHE_TTL = int(os.getenv("CANDLE_CACHE_TTL", "20"))
QUOTE_CACHE_TTL = int(os.getenv("QUOTE_CACHE_TTL", "3"))
OPTION_CHAIN_CACHE_TTL = int(os.getenv("OPTION_CHAIN_CACHE_TTL", "15"))


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < entry.get("ttl", CANDLE_CACHE_TTL):
        return entry["data"]
    return None


def _cache_set(key: str, data, ttl: int):
    _cache[key] = {"data": data, "ts": time.time(), "ttl": ttl}


# ─── Fallback Mock ─────────────────────────────────────────────────────────────
def generate_mock_candles(symbol: str, minutes: int = 100):
    now = datetime.now()
    times = [now - timedelta(minutes=i) for i in range(minutes, 0, -1)]
    base = 22000 if "NIFTY" in symbol else 48000
    df = pd.DataFrame({"timestamp": [str(t) for t in times]})
    df["open"]   = np.random.normal(base, 20, minutes)
    df["high"]   = df["open"] + np.random.uniform(5, 15, minutes)
    df["low"]    = df["open"] - np.random.uniform(5, 15, minutes)
    df["close"]  = df["open"] + np.random.normal(0, 15, minutes)
    df["volume"] = np.random.randint(1000, 10000, minutes)
    return df.to_dict(orient="records")


def fetch_live_candles(symbol: str, timeframe: str = "1m", period: str = "1d"):
    global _last_live_fetch_ts

    cache_key = f"candles:{symbol}:{timeframe}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    yf_ticker = TICKER_MAP.get(symbol, symbol)
    try:
        data = yf.download(yf_ticker, period=period, interval=timeframe, progress=False)
        if data.empty:
            if ALLOW_MOCK_DATA:
                mock_data = generate_mock_candles(symbol, 100)
                _cache_set(cache_key, mock_data, CANDLE_CACHE_TTL)
                return mock_data
            raise RuntimeError(f"No live candle data available for {symbol}")

        df = data.reset_index()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        time_col = next((c for c in df.columns if c in ("datetime", "date", "timestamp")), df.columns[0])
        df = df.rename(columns={time_col: "timestamp"})
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df["timestamp"] = df["timestamp"].astype(str)
        result = df.to_dict(orient="records")
        _last_live_fetch_ts = time.time()
        _cache_set(cache_key, result, CANDLE_CACHE_TTL)
        return result
    except Exception as e:
        print(f"yfinance fetch failed ({symbol}): {e}")
        if ALLOW_MOCK_DATA:
            mock_data = generate_mock_candles(symbol, 100)
            _cache_set(cache_key, mock_data, CANDLE_CACHE_TTL)
            return mock_data
        raise


def get_spot_price(symbol: str) -> float:
    """Fast spot-price-only fetch, cached separately."""
    cache_key = f"quote:{symbol}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    candles = fetch_live_candles(symbol)
    price = float(candles[-1]["close"]) if candles else 22000.0
    _cache_set(cache_key, price, QUOTE_CACHE_TTL)
    return price


# ─── Black-Scholes Proxy for option premium ───────────────────────────────────
def bs_approx_premium(spot: float, strike: float, atr_pct: float, days_to_expiry: int = 7, is_call: bool = True) -> float:
    """
    Rough B-S approximation:
      sigma ≈ atr_pct * sqrt(252)  (annualized IV from daily ATR %)
      premium ≈ intrinsic + time_value
    """
    sigma_annual = (atr_pct / 100) * math.sqrt(252)
    T = days_to_expiry / 365
    time_val = spot * sigma_annual * math.sqrt(T) * 0.4  # simplified
    intrinsic = max(0, (spot - strike) if is_call else (strike - spot))
    return round(max(15, intrinsic + time_val), 2)


def build_option_chain(symbol: str, spot: float, atr_pct: float):
    """
    Build a realistic synthetic option chain:
    - ATM strike rounded to nearest 50 (NIFTY/BANKNIFTY)
    - ±5 strikes in steps of 50
    - Premiums from BS approx
    - OI: higher at round numbers, follows Gaussian around ATM
    """
    step = 100 if "BANK" in symbol else 50
    atm = round(spot / step) * step
    strikes = [atm + i * step for i in range(-5, 6)]

    total_ce_oi = 0
    total_pe_oi = 0
    calls, puts = [], []

    for i, strike in enumerate(strikes):
        moneyness = abs(strike - atm) / atm
        oi_base = max(10000, int(1_500_000 * math.exp(-8 * moneyness)))
        oi_base += np.random.randint(-oi_base // 10, oi_base // 10)

        ce_oi = int(oi_base * np.random.uniform(0.8, 1.2))
        pe_oi = int(oi_base * np.random.uniform(0.8, 1.2))

        call_premium = bs_approx_premium(spot, strike, atr_pct, is_call=True)
        put_premium  = bs_approx_premium(spot, strike, atr_pct, is_call=False)

        oi_change = round(np.random.uniform(-8, 8), 2)

        calls.append({"strike": strike, "ltp": call_premium, "oi": ce_oi, "oi_change_pct": oi_change})
        puts.append( {"strike": strike, "ltp": put_premium,  "oi": pe_oi, "oi_change_pct": -oi_change})
        total_ce_oi += ce_oi
        total_pe_oi += pe_oi

    pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else 1.0
    return {
        "spot_price":    round(spot, 2),
        "atm_strike":    atm,
        "expiry_days":   7,
        "calls":         calls,
        "puts":          puts,
        "total_ce_oi":   total_ce_oi,
        "total_pe_oi":   total_pe_oi,
        "pcr":           pcr,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/candles")
def get_candles(symbol: str = "NIFTY", timeframe: str = "1m"):
    try:
        data = fetch_live_candles(symbol, timeframe=timeframe)
        return {"status": "success", "symbol": symbol, "count": len(data), "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/quote")
def get_quote(symbol: str = "NIFTY"):
    """Fast single-price quote endpoint."""
    try:
        price = get_spot_price(symbol)
        return {
            "status":  "success",
            "symbol":  symbol,
            "price":   price,
            "time":    datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/option_chain")
def get_option_chain(symbol: str = "NIFTY"):
    try:
        cache_key = f"option_chain:{symbol}"
        cached = _cache_get(cache_key)
        if cached:
            return {"status": "success", "symbol": symbol, "data": cached}

        spot = get_spot_price(symbol)
        candles = fetch_live_candles(symbol)
        closes = [c["close"] for c in candles[-15:]]
        atr_pct = float(np.std(np.diff(closes)) / np.mean(closes) * 100) if len(closes) > 2 else 0.7

        chain = build_option_chain(symbol, spot, atr_pct)
        _cache_set(cache_key, chain, OPTION_CHAIN_CACHE_TTL)
        return {"status": "success", "symbol": symbol, "data": chain}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    now = time.time()
    age = None if _last_live_fetch_ts is None else round(now - _last_live_fetch_ts, 2)
    return {
        "status": "success",
        "data": {
            "allow_mock_data": ALLOW_MOCK_DATA,
            "last_live_fetch_age_sec": age,
            "cache_items": len(_cache),
        },
    }


@app.get("/oi_data")
def get_oi_data(symbol: str = "NIFTY"):
    """Returns PCR and aggregate OI data. Powered by the option chain builder."""
    try:
        spot = get_spot_price(symbol)
        candles = fetch_live_candles(symbol)
        closes = [c["close"] for c in candles[-15:]]
        atr_pct = float(np.std(np.diff(closes)) / np.mean(closes) * 100) if len(closes) > 2 else 0.7

        chain = build_option_chain(symbol, spot, atr_pct)
        return {
            "status": "success",
            "data": {
                "symbol":       symbol,
                "spot_price":   chain["spot_price"],
                "total_ce_oi":  chain["total_ce_oi"],
                "total_pe_oi":  chain["total_pe_oi"],
                "pcr":          chain["pcr"],
                "atm_strike":   chain["atm_strike"],
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
