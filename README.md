# AI F&O Trader

A fully automated AI-powered F&O trading system for Indian markets (NIFTY / BANKNIFTY), built with a microservices architecture.

## Architecture

The system consists of 10 independent services, each running as a FastAPI server:

| Service | Port | Description |
|---|---|---|
| Market Data | 8001 | Fetches live 1-min candles via Yahoo Finance |
| News | 8008 | Ingests real-time market/news headlines and computes sentiment + impact summary |
| Event Bus | 8009 | Lightweight event stream for pipeline stage events (publish/consume) |
| Feature Engine | 8002 | Computes 29 technical features (Supertrend, ADX, OBV, etc.) |
| Prediction | 8003 | LightGBM ML model classifies trend as Bullish/Neutral/Bearish |
| Strategy | 8004 | 7-filter signal generation (VIX regime, ADX, Supertrend alignment, etc.) |
| Risk Manager | 8005 | Kelly Criterion position sizing with daily guardrails |
| Execution | 8006 | Paper trading execution with realistic NSE brokerage simulation |
| Dashboard API | 8007 | Real-time state gateway for the React frontend |
| React Dashboard | 5173 | Live trading dashboard (Vite + React) |

Additionally, generated features are persisted in a local SQLite feature store (`feature_store.db`) for auditability and replay.

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

5. Run real-time prediction simulation (paper mode on live market feed):
   ```
   python main_orchestrator.py --simulation --symbol NIFTY --interval-sec 15
   ```

## Web-based Navigation & Controls

- You can now start/stop orchestrator directly from the dashboard UI (no manual orchestrator command required).
- Open the dashboard and use **Web Control Center** to choose mode (`simulation` / `dry-run` / `live`) and cycle interval.
- API endpoints for web orchestration:
   - `GET /api/orchestrator/status`
   - `POST /api/orchestrator/start`
   - `POST /api/orchestrator/stop`
   - `GET /api/orchestrator/logs`
- Simulation analytics endpoint: `GET /api/simulation`

## Strategy

- **Entry filters**: Confidence ≥ 65%, ADX ≥ 20, Supertrend alignment, OBV confirmation, VIX regime check, BB extremes, market hours
- **News-aware risk gate**: During high-impact news windows, low-confidence trades are blocked
- **Sizing**: Fractional Kelly Criterion (25%) with 30% max capital exposure
- **Risk**: Max 3% daily loss, max 3 consecutive losses, max 8 trades/day
- **Instruments**: BUY_CALL, BUY_PUT, SELL_CALL, SELL_PUT, SELL_STRANGLE based on trend + volatility

## Real-time Simulation

- Uses live market data + full feature/prediction/strategy pipeline.
- Does not place live execution orders in simulation mode.
- Logs each cycle in `simulation_log.jsonl` for replay and analysis.
- Dashboard API endpoint: `GET /api/simulation` from Dashboard Gateway (`http://localhost:8007`).

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
