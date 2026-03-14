import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, Any, List


DB_PATH = os.getenv("FEATURE_STORE_DB", "feature_store.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            feature_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_features_symbol_ts
        ON features(symbol, ts)
        """
    )
    return conn


def save_features(symbol: str, features: Dict[str, Any], ts: str = "") -> None:
    timestamp = ts or datetime.utcnow().isoformat()
    payload = json.dumps(features, default=str)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO features(symbol, ts, feature_json) VALUES (?, ?, ?)",
            (symbol, timestamp, payload),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_features(symbol: str, limit: int = 1) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT symbol, ts, feature_json FROM features WHERE symbol = ? ORDER BY ts DESC LIMIT ?",
            (symbol, max(1, min(limit, 1000))),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    output = []
    for sym, ts, feature_json in rows:
        try:
            features = json.loads(feature_json)
        except Exception:
            features = {}
        output.append({"symbol": sym, "timestamp": ts, "features": features})
    return output
