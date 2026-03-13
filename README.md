# AI F&O Trader

A fully automated AI-powered F&O trading system for Indian markets (NIFTY / BANKNIFTY), built with a microservices architecture.

## Architecture

The system consists of 8 independent services, each running as a FastAPI server:

| Service | Port | Description |
|---|---|---|
| Market Data | 8001 | Fetches live 1-min candles via Yahoo Finance |
| Feature Engine | 8002 | Computes 29 technical features (Supertrend, ADX, OBV, etc.) |
| Prediction | 8003 | LightGBM ML model classifies trend as Bullish/Neutral/Bearish |
| Strategy | 8004 | 7-filter signal generation (VIX regime, ADX, Supertrend alignment, etc.) |
| Risk Manager | 8005 | Kelly Criterion position sizing with daily guardrails |
| Execution | 8006 | Paper trading execution with realistic NSE brokerage simulation |
| Dashboard API | 8007 | Real-time state gateway for the React frontend |
| React Dashboard | 5173 | Live trading dashboard (Vite + React) |

## Quick Start

1. Install dependencies:
   ```
   pip install -r requirements.txt
   cd dashboard-ui && npm install
   ```

2. Start all services:
   ```
   run_all.bat
   ```

3. Open the dashboard: `http://localhost:5173`

4. Start the orchestrator (after 9:15 AM on a trading day):
   ```
   python main_orchestrator.py
   ```

## Strategy

- **Entry filters**: Confidence ≥ 65%, ADX ≥ 20, Supertrend alignment, OBV confirmation, VIX regime check, BB extremes, market hours
- **Sizing**: Fractional Kelly Criterion (25%) with 30% max capital exposure
- **Risk**: Max 3% daily loss, max 3 consecutive losses, max 8 trades/day
- **Instruments**: BUY_CALL, BUY_PUT, SELL_CALL, SELL_PUT, SELL_STRANGLE based on trend + volatility

## Backtesting Results (Simulated, 60 days)

```
Total Trades   : 114
Win Rate       : 59.6%
Profit Factor  : 2.41
Max Drawdown   : 6.2%
Net Profit     : Rs 7,50,188 on Rs 5,00,000 capital
```

## Training

```
cd training
python train_lgbm.py
```

Uses 5-fold TimeSeriesSplit walk-forward validation to avoid data leakage.
