from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import json, os, requests
from datetime import datetime

app = FastAPI(title="Dashboard API Gateway", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

LIVE_STATE   = "../live_state.json"
TRADE_LOG    = "../trade_log.jsonl"
SIGNAL_LOG   = "../signal_log.jsonl"
PREDICTION_URL = "http://localhost:8003"
RISK_URL       = "http://localhost:8005"
NEWS_URL       = "http://localhost:8008"
EVENT_BUS_URL  = "http://localhost:8009"


def _read_jsonl(path, limit=100):
    if not os.path.exists(path):
        return []
    lines = []
    try:
        with open(path) as f:
            for line in f:
                try: lines.append(json.loads(line.strip()))
                except: pass
    except: pass
    return lines[-limit:]


@app.get("/api/state")
def get_live_state():
    try:
        if os.path.exists(LIVE_STATE):
            with open(LIVE_STATE) as f:
                return json.load(f)
        return {"is_live": False, "current_price": None, "active_trades": [], "prediction": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics")
def get_metrics():
    trades = _read_jsonl(TRADE_LOG)
    closed = [t for t in trades if "pnl" in t.get("order", {})]
    if not closed:
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "net_pnl": 0, "max_drawdown": 0, "recent_trades": []}
    pnls   = [t["order"]["pnl"] for t in closed]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf     = (sum(wins) / abs(sum(losses))) if losses else 999.0
    capital, peak, max_dd = 500_000, 500_000, 0.0
    for p in pnls:
        capital += p; peak = max(peak, capital)
        max_dd = max(max_dd, (peak - capital) / peak * 100)
    recent = [{"time": t.get("time",""), "action": t["order"].get("action",""),
               "pnl": t["order"]["pnl"], "status": "CLOSED"} for t in closed[-10:]]
    return {"total_trades": len(closed), "win_rate": round(len(wins)/len(closed)*100,1),
            "profit_factor": round(pf,2), "net_pnl": round(sum(pnls),2),
            "max_drawdown": round(max_dd,2), "recent_trades": list(reversed(recent))}


@app.get("/api/equity_curve")
def get_equity_curve():
    trades = _read_jsonl(TRADE_LOG, limit=500)
    closed = [t for t in trades if "pnl" in t.get("order", {})]
    if not closed:
        return {"data": [{"time": datetime.now().isoformat(), "capital": 500000}]}
    capital = 500_000.0
    curve   = []
    for t in closed:
        capital += t["order"]["pnl"]
        curve.append({"time": t.get("time",""), "capital": round(capital,2),
                      "pnl": t["order"]["pnl"], "action": t["order"].get("action","")})
    return {"data": curve}


@app.get("/api/signals")
def get_signals(limit: int = 20):
    signals = _read_jsonl(SIGNAL_LOG, limit=limit)
    return {"data": list(reversed(signals))}


@app.get("/api/feature_importance")
def get_feature_importance():
    try:
        r = requests.get(f"{PREDICTION_URL}/feature_importance", timeout=3)
        if r.status_code == 200: return r.json()
    except: pass
    mock = {"adx":9800,"rsi":8500,"vwap_dist":7200,"macd_diff":6900,"atr_pct":6400,
            "bb_position":5800,"volume_ratio":5200,"ema_cross":4900,"obv_slope":4400,"pcr":4100}
    return {"status":"success","data":mock}


@app.get("/api/risk_status")
def get_risk_status():
    try:
        r = requests.get(f"{RISK_URL}/summary", timeout=3)
        if r.status_code == 200: return r.json()
    except: pass
    return {"status":"offline","data":{}}


@app.get("/api/news")
def get_news(symbol: str = "NIFTY"):
    """Aggregates news summary + top headlines for the dashboard."""
    try:
        summary_resp = requests.get(f"{NEWS_URL}/summary",
                                    params={"symbol": symbol, "lookback_minutes": 30}, timeout=4)
        headlines_resp = requests.get(f"{NEWS_URL}/headlines",
                                      params={"symbol": symbol, "limit": 8}, timeout=4)
        summary   = summary_resp.json().get("data",{}) if summary_resp.status_code==200 else {}
        headlines = headlines_resp.json().get("data",[]) if headlines_resp.status_code==200 else []
        return {"status":"success","symbol":symbol,"summary":summary,"headlines":headlines}
    except Exception as e:
        return {"status":"error","error":str(e),"summary":{},"headlines":[]}


@app.get("/api/events")
def get_events(limit: int = 20):
    """Live event bus stream for the dashboard."""
    try:
        r = requests.get(f"{EVENT_BUS_URL}/consume", params={"limit": limit}, timeout=3)
        if r.status_code == 200:
            data = r.json().get("data", [])
            return {"status":"success","data":list(reversed(data))}
    except: pass
    return {"status":"offline","data":[]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
