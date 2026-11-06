"""A single cache node: an in-memory LRU/TTL store exposed over gRPC."""
import argparse
import threading
import time
from collections import OrderedDict
from concurrent import futures

import grpc

import cache_pb2 as pb
import cache_pb2_grpc as rpc


class Store:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._data: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = self.evictions = 0

    def get(self, key, internal=False):
        with self._lock:
            item = self._data.get(key)
            if item is None or (item[1] and item[1] < time.time()):
                self._data.pop(key, None)
                if not internal:
                    self.misses += 1
                return None
            if not internal:
                self._data.move_to_end(key)
                self.hits += 1
            return item[0]

    def put(self, key, value, ttl):
        with self._lock:
            self._data[key] = (value, time.time() + ttl if ttl else 0.0)
            self._data.move_to_end(key)
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)
                self.evictions += 1

    def delete(self, key):
        with self._lock:
            return self._data.pop(key, None) is not None

    def keys(self):
        now = time.time()
        with self._lock:
            return [k for k, (_, exp) in self._data.items() if not exp or exp >= now]

    def clear(self):
        with self._lock:
            self._data.clear()
            self.hits = self.misses = self.evictions = 0

    def size(self):
        with self._lock:
            return len(self._data)


class CacheNodeService(rpc.CacheNodeServicer):
    def __init__(self, name: str, capacity: int):
        self.name = name
        self.store = Store(capacity)
        self.started = time.time()

    def Get(self, request, context):
        value = self.store.get(request.key, request.internal)
        return pb.GetReply(found=value is not None, value=value or "")

    def Put(self, request, context):
        self.store.put(request.key, request.value, request.ttl_seconds)
        return pb.PutReply(ok=True)

    def Delete(self, request, context):
        return pb.DeleteReply(existed=self.store.delete(request.key))

    def Keys(self, request, context):
        return pb.KeysReply(keys=self.store.keys())

    def Ping(self, request, context):
        return pb.PingReply(name=self.name, uptime_ms=int((time.time() - self.started) * 1000))

    def Stats(self, request, context):
        s = self.store
        return pb.StatsReply(
            name=self.name, size=s.size(), capacity=s.capacity,
            hits=s.hits, misses=s.misses, evictions=s.evictions,
        )

    def Clear(self, request, context):
        self.store.clear()
        return pb.Empty()


def serve(name: str, host: str, port: int, capacity: int):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    rpc.add_CacheNodeServicer_to_server(CacheNodeService(name, capacity), server)
    server.add_insecure_port(f"{host}:{port}")
    server.start()
    print(f"[node {name}] listening on {host}:{port} (capacity {capacity})", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run one cache node")
    ap.add_argument("--name", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--capacity", type=int, default=150, help="max keys before LRU eviction")
    args = ap.parse_args()
    serve(args.name, args.host, args.port, args.capacity)
