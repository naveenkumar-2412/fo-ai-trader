from fastapi import FastAPI, HTTPException
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

app = FastAPI(title="Market Data MCP", version="1.0.0")

# Simulated Data Store
def generate_mock_candles(symbol: str, minutes: int = 100):
    now = datetime.now()
    times = [now - timedelta(minutes=i) for i in range(minutes, 0, -1)]
    base_price = 22000 if "NIFTY" in symbol else 48000
    
    df = pd.DataFrame({"timestamp": times})
    df['open'] = np.random.normal(base_price, 20, minutes)
    df['high'] = df['open'] + np.random.uniform(5, 15, minutes)
    df['low'] = df['open'] - np.random.uniform(5, 15, minutes)
    df['close'] = df['open'] + np.random.normal(0, 15, minutes)
    df['volume'] = np.random.randint(1000, 10000, minutes)
    return df.to_dict(orient="records")

import yfinance as yf

# Map generic names to Yahoo Finance tickers
TICKER_MAP = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK"
}

def fetch_live_candles(symbol: str, timeframe: str = "5m", period: str = "5d"):
    """Fetch real market data from Yahoo Finance"""
    yf_ticker = TICKER_MAP.get(symbol, symbol)
    
    try:
        data = yf.download(yf_ticker, period=period, interval=timeframe, progress=False)
        if data.empty:
            # Fallback for weekends/holidays if absolutely needed
            return generate_mock_candles(symbol, 100)
            
        # Format the dataframe to match our expected schema
        df = data.reset_index()
        # Rename columns to lowercase standard
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        
        # Handle index naming variations (Datetime vs Date)
        time_col = 'datetime' if 'datetime' in df.columns else 'date' if 'date' in df.columns else df.columns[0]
        df = df.rename(columns={time_col: 'timestamp'})
        
        # Keep only required columns
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        # Ensure timestamp is string for JSON serialization
        df['timestamp'] = df['timestamp'].astype(str)
        
        return df.to_dict(orient="records")
    except Exception as e:
        print(f"yfinance fetch failed: {e}")
        return generate_mock_candles(symbol, 100)

@app.get("/candles")
def get_candles(symbol: str, timeframe: str = "5m"):
    try:
        data = fetch_live_candles(symbol, timeframe=timeframe)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/option_chain")
def get_option_chain(symbol: str):
    # Mock Option Chain
    return {
        "status": "success",
        "data": {
            "symbol": symbol,
            "spot_price": 22000,
            "calls": [{"strike": 22000, "ltp": 150, "oi": 50000}],
            "puts": [{"strike": 22000, "ltp": 160, "oi": 45000}]
        }
    }

@app.get("/oi_data")
def get_oi_data(symbol: str):
    # Mock OI Data
    return {
        "status": "success", 
        "data": {"symbol": symbol, "total_pe_oi": 1500000, "total_ce_oi": 1200000, "pcr": 1.25}
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
