"""FlashMart: storefront + ops dashboard on top of the distributed cache."""
import argparse
import logging
import random
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, redirect, render_template, request

from cluster import Cluster
import db as db_module
from db import Database

app = Flask(__name__)
database = Database(200)
catalog = database.catalog
cluster = Cluster(database, catalog)
traffic = {"running": False, "done": 0, "total": 0}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/ops")
def ops():
    return redirect("/#control")


@app.get("/api/locate")
def api_locate():
    key = request.args.get("key", "").strip()[:80]
    if not key:
        return jsonify(error="enter a key"), 400
    return jsonify(cluster.locate(key))


@app.get("/api/catalog")
def api_catalog():
    return jsonify(database.rows())


@app.get("/api/db/log")
def api_db_log():
    return jsonify(queries=database.queries, log=list(database.log)[:30])


@app.get("/api/db/access")
def api_db_access():
    return jsonify(database.access_summary(int(request.args.get("limit", 40))))


@app.post("/api/db/sql")
def api_db_sql():
    sql = str(request.get_json(force=True).get("sql", ""))[:2000]
    try:
        return jsonify(database.run_select(sql))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/db/price")
def api_db_price():
    body = request.get_json(force=True)
    try:
        pid, price = int(body["id"]), round(float(body["price"]), 2)
    except (KeyError, ValueError, TypeError):
        return jsonify(error="send a product id and a numeric price"), 400
    if pid not in catalog or not 0 < price < 100000:
        return jsonify(error="unknown product or price out of range"), 400
    return jsonify(cluster.update_price(pid, price))


@app.get("/api/trace")
def api_trace():
    key = request.args.get("key", "").strip()[:80]
    if not key:
        return jsonify(error="enter a key"), 400
    return jsonify(cluster.trace(key))


@app.post("/api/evict")
def api_evict():
    key = str(request.get_json(force=True).get("key", ""))[:80]
    return jsonify(removed=cluster.evict(key))


@app.get("/api/simulate")
def api_simulate():
    try:
        return jsonify(cluster.simulate(request.args.get("action", "add"), request.args.get("name")))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/product/<int:pid>")
def api_product(pid):
    if pid not in catalog:
        return jsonify(error="no such product"), 404
    return jsonify(cluster.fetch_product(pid))


@app.get("/api/state")
def api_state():
    return jsonify(cluster.state() | {"traffic": dict(traffic)})


@app.post("/api/nodes")
def api_nodes():
    body = request.get_json(force=True)
    action, name = body.get("action"), body.get("name")
    try:
        if action == "add":
            name = cluster.add_node(name)
        elif action == "kill":
            cluster.kill_node(name)
        else:
            return jsonify(error="unknown action"), 400
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, name=name)


@app.post("/api/settings")
def api_settings():
    body = request.get_json(force=True)
    if "cache_enabled" in body:
        cluster.cache_enabled = bool(body["cache_enabled"])
        cluster.log("Cache " + ("enabled" if cluster.cache_enabled else "DISABLED: all reads hit the database"))
    if "vnodes" in body:
        cluster.set_vnodes(max(1, min(300, int(body["vnodes"]))))
    return jsonify(ok=True)


@app.post("/api/reset")
def api_reset():
    cluster.reset()
    return jsonify(ok=True)


@app.post("/api/traffic")
def api_traffic():
    if traffic["running"]:
        return jsonify(error="traffic already running"), 400
    body = request.get_json(force=True)
    total = max(1, min(3000, int(body.get("requests", 400))))
    workers = max(1, min(64, int(body.get("workers", 24))))
    traffic.update(running=True, done=0, total=total)
    cluster.log(f"Flash sale started: {total} requests from {workers} concurrent users", "change")

    def one(_):
        # skewed popularity: a few hot products take most of the traffic
        pid = int(len(catalog) * random.random() ** 2.5)
        cluster.fetch_product(pid)
        traffic["done"] += 1

    def run():
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(one, range(total)))
        traffic["running"] = False
        cluster.log("Flash sale finished", "change")

    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--nodes", default="A,B,C", help="local nodes to spawn at start")
    ap.add_argument("--attach", action="append", default=[],
                    help="use an already-running remote node, e.g. D=192.168.1.20:50054")
    ap.add_argument("--quiet", action="store_true", help="do not print every hit, miss and SQL statement")
    args = ap.parse_args()
    db_module.VERBOSE = not args.quiet
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    print("Backend log: HIT / MISS / BYPASS per request, SQL per database query, EVENT for cluster changes\n", flush=True)
    cluster.start(args.nodes.split(",") if args.nodes else [], args.attach)
    print(f"FlashMart running at http://localhost:{args.port}/", flush=True)
    app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False)
