import sqlite3
import time
import threading
from typing import Optional

_DB_PATH = None
_DB_LOCK = threading.Lock()

def init_db(path: str):
    global _DB_PATH
    _DB_PATH = path
    with _DB_LOCK:
        con = sqlite3.connect(_DB_PATH, timeout=5)
        try:
            cur = con.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS token_buckets (
                name TEXT PRIMARY KEY,
                capacity REAL,
                tokens REAL,
                refill_rate_per_sec REAL,
                last_ts REAL
            )
            """)
            con.commit()
        finally:
            con.close()


def _now():
    return time.time()


def create_bucket(name: str, capacity: float, refill_rate_per_sec: float):
    with _DB_LOCK:
        con = sqlite3.connect(_DB_PATH, timeout=5)
        try:
            cur = con.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO token_buckets (name, capacity, tokens, refill_rate_per_sec, last_ts) VALUES (?, ?, ?, ?, ?)",
                (name, capacity, capacity, refill_rate_per_sec, _now()),
            )
            con.commit()
        finally:
            con.close()


def try_consume(name: str, amount: float = 1.0, block: bool = False, timeout: Optional[float] = None) -> bool:
    """Attempt to consume `amount` tokens atomically. If `block` is True,
    waits up to `timeout` seconds for tokens to become available.
    Returns True on success, False otherwise.
    """
    start = _now()
    while True:
        with _DB_LOCK:
            con = sqlite3.connect(_DB_PATH, timeout=5)
            try:
                cur = con.cursor()
                cur.execute("SELECT capacity, tokens, refill_rate_per_sec, last_ts FROM token_buckets WHERE name = ?", (name,))
                row = cur.fetchone()
                if not row:
                    return False
                capacity, tokens, rate, last_ts = row
                now = _now()
                # refill
                delta = max(0.0, now - last_ts)
                tokens = min(capacity, tokens + delta * rate)
                if tokens >= amount:
                    tokens -= amount
                    cur.execute("UPDATE token_buckets SET tokens = ?, last_ts = ? WHERE name = ?", (tokens, now, name))
                    con.commit()
                    return True
                else:
                    # not enough tokens
                    cur.execute("UPDATE token_buckets SET tokens = ?, last_ts = ? WHERE name = ?", (tokens, now, name))
                    con.commit()
            finally:
                con.close()
        if not block:
            return False
        if timeout is not None and (_now() - start) >= timeout:
            return False
        time.sleep(0.05)
