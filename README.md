# FlashMart: Distributed Cache with Consistent Hashing

A flash-sale shopping site whose product pages are served through a **distributed cache**.
Cache nodes are separate processes that talk over **gRPC**; keys are placed on a **consistent
hash ring** with virtual nodes and replicated to the next node clockwise, so the site keeps
working when a node dies.

```
 Browser (storefront + ops dashboard)
        |  HTTP
 Coordinator (app.py + cluster.py): hash ring, routing, replication, heartbeats
        |  gRPC: Get / Put / Delete / Keys / Ping / Stats / Clear
 Node A      Node B      Node C   (+ more added live)      each its own process and port
```

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Linux/macOS: .venv/bin/pip
.venv/Scripts/python app.py                        # starts the coordinator and nodes A, B, C
```

Open http://localhost:8000/. One page with six tabs:

- **Learn**: what the project is, a live "where does this key go?" ring, and a four-step explainer.
- **Hashing lab**: follow one key step by step (MD5, ring position, clockwise walk, owner, replica, cache, SQL), and preview a server joining or leaving next to `hash % N`.
- **Database**: the real SQLite `products` table, which cache servers hold each row, read and edit rows, and a live SQL log.
- **Shop**: the customer view, with a "last page load" panel showing the path each request took.
- **Control room**: the engineer view, with the live ring, metrics, a guided "Run the demo" tour, and manual controls.
- **Split view**: shop and control room side by side, for presenting.

If you edit `cache.proto`, regenerate stubs with
`python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cache.proto`.

## Demo script (about 5 minutes)

Start on **Learn** (about a minute), then use **Split view** and follow the "Run the demo" steps.

1. **The idea (Learn).** Type a key into "Where does a key go?" and show the white arc walking
   clockwise to the owning server. Read the four steps aloud.
2. **Cache effect (Shop).** Open one product twice: first `From database, ~190 ms`, then
   `From cache, ~1 ms`. Start the auto-shopper.
3. **Fill the cache (tour step 1).** Hollow dots turn solid and the hit ratio climbs to ~75%.
4. **Why the cache matters (step 2).** Runs traffic with the cache off: response time jumps.
5. **Scale out (step 3).** "How many keys moved" shows consistent hashing (green) against
   plain `hash % N` (red).
6. **Failure (step 4).** Kills the busiest server. The shop keeps serving, `Failover reads`
   counts up, the server goes `not answering` then `down`, and replicas are rebuilt.
7. **Recovery (step 5).** The server restarts empty and rejoins the ring.
8. **Virtual nodes (Manual controls).** Drag the slider from 1 to 200 and watch the
   "Products owned" bars even out.

## How it maps to distributed-computing concepts

| Concept | Where |
| --- | --- |
| RPC | `cache.proto`, `node.py` (server), `cluster.py` (client stubs) |
| Partitioning / consistent hashing | `hashring.py` (MD5 ring, virtual nodes) |
| Replication (RF=2) | `Cluster.fetch_product`, `Cluster._rebalance` |
| Failure detection | heartbeat loop in `Cluster._heartbeat_loop` (2 missed pings = down) |
| Fault tolerance / failover reads | `Cluster.fetch_product` tries the replica when the owner fails |
| Rebalancing on membership change | `Cluster._membership_change` + `_rebalance` |
| Eviction | LRU + TTL in `node.py` (`Store`), capacity 150 keys per node |

## Running on several machines (optional)

Start a node on another machine: `python node.py --name D --host 0.0.0.0 --port 50054`.
On the coordinator machine: `python app.py --attach D=<that-ip>:50054`.
Stopping that node's process triggers the same heartbeat detection and failover.

## Tests

```bash
.venv/Scripts/python -m pytest -q tests
```

## Inspecting the database from the backend

The terminal running `app.py` prints every HIT, MISS and SQL statement live (`--quiet` turns this off).
Every lookup is also stored in `flashmart.db` (tables `products`, `access_log`, `query_log`):

```bash
.venv/Scripts/python show_db.py          # snapshot
.venv/Scripts/python show_db.py --tail   # live feed of new requests
```

The tests cover ring determinism, replica selection, the "only ~1/N keys move" property,
the modulo-hashing contrast, and vnode load balancing.
