from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import os
import requests
from datetime import datetime
from pathlib import Path
import subprocess
import threading
import sys

app = FastAPI(title="Dashboard API Gateway", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Paths ───────────────────────────────────────────────────────────────────
LIVE_STATE   = "../live_state.json"
TRADE_LOG    = "../trade_log.jsonl"
SIGNAL_LOG   = "../signal_log.jsonl"
SIM_LOG      = "../simulation_log.jsonl"
PREDICTION_URL = "http://localhost:8003"
RISK_URL       = "http://localhost:8005"
EVENT_BUS_URL  = "http://localhost:8009"
FEATURE_URL    = "http://localhost:8002"

ORCHESTRATOR_PATH = str((Path(__file__).resolve().parent.parent / "main_orchestrator.py").resolve())
ORCH_LOG = str((Path(__file__).resolve().parent.parent / "orchestrator_runtime.log").resolve())

_orch_lock = threading.Lock()
_orch_process: subprocess.Popen | None = None
_orch_meta = {
    "running": False,
    "mode": "live",
    "symbol": "NIFTY",
    "interval_sec": 15,
    "started_at": None,
    "pid": None,
}


class OrchestratorStartRequest(BaseModel):
    symbol: str = "NIFTY"
    mode: str = "simulation"  # simulation | dry-run | live
    interval_sec: int = 15


def _is_process_running() -> bool:
    global _orch_process
    return _orch_process is not None and _orch_process.poll() is None


def _sync_orchestrator_meta():
    if not _is_process_running():
        _orch_meta.update({"running": False, "pid": None})


def _read_jsonl(path: str, limit: int = 100) -> list:
    if not os.path.exists(path):
        return []
    lines = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    lines.append(json.loads(line.strip()))
                except Exception:
                    pass
    except Exception:
        pass
    return lines[-limit:]


def _tail_file(path: str, lines: int = 200) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            rows = f.readlines()
        return [r.rstrip("\n") for r in rows[-max(1, min(lines, 2000)):]]
    except Exception:
        return []


# ─── /api/state ───────────────────────────────────────────────────────────────
@app.get("/api/state")
def get_live_state():
    try:
        if os.path.exists(LIVE_STATE):
            with open(LIVE_STATE) as f:
                data = json.load(f)
        else:
            data = {"is_live": False, "current_price": None, "active_trade": None, "prediction": None}
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── /api/metrics ─────────────────────────────────────────────────────────────
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
    pf     = (sum(wins) / abs(sum(losses))) if losses else float("inf")

    # Drawdown
    capital = 500_000
    peak    = capital
    max_dd  = 0.0
    for p in pnls:
        capital += p
        peak    = max(peak, capital)
        dd      = (peak - capital) / peak * 100
        max_dd  = max(max_dd, dd)

    recent = [
        {"time": t.get("time", ""),
         "action": t["order"].get("action", ""),
         "pnl": t["order"].get("pnl", 0),
         "status": "CLOSED"}
        for t in closed[-10:]
    ]

    return {
        "total_trades":  len(closed),
        "win_rate":      round(len(wins) / len(closed) * 100, 1),
        "profit_factor": round(pf, 2),
        "net_pnl":       round(sum(pnls), 2),
        "max_drawdown":  round(max_dd, 2),
        "recent_trades": list(reversed(recent)),
    }


# ─── /api/equity_curve ────────────────────────────────────────────────────────
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
        curve.append({
            "time":    t.get("time", ""),
            "capital": round(capital, 2),
            "pnl":     t["order"]["pnl"],
            "action":  t["order"].get("action", ""),
        })
    return {"data": curve}


# ─── /api/signals ─────────────────────────────────────────────────────────────
@app.get("/api/signals")
def get_signals(limit: int = 20):
    signals = _read_jsonl(SIGNAL_LOG, limit=limit)
    return {"data": list(reversed(signals))}


# ─── /api/feature_importance ──────────────────────────────────────────────────
@app.get("/api/feature_importance")
def get_feature_importance():
    try:
        r = requests.get(f"{PREDICTION_URL}/feature_importance", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    # Fallback mocked importances
    mock = {
        "adx": 9800, "rsi": 8500, "vwap_dist": 7200, "macd_diff": 6900,
        "atr_pct": 6400, "bb_position": 5800, "volume_ratio": 5200,
        "ema_cross": 4900, "obv_slope": 4400, "pcr": 4100,
    }
    return {"status": "success", "data": mock}


# ─── /api/risk_status ─────────────────────────────────────────────────────────
@app.get("/api/risk_status")
def get_risk_status():
    try:
        r = requests.get(f"{RISK_URL}/summary", timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"status": "offline", "data": {}}


@app.get("/api/events")
def get_pipeline_events(limit: int = 100, symbol: str = ""):
    try:
        params = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        r = requests.get(f"{EVENT_BUS_URL}/consume", params=params, timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"status": "offline", "count": 0, "data": []}


@app.get("/api/latest_features")
def get_latest_features(symbol: str = "NIFTY", limit: int = 1):
    try:
        r = requests.get(f"{FEATURE_URL}/latest_features", params={"symbol": symbol, "limit": limit}, timeout=3)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"status": "offline", "count": 0, "data": []}


@app.get("/api/simulation")
def get_simulation(limit: int = 200):
    rows = _read_jsonl(SIM_LOG, limit=max(1, min(limit, 2000)))
    if not rows:
        return {
            "status": "success",
            "count": 0,
            "data": [],
            "summary": {
                "trade_candidates": 0,
                "no_trade_cycles": 0,
                "avg_confidence": 0.0,
                "bullish": 0,
                "neutral": 0,
                "bearish": 0,
            },
        }

    trade_candidates = [r for r in rows if isinstance(r.get("signal"), dict)]
    no_trade_cycles = [r for r in rows if r.get("signal") == "no_trade"]
    confidences = [float(r.get("confidence", 0)) for r in rows if r.get("confidence") is not None]
    avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    trend_counts = {"bullish": 0, "neutral": 0, "bearish": 0}
    for r in rows:
        t = (r.get("trend") or "").lower()
        if t in trend_counts:
            trend_counts[t] += 1

    return {
        "status": "success",
        "count": len(rows),
        "data": list(reversed(rows)),
        "summary": {
            "trade_candidates": len(trade_candidates),
            "no_trade_cycles": len(no_trade_cycles),
            "avg_confidence": avg_conf,
            **trend_counts,
        },
    }


@app.get("/api/orchestrator/status")
def orchestrator_status():
    with _orch_lock:
        _sync_orchestrator_meta()
        return {"status": "success", "data": {**_orch_meta}}


@app.post("/api/orchestrator/start")
def orchestrator_start(payload: OrchestratorStartRequest):
    global _orch_process
    with _orch_lock:
        if _is_process_running():
            return {"status": "success", "data": {**_orch_meta}, "message": "Orchestrator already running"}

        symbol = (payload.symbol or "NIFTY").upper()
        if symbol not in {"NIFTY", "BANKNIFTY", "FINIFTY"}:
            raise HTTPException(status_code=400, detail="Invalid symbol")

        mode = (payload.mode or "simulation").lower()
        if mode not in {"simulation", "dry-run", "live"}:
            raise HTTPException(status_code=400, detail="Invalid mode")

        interval = max(5, int(payload.interval_sec or 15))

        cmd = [sys.executable, "-u", ORCHESTRATOR_PATH, "--symbol", symbol, "--interval-sec", str(interval)]
        if mode == "simulation":
            cmd.append("--simulation")
        elif mode == "dry-run":
            cmd.append("--dry-run")

        try:
            log_handle = open(ORCH_LOG, "a", encoding="utf-8")
            _orch_process = subprocess.Popen(
                cmd,
                cwd=str(Path(ORCHESTRATOR_PATH).parent),
                stdout=log_handle,
                stderr=log_handle,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to start orchestrator: {e}")

        _orch_meta.update(
            {
                "running": True,
                "mode": mode,
                "symbol": symbol,
                "interval_sec": interval,
                "started_at": datetime.now().isoformat(),
                "pid": _orch_process.pid,
            }
        )
        return {"status": "success", "data": {**_orch_meta}}


@app.post("/api/orchestrator/stop")
def orchestrator_stop():
    global _orch_process
    with _orch_lock:
        if not _is_process_running():
            _sync_orchestrator_meta()
            return {"status": "success", "data": {**_orch_meta}, "message": "Orchestrator is not running"}

        try:
            _orch_process.terminate()
            _orch_process.wait(timeout=8)
        except Exception:
            try:
                _orch_process.kill()
            except Exception:
                pass

        _orch_process = None
        _orch_meta.update({"running": False, "pid": None})
        return {"status": "success", "data": {**_orch_meta}}


@app.get("/api/orchestrator/logs")
def orchestrator_logs(lines: int = 200):
    data = _tail_file(ORCH_LOG, lines=lines)
    return {"status": "success", "count": len(data), "data": data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
