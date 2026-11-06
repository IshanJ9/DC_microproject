"""Inspect the database from outside the app (read-only).

    python show_db.py          one-time snapshot: tables, rows, hit/miss totals, latest requests
    python show_db.py --tail   live view: prints each new request as the app logs it
"""
import argparse
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).parent / "flashmart.db"


def connect():
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table(conn, sql, headers):
    rows = conn.execute(sql).fetchall()
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print("  " + "  ".join("-" * w for w in widths))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(tuple(r), widths)))
    print()


def snapshot():
    conn = connect()
    print(f"Database file: {DB}  ({DB.stat().st_size} bytes)\n")
    print("Tables and row counts")
    table(conn, """SELECT name AS table_name, (SELECT COUNT(*) FROM products) AS rows FROM sqlite_master
                   WHERE name = 'products' UNION ALL
                   SELECT 'access_log', COUNT(*) FROM access_log UNION ALL
                   SELECT 'query_log', COUNT(*) FROM query_log""", ["table", "rows"])
    print("products (first 8 rows)")
    table(conn, "SELECT id, name, category, price, stock FROM products ORDER BY id LIMIT 8",
          ["id", "name", "category", "price", "stock"])
    print("Cache results (access_log)")
    table(conn, "SELECT result, COUNT(*) AS requests, ROUND(AVG(ms), 1) AS avg_ms FROM access_log GROUP BY result",
          ["result", "requests", "avg_ms"])
    print("Cache hits per server")
    table(conn, "SELECT server, COUNT(*) AS hits FROM access_log WHERE result = 'hit' GROUP BY server ORDER BY server",
          ["server", "hits"])
    print("Latest 10 requests")
    table(conn, "SELECT ts, key, result, COALESCE(server, '-') AS server, ROUND(ms, 1) AS ms FROM access_log ORDER BY id DESC LIMIT 10",
          ["time", "key", "result", "server", "ms"])
    print("Latest 5 SQL statements the application ran (query_log)")
    table(conn, "SELECT ts, ROUND(ms) AS ms, sql FROM query_log ORDER BY id DESC LIMIT 5", ["time", "ms", "sql"])


def tail():
    conn = connect()
    last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM access_log").fetchone()[0]
    print("Following access_log. Press Ctrl+C to stop.\n")
    while True:
        for r in conn.execute("SELECT * FROM access_log WHERE id > ? ORDER BY id", (last,)):
            last = r["id"]
            where = f"server {r['server']}" if r["server"] else "database"
            print(f"{r['ts']}  {r['result'].upper():<6} {r['key']:<13} {where:<10} {r['ms']:7.1f} ms")
        time.sleep(0.5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", action="store_true")
    try:
        tail() if ap.parse_args().tail else snapshot()
    except KeyboardInterrupt:
        pass
    except sqlite3.OperationalError as exc:
        print(f"Could not read {DB}: {exc}\nStart the app first so the database exists.")
