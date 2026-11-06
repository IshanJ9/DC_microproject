"""Coordinator-side cluster manager: membership, routing, replication, failover."""
import atexit
import json
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import grpc

import cache_pb2 as pb
import cache_pb2_grpc as rpc
from db import echo
from hashring import RING_SIZE, HashRing, angle_of, hash_details, hash_key, modulo_owner

BASE_PORT = 50051
ROOT = Path(__file__).parent
CHANNEL_OPTS = [
    ("grpc.initial_reconnect_backoff_ms", 200),
    ("grpc.max_reconnect_backoff_ms", 500),
]


def key_of(pid: int) -> str:
    return f"product:{pid}"


class Member:
    def __init__(self, name, host, port, spawned):
        self.name, self.host, self.port = name, host, port
        self.spawned = spawned
        self.proc: subprocess.Popen | None = None
        self.status = "down"  # up | suspect | down
        self.in_ring = False
        self.missed = 0
        self.channel = None
        self.stub = None

    def connect(self):
        if self.channel:
            self.channel.close()
        self.channel = grpc.insecure_channel(f"{self.host}:{self.port}", options=CHANNEL_OPTS)
        self.stub = rpc.CacheNodeStub(self.channel)

    def alive_process(self):
        return self.proc is not None and self.proc.poll() is None


