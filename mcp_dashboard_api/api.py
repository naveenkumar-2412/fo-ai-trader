from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import json
import os
from typing import Dict, Any

app = FastAPI(title="Dashboard API Gateway", version="1.0.0")

# Allow requests from the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with the specific frontend origin e.g. "http://localhost:5173"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LIVE_STATE_FILE = "../live_state.json"
TRADE_LOG_FILE = "../trade_log.jsonl"

@app.get("/api/state")
def get_live_state():
    """Returns the current live state of the orchestrator."""
    if not os.path.exists(LIVE_STATE_FILE):
        return {"status": "waiting", "message": "Orchestrator hasn't started yet."}
    
    try:
        with open(LIVE_STATE_FILE, "r") as f:
            state = json.load(f)
        return {"status": "success", "data": state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading live state: {str(e)}")

@app.get("/api/metrics")
def get_metrics():
    """Calculates overall metrics from the trade log."""
    trades = []
    if os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "r") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    trades.append(data["order"])
                except:
                    pass

    total_trades = len(trades)
    if total_trades == 0:
        return {"status": "success", "data": {"win_rate": 0, "profit_factor": 0, "max_drawdown": 0, "total_pnl": 0}}

    winning_trades = [t for t in trades if t.get("pnl", 0) > 0]
    losing_trades = [t for t in trades if t.get("pnl", 0) <= 0]
    
    win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
    
    gross_profit = sum(t.get("pnl", 0) for t in winning_trades)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losing_trades))
    
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)
    
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    
    # Rough simulated max drawdown based on completed trades
    max_drawdown = 0
    peak = 0
    current_balance = 1000000 # Assume starting capital of 10L
    
    for t in trades:
        current_balance += t.get("pnl", 0)
        if current_balance > peak:
            peak = current_balance
        
        drawdown = (peak - current_balance) / peak if peak > 0 else 0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    return {
        "status": "success", 
        "data": {
            "win_rate": round(win_rate * 100, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_drawdown * 100, 2),
            "total_pnl": round(total_pnl, 2),
            "trades": trades[-10:] # Return last 10 trades for the table
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
