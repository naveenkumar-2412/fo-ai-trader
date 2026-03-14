from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from datetime import datetime
import os
import json

app = FastAPI(title="Event Bus MCP", version="1.0.0")

EVENT_LOG_FILE = os.getenv("EVENT_LOG_FILE", "pipeline_events.jsonl")
MAX_IN_MEMORY = int(os.getenv("EVENT_BUS_MAX_IN_MEMORY", "2000"))

events: List[Dict[str, Any]] = []


class EventPayload(BaseModel):
    event_type: str
    symbol: str
    stage: str
    payload: Dict[str, Any] = {}


def _persist_event(evt: Dict[str, Any]):
    try:
        with open(EVENT_LOG_FILE, "a") as f:
            f.write(json.dumps(evt, default=str) + "\n")
    except Exception:
        pass


@app.post("/publish")
def publish(payload: EventPayload):
    try:
        evt = {
            "id": len(events) + 1,
            "ts": datetime.utcnow().isoformat(),
            "event_type": payload.event_type,
            "symbol": payload.symbol,
            "stage": payload.stage,
            "payload": payload.payload,
        }
        events.append(evt)
        if len(events) > MAX_IN_MEMORY:
            del events[: len(events) - MAX_IN_MEMORY]
        _persist_event(evt)
        return {"status": "success", "event": evt}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/consume")
def consume(symbol: Optional[str] = None, stage: Optional[str] = None, limit: int = 100):
    limit = max(1, min(limit, 500))
    data = events
    if symbol:
        data = [e for e in data if e.get("symbol") == symbol]
    if stage:
        data = [e for e in data if e.get("stage") == stage]
    return {"status": "success", "count": min(len(data), limit), "data": data[-limit:]}


@app.get("/latest")
def latest(symbol: str = "NIFTY"):
    filtered = [e for e in events if e.get("symbol") == symbol]
    if not filtered:
        return {"status": "success", "data": None}
    return {"status": "success", "data": filtered[-1]}


@app.get("/health")
def health():
    return {
        "status": "success",
        "data": {
            "events_in_memory": len(events),
            "max_in_memory": MAX_IN_MEMORY,
            "event_log_file": EVENT_LOG_FILE,
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8009)
