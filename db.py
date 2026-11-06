"""The real SQLite database the cache protects.

Every query also sleeps for ~180 ms to stand in for a database on another machine,
and gets slower as concurrent load grows.
"""
import random
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

DB_PATH = Path(__file__).parent / "flashmart.db"

ADJ = ["Aero", "Nova", "Pixel", "Urban", "Zen", "Turbo", "Lumen", "Terra", "Echo", "Vivid"]
NOUN = [
    ("Sneakers", "Footwear"), ("Headphones", "Audio"), ("Smart watch", "Wearables"),
    ("Backpack", "Bags"), ("Camera", "Photo"), ("Speaker", "Audio"), ("Jacket", "Apparel"),
    ("Keyboard", "Computing"), ("Water bottle", "Outdoors"), ("Desk lamp", "Home"),
]
COLUMNS = "id, name, category, price, stock"
VERBOSE = True


def echo(line: str) -> None:
    """Backend activity shown live in the terminal that runs app.py."""
    if VERBOSE:
        print(line, flush=True)


def build_catalog(size: int = 200) -> dict[int, dict]:
    rng = random.Random(7)
    catalog = {}
    for pid in range(size):
        noun, category = NOUN[pid % len(NOUN)]
        catalog[pid] = {
            "id": pid,
            "name": f"{ADJ[(pid // len(NOUN)) % len(ADJ)]} {noun}",
            "category": category,
            "price": round(rng.uniform(9, 399), 2),
            "stock": rng.randint(0, 500),
        }
    return catalog


class Database:
    def __init__(self, size: int = 200, path: Path = DB_PATH, base_ms: float = 180,
                 per_inflight_ms: float = 4):
        self.base_ms = base_ms
        self.per_inflight_ms = per_inflight_ms
        self.queries = 0
        self.log: deque[dict] = deque(maxlen=60)
        self._inflight = 0
        self._lock = threading.Lock()
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")    # lets other tools read while the app writes
        self._conn.execute("PRAGMA synchronous=OFF")     # logging every request must stay cheap
        self._seed(size)
        self.catalog = {r["id"]: dict(r) for r in self._conn.execute(f"SELECT {COLUMNS} FROM products")}

    def _seed(self, size):
        c = self._conn
        c.execute("DROP TABLE IF EXISTS products")
        c.execute("""CREATE TABLE products (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
            price REAL NOT NULL, stock INTEGER NOT NULL)""")
        c.executemany(
            "INSERT INTO products VALUES (:id, :name, :category, :price, :stock)",
            build_catalog(size).values())
        c.execute("DROP TABLE IF EXISTS access_log")
        c.execute("""CREATE TABLE access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, key TEXT NOT NULL,
            result TEXT NOT NULL, server TEXT, ms REAL NOT NULL, failover INTEGER NOT NULL)""")
        c.execute("DROP TABLE IF EXISTS query_log")
        c.execute("""CREATE TABLE query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, sql TEXT NOT NULL,
            ms REAL NOT NULL, why TEXT)""")
        c.commit()

    def _record(self, sql, ms, rows, why):
        entry = {"t": time.strftime("%H:%M:%S"), "sql": sql, "ms": round(ms), "rows": rows, "why": why}
        self.log.appendleft(entry)
        with self._lock:
            self._conn.execute("INSERT INTO query_log (ts, sql, ms, why) VALUES (?, ?, ?, ?)",
                               (time.strftime("%Y-%m-%d %H:%M:%S"), sql, ms, why))
            self._conn.commit()
        echo(f"{entry['t']}  SQL    {sql}  ({round(ms)} ms)")
        return entry

    def log_access(self, key, result, server, ms, failover):
        """One row per cache lookup: hit, miss (database used) or bypass (cache off)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO access_log (ts, key, result, server, ms, failover) VALUES (?, ?, ?, ?, ?, ?)",
                (time.strftime("%Y-%m-%d %H:%M:%S"), key, result, server, ms, int(failover)))
            self._conn.commit()
        where = f"server {server}" if server else "database"
        extra = "  (served by replica, owner unreachable)" if failover else ""
        echo(f"{time.strftime('%H:%M:%S')}  {result.upper():<6} {key:<13} {where:<10} {ms:7.1f} ms{extra}")

    def access_summary(self, limit=40) -> dict:
        with self._lock:
            c = self._conn
            recent = [dict(r) for r in c.execute(
                "SELECT id, ts, key, result, server, ms, failover FROM access_log ORDER BY id DESC LIMIT ?", (limit,))]
            totals = {r["result"]: r["n"] for r in c.execute(
                "SELECT result, COUNT(*) AS n FROM access_log GROUP BY result")}
            per_server = {r["server"]: r["n"] for r in c.execute(
                "SELECT server, COUNT(*) AS n FROM access_log WHERE result = 'hit' GROUP BY server")}
        return {"recent": recent, "totals": totals, "hits_per_server": per_server}

    def run_select(self, sql: str, limit: int = 200) -> dict:
        """Read-only SQL for the Database tab's console. Opens its own read-only connection."""
        text = sql.strip().rstrip(";").strip()
        if not text.lower().startswith(("select", "with")):
            raise ValueError("Only SELECT queries are allowed here.")
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            cur = conn.execute(text)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(limit + 1)
        except sqlite3.Error as exc:
            raise ValueError(str(exc)) from exc
        finally:
            conn.close()
        return {"columns": cols, "rows": [list(r) for r in rows[:limit]], "truncated": len(rows) > limit}

    def query(self, pid: int, why: str = "") -> tuple[dict, dict]:
        """SELECT one product. Returns (row, log entry)."""
        with self._lock:
            self.queries += 1
            self._inflight += 1
            delay = self.base_ms + self.per_inflight_ms * self._inflight
        start = time.perf_counter()
        try:
            time.sleep(delay / 1000)
            with self._lock:
                row = self._conn.execute(
                    f"SELECT {COLUMNS} FROM products WHERE id = ?", (pid,)).fetchone()
            ms = (time.perf_counter() - start) * 1000
            entry = self._record(f"SELECT {COLUMNS} FROM products WHERE id = {pid}", ms, 1 if row else 0, why)
            return dict(row), entry
        finally:
            with self._lock:
                self._inflight -= 1

    def update_price(self, pid: int, price: float, why: str = "") -> dict:
        start = time.perf_counter()
        time.sleep(self.base_ms / 1000)
        with self._lock:
            cur = self._conn.execute("UPDATE products SET price = ? WHERE id = ?", (price, pid))
            self._conn.commit()
            self.queries += 1
            if cur.rowcount:
                self.catalog[pid]["price"] = price
        ms = (time.perf_counter() - start) * 1000
        return self._record(f"UPDATE products SET price = {price:.2f} WHERE id = {pid}", ms, cur.rowcount, why)

    def rows(self) -> list[dict]:
        """Admin listing for the Database tab. Not a counted query."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(f"SELECT {COLUMNS} FROM products ORDER BY id")]

    def reset(self):
        with self._lock:
            self.queries = 0
            self.log.clear()