class Cluster:
    def __init__(self, database, catalog, vnodes=40, rf=2, ttl=300):
        self.db = database
        self.catalog = catalog
        self.ring = HashRing(vnodes)
        self.rf = rf
        self.ttl = ttl
        self.members: dict[str, Member] = {}
        self.cache_enabled = True
        self.lock = threading.RLock()
        self.events = deque(maxlen=60)
        self.last_change: dict | None = None
        self._stop = threading.Event()
        self._reset_counters()
        atexit.register(self.shutdown)

    def _reset_counters(self):
        self.hits = self.misses = self.failovers = 0
        self.latencies = deque(maxlen=300)
        self.recent = deque(maxlen=12)
        self.requests = 0

    def log(self, msg, kind="info"):
        t = time.strftime("%H:%M:%S")
        self.events.appendleft({"t": t, "msg": msg, "kind": kind})
        echo(f"{t}  EVENT  {msg}")

    def start(self, names, attach=None):
        for spec in attach or []:
            name, addr = spec.split("=")
            host, port = addr.rsplit(":", 1)
            m = Member(name, host, int(port), spawned=False)
            self.members[name] = m
            self._join_quietly(m)
        for name in names:
            if name not in self.members:
                m = self._new_member(name)
                self._spawn(m)
                self._join_quietly(m)
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        self.log(f"Cluster started with servers {', '.join(sorted(self.members))}")

    def shutdown(self):
        self._stop.set()
        for m in self.members.values():
            if m.alive_process():
                m.proc.kill()

    def _new_member(self, name):
        m = Member(name, "127.0.0.1", BASE_PORT + ord(name) - ord("A"), spawned=True)
        self.members[name] = m
        return m

    def _spawn(self, m: Member):
        m.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "node.py"), "--name", m.name, "--port", str(m.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        m.connect()
        self._wait_ready(m)

    def _wait_ready(self, m, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if self._ping(m):
                return
            time.sleep(0.1)
        raise RuntimeError(f"node {m.name} did not start on port {m.port}")

    def _ping(self, m, timeout=0.4):
        try:
            if m.stub is None:
                m.connect()
            m.stub.Ping(pb.Empty(), timeout=timeout)
            return True
        except grpc.RpcError:
            return False

    def _join_quietly(self, m):
        if self._ping(m):
            m.status, m.missed = "up", 0
            m.in_ring = True
            self.ring.add_node(m.name)

    # ---- snapshots used to measure how many keys a membership change moves ----

    def _owners(self):
        ring = {pid: self.ring.owner(key_of(pid)) for pid in self.catalog}
        names = self.ring.nodes
        mod = {pid: modulo_owner(key_of(pid), names) for pid in self.catalog}
        return ring, mod

    def _membership_change(self, description, mutate):
        with self.lock:
            before_ring, before_mod = self._owners()
            mutate()
            copies, deletes = self._rebalance()
            after_ring, after_mod = self._owners()
            total = len(self.catalog)
            ring_ids = [p for p in self.catalog if before_ring[p] != after_ring[p]]
            mod_ids = [p for p in self.catalog if before_mod[p] != after_mod[p]]
            self.last_change = {
                "event": description,
                "total": total,
                "ring_moved": len(ring_ids),
                "modulo_moved": len(mod_ids),
                "ring_ids": ring_ids,
                "modulo_ids": mod_ids,
                "copied": copies,
                "removed": deletes,
                "at": time.time(),
            }
            lc = self.last_change
            self.log(
                f"{description}: consistent hashing moved {lc['ring_moved']}/{total} keys "
                f"(plain hash % N would move {lc['modulo_moved']}/{total})", "change")

    # ---- membership operations ----

    def add_node(self, name=None):
        with self.lock:
            if name is None:
                revivable = [n for n, mm in self.members.items() if mm.status == "down" and mm.spawned]
                free = [chr(65 + i) for i in range(8) if chr(65 + i) not in self.members]
                if not revivable and not free:
                    raise ValueError("cluster is full")
                name = (revivable + free)[0]
            m = self.members.get(name) or self._new_member(name)
            if m.in_ring and m.status == "up":
                raise ValueError(f"Server {name} is already in the ring")
            if m.spawned and not m.alive_process():
                self._spawn(m)
            elif not self._ping(m):
                raise ValueError(f"Server {name} is unreachable")
            m.status, m.missed = "up", 0

            def mutate():
                m.in_ring = True
                self.ring.add_node(name)

            self._membership_change(f"Server {name} joined", mutate)
            return name

    def kill_node(self, name):
        with self.lock:
            m = self.members.get(name)
            if not m or not m.alive_process():
                raise ValueError(f"Server {name} is not a running local process")
            m.proc.kill()
            self.log(f"Server {name} process killed", "fault")

    def _mark_down(self, m):
        def mutate():
            m.status, m.in_ring = "down", False
            self.ring.remove_node(m.name)

        self._membership_change(f"Server {m.name} declared down", mutate)

    # ---- heartbeat failure detector ----

    def _heartbeat_loop(self):
        while not self._stop.wait(0.7):
            for m in list(self.members.values()):
                ok = self._ping(m)
                with self.lock:
                    if ok:
                        m.missed = 0
                        if m.in_ring:
                            m.status = "up"
                    elif m.in_ring:
                        m.missed += 1
                        if m.missed >= 2:
                            self._mark_down(m)
                        else:
                            m.status = "suspect"
                            self.log(f"Server {m.name} missed a heartbeat", "fault")

    # ---- rebalancing: make every key live on exactly its preference list ----

    def _rebalance(self):
        live = [m for m in self.members.values() if m.status != "down" and m.stub]
        held: dict[str, set[str]] = {}
        for m in live:
            try:
                held[m.name] = set(m.stub.Keys(pb.Empty(), timeout=1).keys)
            except grpc.RpcError:
                held[m.name] = set()
        copies = deletes = 0
        all_keys = set().union(*held.values()) if held else set()
        for key in all_keys:
            desired = self.ring.preference_list(key, self.rf)
            holders = [n for n, ks in held.items() if key in ks]
            for node in desired:
                if key in held.get(node, ()):
                    continue
                value = self._peek(holders, key)
                if value is not None:
                    try:
                        self.members[node].stub.Put(
                            pb.PutRequest(key=key, value=value, ttl_seconds=self.ttl), timeout=1)
                        copies += 1
                    except grpc.RpcError:
                        pass
            for node in holders:
                if node not in desired:
                    try:
                        self.members[node].stub.Delete(pb.DeleteRequest(key=key), timeout=1)
                        deletes += 1
                    except grpc.RpcError:
                        pass
        return copies, deletes

    def _peek(self, holders, key):
        for n in holders:
            try:
                r = self.members[n].stub.Get(pb.GetRequest(key=key, internal=True), timeout=1)
                if r.found:
                    return r.value
            except grpc.RpcError:
                continue
        return None

    # ---- data path ----

    def fetch_product(self, pid: int, trace: list | None = None) -> dict:
        """Read-through lookup. If `trace` is a list, each step is appended for display."""
        ev = trace.append if trace is not None else (lambda e: None)
        start = time.perf_counter()
        key = key_of(pid)
        source, node, tried = "database", None, []
        if self.cache_enabled:
            with self.lock:
                prefs = self.ring.preference_list(key, self.rf)
            for idx, name in enumerate(prefs):
                m = self.members[name]
                try:
                    r = m.stub.Get(pb.GetRequest(key=key), timeout=0.3)
                except grpc.RpcError:
                    tried.append(name)
                    ev({"type": "probe", "node": name, "result": "down"})
                    m.status = "suspect" if m.status == "up" else m.status
                    continue
                if r.found:
                    ev({"type": "probe", "node": name, "result": "hit"})
                    product = json.loads(r.value)
                    source, node = "cache", name
                    failover = bool(tried) or idx > 0
                    break
                ev({"type": "probe", "node": name, "result": "miss"})
            else:
                product = None
            if product is None:
                product, entry = self.db.query(pid, why=f"cache miss for {key}")
                ev({"type": "db", "sql": entry["sql"], "ms": entry["ms"]})
                stored = []
                for name in prefs:
                    try:
                        self.members[name].stub.Put(
                            pb.PutRequest(key=key, value=json.dumps(product), ttl_seconds=self.ttl),
                            timeout=0.5)
                        stored.append(name)
                    except grpc.RpcError:
                        pass
                ev({"type": "store", "nodes": stored})
                failover = False
        else:
            product, entry = self.db.query(pid, why="cache is switched off")
            ev({"type": "db", "sql": entry["sql"], "ms": entry["ms"]})
            failover = False
        ms = (time.perf_counter() - start) * 1000
        result = "hit" if source == "cache" else ("miss" if self.cache_enabled else "bypass")
        self.db.log_access(key, result, node, ms, failover)
        with self.lock:
            self.requests += 1
            if source == "cache":
                self.hits += 1
                self.failovers += failover
            else:
                self.misses += 1
            self.latencies.append(ms)
            self.recent.append({"id": pid, "source": source, "node": node, "t": time.time()})
        return {"product": product, "source": source, "node": node,
                "ms": round(ms, 1), "failover": failover, "skipped": tried}

    # ---- dashboard state ----

    def reset(self):
        with self.lock:
            for m in self.members.values():
                if m.status != "down":
                    try:
                        m.stub.Clear(pb.Empty(), timeout=1)
                    except grpc.RpcError:
                        pass
            self.db.reset()
            self._reset_counters()
            self.last_change = None
            self.log("Caches cleared and counters reset")

    def set_vnodes(self, n):
        self._membership_change(f"Virtual nodes set to {n}", lambda: self.ring.set_vnodes(n))

    def state(self):
        with self.lock:
            names = list(self.members)
            ring_nodes = self.ring.nodes
            members = {n: self.members[n] for n in names}
            prefs = {pid: self.ring.preference_list(key_of(pid), self.rf) for pid in self.catalog}
            owners = {pid: (p[0] if p else None) for pid, p in prefs.items()}
        held, stats = {}, {}
        for n, m in members.items():
            if m.status == "down":
                continue
            try:
                held[n] = set(m.stub.Keys(pb.Empty(), timeout=0.3).keys)
                s = m.stub.Stats(pb.Empty(), timeout=0.3)
                stats[n] = {"size": s.size, "capacity": s.capacity, "hits": s.hits,
                            "misses": s.misses, "evictions": s.evictions}
            except grpc.RpcError:
                pass
        share = {n: 0 for n in names}
        for o in owners.values():
            if o:
                share[o] += 1
        keys = [{"id": pid, "name": self.catalog[pid]["name"],
                 "angle": round(360 * self.ring_pos(key_of(pid)), 2),
                 "owner": owners[pid], "replicas": prefs[pid][1:],
                 "holders": sorted(n for n, ks in held.items() if key_of(pid) in ks),
                 "cached": bool(owners[pid]) and key_of(pid) in held.get(owners[pid], ())}
                for pid in self.catalog]
        change = self.last_change and dict(self.last_change, age_s=round(time.time() - self.last_change["at"], 1))
        now = time.time()
        recent = [{"id": r["id"], "source": r["source"], "node": r["node"],
                   "age_ms": int((now - r["t"]) * 1000)} for r in list(self.recent)
                  if now - r["t"] < 2]
        lat = sorted(self.latencies)
        total = self.hits + self.misses
        return {
            "cache_enabled": self.cache_enabled,
            "vnodes": self.ring.vnodes, "rf": self.rf,
            "nodes": [{"name": n, "port": m.port, "status": m.status, "in_ring": m.in_ring,
                       "owns": share[n], "stats": stats.get(n),
                       "local": m.spawned} for n, m in members.items()],
            "ticks": [{"angle": round(360 * p / RING_SIZE, 2), "node": o} for p, o in self.ring.points()],
            "keys": keys,
            "recent": recent,
            "metrics": {"requests": self.requests, "hits": self.hits, "misses": self.misses,
                        "failovers": self.failovers,
                        "hit_ratio": round(100 * self.hits / total, 1) if total else 0,
                        "avg_ms": round(sum(lat) / len(lat), 1) if lat else 0,
                        "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 1) if lat else 0,
                        "db_queries": self.db.queries},
            "last_change": change,
            "events": list(self.events)[:12],
        }

    # ---- hashing lab: look up, trace, preview ----

    def locate(self, key: str) -> dict:
        """Where a key lands on the ring. Read-only: touches no cache."""
        with self.lock:
            walk = self.ring.walk(key, self.rf)
            points = len(self.ring.points())
        d = hash_details(key)
        return {"key": key, "angle": d["angle"], "hash": d, "walk": walk,
                "nodes": [h["node"] for h in walk if h["new"]],
                "owner_angle": walk[0]["angle"] if walk else None,
                "vnode_count": points, "vnodes": self.ring.vnodes, "servers": len(self.ring.nodes)}

    def trace(self, key: str) -> dict:
        """locate() plus a real read-through, so the cache and database steps are real."""
        out = self.locate(key)
        out["events"], out["product"] = [], None
        if key.startswith("product:") and key[8:].isdigit() and int(key[8:]) in self.catalog:
            res = self.fetch_product(int(key[8:]), trace=out["events"])
            out.update(product=res["product"], source=res["source"], ms=res["ms"])
        return out

    def _delete_everywhere(self, key):
        removed = []
        for m in self.members.values():
            if m.status == "down" or not m.stub:
                continue
            try:
                if m.stub.Delete(pb.DeleteRequest(key=key), timeout=0.5).existed:
                    removed.append(m.name)
            except grpc.RpcError:
                pass
        return removed

    def evict(self, key: str) -> list[str]:
        removed = self._delete_everywhere(key)
        self.log(f"{key} evicted from the cache" + (f" (was on {', '.join(removed)})" if removed else ""))
        return removed

    def update_price(self, pid: int, price: float) -> dict:
        """Write path: update the database, then delete every cached copy (cache-aside)."""
        entry = self.db.update_price(pid, price, why="price edited in the Database tab")
        removed = self._delete_everywhere(key_of(pid))
        self.log(f"Price of product {pid} changed in the database; cached copies on "
                 f"{', '.join(removed) or 'no server'} deleted", "change")
        return {"sql": entry["sql"], "ms": entry["ms"], "invalidated": removed}

    def simulate(self, action: str, name: str | None = None) -> dict:
        """Preview a membership change without touching the cluster."""
        with self.lock:
            before = self.ring.copy()
        names = before.nodes
        after = before.copy()
        if action == "add":
            name = name or next(chr(65 + i) for i in range(8) if chr(65 + i) not in self.members)
            after.add_node(name)
        elif action == "remove":
            if name not in names:
                raise ValueError(f"Server {name} is not in the ring")
            if len(names) == 1:
                raise ValueError("Cannot remove the last server")
            after.remove_node(name)
        else:
            raise ValueError("unknown action")
        after_names = after.nodes
        moves, mod_ids, owners_after = [], [], {}
        for pid in self.catalog:
            k = key_of(pid)
            a, b = before.owner(k), after.owner(k)
            owners_after[pid] = b
            if a != b:
                moves.append({"id": pid, "from": a, "to": b})
            if modulo_owner(k, names) != modulo_owner(k, after_names):
                mod_ids.append(pid)
        return {"action": action, "node": name, "total": len(self.catalog), "moves": moves,
                "modulo_ids": mod_ids, "owners_after": owners_after,
                "ticks_after": [{"angle": angle_of(p), "node": o} for p, o in after.points()],
                "nodes_before": names, "nodes_after": after_names}

    @staticmethod
    def ring_pos(key):
        return hash_key(key) / RING_SIZE
