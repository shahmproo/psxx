"""
Database layer for PSX Market Dashboard.
Utilizes SQLite with Write-Ahead Logging (WAL) for concurrent reads/writes and crash resilience.
"""
import sqlite3
import os
import threading
from typing import List, Optional, Dict, Any
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "psx_dashboard.db")
_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    """Initializes tables and default settings."""
    with _lock, get_connection() as conn:
        cursor = conn.cursor()

        # 1. Alerts Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                condition_type TEXT NOT NULL,
                target_value REAL NOT NULL,
                trigger_mode TEXT NOT NULL DEFAULT 'cooldown',
                cooldown_minutes INTEGER NOT NULL DEFAULT 15,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_triggered_at TEXT,
                created_at TEXT NOT NULL,
                notes TEXT
            );
        """)

        # Index for symbol lookup
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts(symbol);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_active);")

        # 2. Alert History Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                condition_type TEXT NOT NULL,
                target_value REAL NOT NULL,
                actual_value REAL NOT NULL,
                message TEXT NOT NULL,
                triggered_at TEXT NOT NULL,
                telegram_sent INTEGER NOT NULL DEFAULT 0,
                telegram_error TEXT,
                FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_triggered ON alert_history(triggered_at DESC);")

        # 3. Settings Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Default settings seed
        default_settings = {
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "telegram_enabled": "0",
            "poll_interval_seconds": "8",
            "min_volume_gainers": "50000",
            "sound_alerts_enabled": "1"
        }
        for k, v in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);", (k, v))

        conn.commit()


# --- Alerts CRUD ---

def get_all_alerts() -> List[Dict[str, Any]]:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.*, COUNT(h.id) AS trigger_count
            FROM alerts a
            LEFT JOIN alert_history h ON a.id = h.alert_id
            GROUP BY a.id
            ORDER BY a.id DESC;
        """)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_active_alerts() -> List[Dict[str, Any]]:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM alerts WHERE is_active = 1;")
        return [dict(r) for r in cursor.fetchall()]


def get_alert_by_id(alert_id: int) -> Optional[Dict[str, Any]]:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.*, COUNT(h.id) AS trigger_count
            FROM alerts a
            LEFT JOIN alert_history h ON a.id = h.alert_id
            WHERE a.id = ?
            GROUP BY a.id;
        """, (alert_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def create_alert(symbol: str, name: str, condition_type: str, target_value: float,
                 trigger_mode: str = "cooldown", cooldown_minutes: int = 15,
                 notes: Optional[str] = None) -> int:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        now_str = datetime.utcnow().isoformat()
        cursor.execute("""
            INSERT INTO alerts (symbol, name, condition_type, target_value, trigger_mode, cooldown_minutes, is_active, created_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?);
        """, (symbol.upper().strip(), name.strip(), condition_type, target_value, trigger_mode, cooldown_minutes, now_str, notes))
        conn.commit()
        return cursor.lastrowid


def update_alert(alert_id: int, **kwargs) -> bool:
    if not kwargs:
        return False
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        fields = []
        values = []
        for k, v in kwargs.items():
            fields.append(f"{k} = ?")
            values.append(v)
        values.append(alert_id)
        query = f"UPDATE alerts SET {', '.join(fields)} WHERE id = ?;"
        cursor.execute(query, tuple(values))
        conn.commit()
        return cursor.rowcount > 0


def toggle_alert(alert_id: int) -> Optional[bool]:
    alert = get_alert_by_id(alert_id)
    if not alert:
        return None
    new_state = 0 if alert["is_active"] else 1
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE alerts SET is_active = ? WHERE id = ?;", (new_state, alert_id))
        conn.commit()
        return bool(new_state)


def delete_alert(alert_id: int) -> bool:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM alerts WHERE id = ?;", (alert_id,))
        conn.commit()
        return cursor.rowcount > 0


def update_alert_last_triggered(alert_id: int, timestamp_str: str, disable_alert: bool = False):
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        if disable_alert:
            cursor.execute("UPDATE alerts SET last_triggered_at = ?, is_active = 0 WHERE id = ?;", (timestamp_str, alert_id))
        else:
            cursor.execute("UPDATE alerts SET last_triggered_at = ? WHERE id = ?;", (timestamp_str, alert_id))
        conn.commit()


# --- Alert History ---

def record_alert_history(alert_id: int, symbol: str, condition_type: str,
                         target_value: float, actual_value: float, message: str,
                         telegram_sent: bool, telegram_error: Optional[str] = None) -> int:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        now_str = datetime.utcnow().isoformat()
        cursor.execute("""
            INSERT INTO alert_history (alert_id, symbol, condition_type, target_value, actual_value, message, triggered_at, telegram_sent, telegram_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (alert_id, symbol, condition_type, target_value, actual_value, message, now_str, 1 if telegram_sent else 0, telegram_error))
        conn.commit()
        return cursor.lastrowid


def get_alert_history(limit: int = 50) -> List[Dict[str, Any]]:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM alert_history
            ORDER BY id DESC
            LIMIT ?;
        """, (limit,))
        return [dict(r) for r in cursor.fetchall()]


# --- Settings ---

def get_settings() -> Dict[str, Any]:
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings;")
        rows = cursor.fetchall()
        res = {r["key"]: r["value"] for r in rows}
        return {
            "telegram_bot_token": res.get("telegram_bot_token", ""),
            "telegram_chat_id": res.get("telegram_chat_id", ""),
            "telegram_enabled": res.get("telegram_enabled", "0") == "1",
            "poll_interval_seconds": int(res.get("poll_interval_seconds", "8")),
            "min_volume_gainers": int(res.get("min_volume_gainers", "50000")),
            "sound_alerts_enabled": res.get("sound_alerts_enabled", "1") == "1"
        }


def save_settings(settings: Dict[str, Any]):
    with _lock, get_connection() as conn:
        cursor = conn.cursor()
        for k, v in settings.items():
            if isinstance(v, bool):
                str_val = "1" if v else "0"
            else:
                str_val = str(v)
            cursor.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            """, (k, str_val))
        conn.commit()
