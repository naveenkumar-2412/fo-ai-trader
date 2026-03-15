"""
Notifications MCP — port 8010
Sends Telegram alerts for trade events and daily summaries.
Configure via environment variables:
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your chat / group ID
Falls back to console print if vars are not set (no crashes).
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
import requests
from datetime import datetime

app = FastAPI(title="Notifications MCP", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TG_URL    = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

CONFIGURED = bool(BOT_TOKEN and CHAT_ID)


# ─── Telegram helper ───────────────────────────────────────────────────────────
def _send_telegram(text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    if not CONFIGURED:
        print(f"[NOTIFY] {text}")
        return True
    try:
        r = requests.post(TG_URL, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=5)
        return r.status_code == 200
    except Exception as e:
        print(f"[NOTIFY ERROR] {e}")
        return False


# ─── Pydantic Models ────────────────────────────────────────────────────────────
class SendPayload(BaseModel):
    message: str

class TradeAlertPayload(BaseModel):
    event:       str          # ENTRY | EXIT | SL_HIT | TARGET_HIT | TRAIL_SL | TIME_EXIT
    symbol:      str
    action:      str
    qty:         int
    price:       float
    pnl:         Optional[float] = None
    pnl_pct:     Optional[float] = None
    order_id:    Optional[str]  = None
    confidence:  Optional[float]= None
    reason:      Optional[str]  = None

class CycleSummaryPayload(BaseModel):
    symbol:       str
    total_trades: int
    win_rate:     float
    net_pnl:      float
    max_drawdown: float
    capital:      float


# ─── Endpoints ─────────────────────────────────────────────────────────────────
@app.post("/send")
def send_message(payload: SendPayload):
    ok = _send_telegram(payload.message)
    return {"status": "sent" if ok else "failed", "configured": CONFIGURED}


@app.post("/trade_alert")
def trade_alert(payload: TradeAlertPayload):
    now = datetime.now().strftime("%H:%M:%S")

    event_icons = {
        "ENTRY":       "🟢",
        "EXIT":        "🔵",
        "SL_HIT":      "🔴",
        "TARGET_HIT":  "✅",
        "TRAIL_SL":    "🟡",
        "TIME_EXIT":   "⏱",
    }
    icon = event_icons.get(payload.event, "📢")

    if payload.event == "ENTRY":
        lines = [
            f"{icon} <b>TRADE ENTRY</b> [{now}]",
            f"Symbol : <b>{payload.symbol}</b>",
            f"Action : <b>{payload.action}</b>",
            f"Qty    : {payload.qty}",
            f"Entry  : ₹{payload.price:.2f}",
            f"Conf   : {(payload.confidence or 0)*100:.1f}%",
            f"ID     : {payload.order_id or '—'}",
        ]
    else:
        pnl_sign = "+" if (payload.pnl or 0) >= 0 else ""
        lines = [
            f"{icon} <b>TRADE {payload.event}</b> [{now}]",
            f"Symbol : <b>{payload.symbol}</b>",
            f"Action : <b>{payload.action}</b>",
            f"Exit   : ₹{payload.price:.2f}",
            f"PnL    : <b>{pnl_sign}₹{payload.pnl or 0:,.0f}</b> ({pnl_sign}{(payload.pnl_pct or 0)*100:.1f}%)",
            f"Reason : {payload.reason or payload.event}",
        ]

    text = "\n".join(lines)
    ok = _send_telegram(text)
    return {"status": "sent" if ok else "failed", "message": text}


@app.post("/cycle_summary")
def cycle_summary(payload: CycleSummaryPayload):
    emoji = "📈" if payload.net_pnl >= 0 else "📉"
    pnl_sign = "+" if payload.net_pnl >= 0 else ""

    text = "\n".join([
        f"{emoji} <b>EOD Summary — {payload.symbol}</b>",
        f"Date      : {datetime.now().strftime('%d %b %Y')}",
        f"Trades    : {payload.total_trades}",
        f"Win Rate  : {payload.win_rate:.1f}%",
        f"Net PnL   : <b>{pnl_sign}₹{payload.net_pnl:,.0f}</b>",
        f"Max DD    : {payload.max_drawdown:.1f}%",
        f"Capital   : ₹{payload.capital:,.0f}",
    ])
    ok = _send_telegram(text)
    return {"status": "sent" if ok else "failed", "message": text}


@app.get("/status")
def status():
    return {
        "configured": CONFIGURED,
        "bot_token_set": bool(BOT_TOKEN),
        "chat_id_set":   bool(CHAT_ID),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
