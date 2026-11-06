# FlashMart explained from scratch

This file explains what the project is, what is happening on screen, and exactly how to run the demo. It assumes you know nothing about caching or hashing yet.

---

## 1. The project in one minute

**Topic:** Distributed Cache with Consistent Hashing.

**The story:** FlashMart is an online shop running a flash sale. Thousands of people open the same product pages at the same time. The shop's **database is slow** (about 180 ms per lookup, and slower when many people ask at once), so it would struggle.

**The fix:** put a **cache** in front of the database. A cache is fast memory that remembers answers. The first person to open a product waits for the database. The next thousand people get the answer from the cache in about 1 ms.

**Why "distributed":** one cache server is a risk. If it crashes, every request falls back to the slow database and the shop collapses. So we run **several cache servers** (A, B, C, and more) that share the work. This is the distributed computing part, and it raises three questions the project answers:

| Question | Answer used in this project |
| --- | --- |
| Which server stores which product? | **Consistent hashing** (a "ring") |
| What if a server dies? | **Replication**: every product is stored on two servers |
| What if we add or remove a server? | **Rebalancing**: only a small share of products move |

The servers are separate programs (processes) that talk to the coordinator over **gRPC**, which is a remote procedure call (RPC) system. That is the "RPC" part of your subject.

---

## 2. The key ideas, in plain words

### 2.1 What is a cache hit and a miss?
- **Hit:** the product is already in the cache. Fast (about 1 ms).
- **Miss:** it is not there. The coordinator asks the database (about 180 ms), returns the answer, and also saves a copy in the cache so the next request is a hit.

**Hit ratio** = hits divided by all requests. Higher is better.

### 2.2 Why not just use `hash(key) % number_of_servers`?
The obvious way to pick a server is: turn the product name into a number, divide by the number of servers, and use the remainder.

With 3 servers, `number % 3` gives 0, 1 or 2. That works until you add a fourth server. Now the formula is `number % 4`, and almost every product maps to a **different** server than before. About **three quarters of the cache becomes useless at once**, and every request falls through to the slow database. This is called a cache stampede.

### 2.3 Consistent hashing (the ring)
Imagine a clock face, but a full circle of 360 degrees.

1. Each **server** is placed at points on the circle.
2. Each **product** is also hashed to a point on the circle.
3. To find a product's server, start at its point and walk **clockwise** until you hit a server. That server is the **owner**.

Now when a server is added, it takes over only the stretch of circle just before it. Products elsewhere do not move. Adding a fourth server moves about **one quarter** of the products (1 divided by 4), not three quarters.

### 2.4 Virtual nodes (vnodes)
If each server sat at only one point, the circle could be cut unevenly, so one server would own far more products than the others. To fix that, each server is placed at **many points** (40 by default). These are "virtual nodes". More points give a more even split.

On the ring picture, the coloured **ticks on the outer edge** are the virtual nodes. The **dots inside** are the 200 products.

### 2.5 Replication (spare copies)
Each product is stored on **two** servers: the owner and the **next different server clockwise**. If the owner dies, the next server already has the data.

### 2.6 Failure detection (heartbeats)
Every 0.7 seconds the coordinator sends a tiny "are you alive?" message (Ping) to each server. Two missed replies in a row mean the server is declared **down**:
1. The first miss shows the server as **suspect** ("not answering").
2. The second miss removes it from the ring and triggers rebalancing.

### 2.7 Failover reads (the magic moment)
Between the server dying and the coordinator noticing (about 2 seconds), customers are still shopping. When a read to the owner fails, the coordinator **immediately tries the replica**. The customer never sees an error. The **Failover reads** counter shows how often this happened.

### 2.8 Rebalancing
When the set of servers changes (join, or declared down), the coordinator makes sure every product is stored on the right two servers. It copies what is missing and deletes what no longer belongs. The panel "How many keys moved" measures how much moved.

### 2.9 What a cache does not do
A cache holds **copies**. The database (a real SQLite table here) is the real source of truth. If a cache server is killed, its data is lost, but nothing is permanently lost. The next request just reloads from the database.

---

## 3. The architecture

```
 Your browser  (Learn / Shop / Control room / Split view)
        |
        |  HTTP
        v
 Coordinator  (app.py + cluster.py, one Python program)
   - holds the hash ring
   - decides which servers handle each product
   - sends heartbeats
   - copies data around when servers join or leave
        |
        |  gRPC calls: Get, Put, Delete, Keys, Ping, Stats, Clear
        v
 Cache server A     Cache server B     Cache server C     (D, E ... added live)
 (own process,      (own process,      (own process,
  port 50051)        port 50052)        port 50053)

 On a cache miss the coordinator runs a SELECT on the SQLite database (db.py), then
 stores the result on the two servers that own that key.
```

Each cache server is a **real separate operating-system process** with its own memory. They do not share anything. They only talk through gRPC. That is what makes it genuinely distributed even on one laptop.

### File guide

| File | What it does |
| --- | --- |
| `app.py` | The web server. Serves the pages and the API the page calls. |
| `cluster.py` | The brain. Ring, routing, replication, heartbeats, failover, rebalancing. |
| `hashring.py` | The consistent hash ring itself (also the plain `% N` method, for comparison). |
| `node.py` | One cache server. Stores data in memory, with LRU eviction and expiry (TTL). |
| `cache.proto` | The gRPC contract: the list of calls a cache server understands. |
| `cache_pb2.py`, `cache_pb2_grpc.py` | Generated from `cache.proto`. Do not edit. |
| `db.py` | The real database: a SQLite file (`flashmart.db`) with a `products` table of 200 rows. Each query also waits about 180 ms to imitate a database on another machine, and every SQL statement is logged. |
| `templates/index.html`, `static/app.css`, `static/app.js` | The web page: navigation, ring drawing, Learn, Shop, Control room. |
| `static/lab.js` | The Hashing lab and Database tabs. |
| `show_db.py` | Opens `flashmart.db` from outside the app and prints the tables, hit/miss totals and latest requests (`--tail` follows live). |
| `tests/test_hashring.py` | Tests proving the ring behaves correctly. |

---

## 4. What happens when you click a product (step by step)

Example: you open "Aero Sneakers" in the Shop tab.

1. The browser calls `GET /api/product/0`.
2. The coordinator turns the key `product:0` into a number and finds its point on the ring.
3. It walks clockwise to get the **owner** and the **replica** (for example A, then C).
4. It asks the owner, over gRPC, "do you have `product:0`?"
   - **Yes (hit):** it returns the data. About 1 ms. The Shop panel says "Served from cache".
   - **No (miss):** it queries the database (about 180 ms), sends the data back to you, and also stores a copy on **both** A and C.
   - **Owner does not answer:** it asks the replica instead (**failover**).
5. The panel "Last page load" shows each hop and the time taken.

---

## 5. Reading the screens

### Learn tab
- **The dark ring:** the live cluster. Coloured **ticks** are virtual nodes, coloured **dots** are the 200 products. A dot's colour is the server that owns it.
- **"Where does a key go?"** Type any text, press **Find its server**. A white dot marks where that key lands, and a white arc shows the clockwise walk to the owner. You are watching the algorithm run.
- **Four steps below:** the same story as section 1, as pictures.

### Shop tab
- **Product cards:** each has an **Open product** button. After opening, the card says "From cache" or "From database" and the time.
- **Last page load panel:** the path of the last request, big time in ms, and a note if a replica had to answer.
- **Start auto-shopper:** opens random products every 0.45 s, with popular items more likely. It simulates a crowd.

### Control room tab
- **The ring:**
  - **Filled dot** = that product is cached on its owner. **Hollow dot** = not cached yet.
  - **White pulse** = a request for that product just happened.
- **Four tiles:**
  - **Cache hit ratio:** share of requests answered without the database.
  - **Average response:** mean time per request. The caption gives the 95th percentile (95% of requests were faster than this).
  - **Database queries:** how many times the slow database was used.
  - **Failover reads:** how many reads were saved by a replica because the owner was down.
- **Run the demo:** the five-step guided tour (see section 6).
- **Servers:** one card per server with status (healthy, not answering, down), how many products it owns, how full it is (items stored out of 150), and hits/misses.
- **How many keys moved:** after a server joins or leaves, a green bar (this project) against a red bar (what plain `% N` would have caused), plus two grids of 200 squares showing which products moved. On the ring, white rings mark the keys that moved. **Green much shorter than red is the proof the idea works.**
- **Manual controls:** extras: run traffic yourself, switch the cache off, change virtual nodes, add or kill servers, clear caches.
- **What just happened:** a plain-language event log.

### Hashing lab tab (this is where consistent hashing is shown)
- **The ring has a coloured band.** Each stretch of the circle is painted in the colour of the server that owns it. A key owned by Server B is simply a key whose position falls inside one of B's coloured stretches. Short ticks outside the band are the virtual nodes.
- **Follow one key.** Type a key (for example `product:42`) and press **Start**. Press **Next step** to reveal each stage and watch the ring change:
  1. The key is just text.
  2. MD5 turns it into a number (you see the real MD5 value).
  3. That number becomes a position on the ring: number divided by 2^64, times 360 degrees. The white dot appears.
  4. Walk clockwise to the first virtual node, for example `C#7`. The white arc is the walk. That server is the **owner**.
  5. Keep walking past virtual nodes of the same server until a different server appears. That one holds the **replica** (dashed arc).
  6. The coordinator asks those servers' caches (hit or miss).
  7. On a miss: the exact SQL it runs on the database, and how long it took.
  8. The row is stored on both servers. Press **Start** again to see a hit.

  Use **Remove it from the cache** to force a miss again. A key that is not a product (like `user:7`) shows only the hashing steps.
- **What happens when a server joins or leaves?** Press **Preview** to see the effect **without changing the real cluster**. The ring switches between **Before** and **After**; white rings mark keys that change owner. Below, two grids of 200 squares (one per product) are painted for each method: consistent hashing (green, a few squares) against plain `hash % N` (red, most squares). The text lists where the moved keys go (for example "C to D: 15").
- **Key map.** All 200 keys sorted by ring position, with owner, replica and the servers that actually hold a copy right now. Click a row to follow that key.

### Database tab
- **The products table** is the real SQLite table. The "cached on" column shows which cache servers hold a copy of each row right now. Those letters are the link between the database and the ring.
- **Read** fetches that row through the cache. The note under "How a read works" says whether it came from the cache or from the database. The counters show reads answered by the cache against queries run on the database.
- **Edit** changes a price. This runs an `UPDATE` on the database, then **deletes the cached copies** so nobody keeps seeing the old price (this pattern is called cache-aside, or cache invalidation). Read the row again: it misses once, runs a `SELECT`, and then the new price is cached.
- **SQL the database has run** is a live log of every statement, with its time and the reason (for example "cache miss for product:2").

### Split view
Shop on the left, Control room on the right. Use this while presenting so the audience sees cause (click a product) and effect (ring and numbers) together.

---

## 6. The five tour steps explained

Open **Control room** (or Split view) and press the highlighted button in "Run the demo".

| Step | Button | What it really does | What you should see |
| --- | --- | --- | --- |
| 1. Fill the cache | Run flash sale | Sends 1000 requests from 24 simulated users. Popular products are requested far more often. | Hollow dots become solid. Hit ratio rises to about 75%. Average response drops. DB queries stay around 200-250 instead of 1000. |
| 2. See what the cache saves | Run without cache | Turns the cache off, sends 300 requests, turns it back on. Every request goes to the database. | Average response jumps to hundreds of ms. DB queries shoot up by 300. |
| 3. Add a server | Add server | Starts a new cache server process (D) and adds it to the ring. Moves the products it now owns. | Server D appears. A few dots change colour. "How many keys moved" shows roughly 30 of 200 (green) against roughly 150 of 200 (red). |
| 4. Kill a server | Kill busiest server | Kills the real OS process of the server that owns the most products. | Header chip goes "not answering" then "down". Failover reads count up. The ring redraws. A new "keys moved" result appears. The shop keeps working. |
| 5. Bring it back | Revive server | Restarts the killed server, empty, and puts it back on the ring. | The server returns to healthy. Products flow back to it. |

Press **Start over** to clear caches, revive any killed servers, and reset the tour before the real presentation.

The exact numbers vary slightly from run to run. The pattern (green far smaller than red, hit ratio around 75%) is the point.

---

## 7. How to run it

```bash
cd C:\Ishan\DC_microproject
.venv\Scripts\python app.py
```

Then open **http://localhost:8000/** in your browser. The program starts the web server and three cache servers (A, B, C) automatically.

To stop: press `Ctrl+C` in the terminal. The cache servers are stopped with it.

If it will not start because a port is busy, an old copy is still running. Close it in Task Manager (look for `python.exe`), then run again.

---

## 7b. Showing the database and every hit on the backend

The database is a real SQLite file, `flashmart.db`, in the project folder. It has three tables:

| Table | What is in it |
| --- | --- |
| `products` | The 200 shop rows (id, name, category, price, stock). This is the source of truth. |
| `access_log` | One row for every lookup: time, key, result (`hit`, `miss` or `bypass` when the cache is off), which server answered, milliseconds, and whether a replica had to answer. |
| `query_log` | One row for every SQL statement the application ran (`SELECT` on a miss, `UPDATE` on a price change), with its time. |

Three ways to show it, from the backend side:

**1. The terminal running `app.py` prints everything live.** Every request is one line:
```
10:58:50  MISS   product:7     database     188.5 ms
10:58:50  SQL    SELECT id, name, category, price, stock FROM products WHERE id = 7  (184 ms)
10:58:51  HIT    product:7     server C       0.7 ms
10:58:52  SQL    UPDATE products SET price = 55.50 WHERE id = 7  (180 ms)
10:58:52  EVENT  Price of product 7 changed in the database; cached copies on B, C deleted
```
Keep this terminal visible next to the browser. Start the app with `--quiet` to switch it off.

**2. `show_db.py` opens the database file itself, outside the app.** Run it in a second terminal:
```bash
.venv\Scripts\python show_db.py          # snapshot: tables, rows, hit/miss totals, hits per server, latest requests
.venv\Scripts\python show_db.py --tail   # live feed of new requests as they happen
```
This is the clearest proof that the data really lives in a database and not in the program's memory.

**3. The Database tab in the browser.** The panel "Every cache hit and miss" shows the same `access_log` rows, and "Ask the database yourself" is a read-only SQL box. The preset buttons run queries such as hits per server or the most requested products. Only `SELECT` is allowed.

Notes:
- The database is rebuilt from the seed data every time the app starts, so prices you edit and the logs reset on restart.
- You can also open `flashmart.db` in any SQLite viewer (for example DB Browser for SQLite) while the app runs.

---

## 8. The demo script (about 9 minutes; skip Part 7 and the optional extra to get closer to 7)

Rehearse once with the tour. Start with **Start over** pressed and Split view open.

### Part 1: The problem (about 40 seconds) - Learn tab
**Say:** "This is FlashMart, a shop in the middle of a flash sale. Its database takes about 180 ms per query and slows down under load. We put a cache in front of it. One cache server would be a single point of failure, so we built a distributed cache with several servers."
**Point at:** the ring. "Each colour is a cache server. Each dot is a product."

### Part 2: Showing consistent hashing (about 2 minutes) - Hashing lab tab
**Say:** "Consistent hashing means a key's server is decided by a position on a circle. I'll follow one key."
**Do:** with `product:42` in the box, press **Start**, then **Next step** repeatedly.
**Say at each step:** "We hash the key with MD5 and get this number. Divided by 2 to the 64 it gives a position of about 354 degrees. Walking clockwise, the first virtual node is C#7, so server C owns it. Walking on to the next different server gives B, which holds the replica."
**Do:** keep stepping: "Now it asks C and B. Both miss, so the database runs this SELECT. The row is stored on both servers." Press **Start** again: "Now it is a hit."
**Do:** type `user:7`, press Start: "Any key works the same way."
**Do:** in "What happens when a server joins or leaves?", choose **Add a server**, press **Preview**, then toggle **After**.
**Say:** "Adding a fourth server moves only 32 of 200 keys, the ones with white rings. The same change with hash modulo N moves 151. The red grid is almost full, the green one is nearly empty."

### Part 3: The database and the cache together (about 90 seconds) - Database tab, then Split view
**Do:** on the Database tab press **Read** on one row twice. First: "The cache did not have it, so the database ran this SELECT, shown in the log." Second: "Now the cache answered and no SQL ran."
**Do:** press **Edit** on that row, change the price, press **Save**. **Say:** "The UPDATE goes to the database, and the cached copies are deleted so no one sees a stale price. The next read goes back to the database and picks up the new price."
**Do:** switch to **Split view** and press **Start auto-shopper**. **Say:** "The pulses on the ring are live customer requests."

### Part 3b: Show the backend (about 60 seconds)
**Do:** put the terminal running `app.py` beside the browser. Read a row twice on the Database tab.
**Say:** "Here is the backend. The first read printed MISS and the SELECT that went to the database. The second printed HIT from server C, with no SQL."
**Do:** in a second terminal run `.venv\Scripts\python show_db.py`.
**Say:** "This opens the database file directly. 200 products, and every cache lookup is recorded: so many hits, so many misses, hits per server, and the exact SQL the application ran."
**Do:** optional, run `show_db.py --tail` while you run the flash sale to show requests arriving live.

### Part 4: Flash sale (about 45 seconds) - Control room
**Do:** in the **Control room** (or Split view), tour step 1, **Run flash sale**.
**Say:** "1000 requests from 24 users. Dots fill as products get cached. The hit ratio is about 75%, so the database only handled about a quarter of the load."
**Do:** tour step 2, **Run without cache**.
**Say:** "Without the cache, every request waits on the database. Response time is many times worse. That is the case for the cache."

### Part 5: Scaling out (about 60 seconds)
**Do:** tour step 3, **Add server**.
**Say:** "We add a fourth server while the shop is running. Look at the keys moved panel: consistent hashing moved about one in six products. The usual `hash % N` method would have moved about three in four. That would have emptied most of the cache and flooded the database."

### Part 6: Failure (about 90 seconds)
**Do:** tour step 4, **Kill busiest server**.
**Say:** "I just killed a real server process. The shop still works, because each product has a replica on another server. The failover counter shows reads rescued by replicas. After two missed heartbeats the coordinator declares the server down, removes it from the ring, and rebuilds the missing copies on the survivors."
**Do:** open a few products in the shop to prove it still serves.

### Part 7: Recovery and wrap-up (about 30 seconds)
**Do:** tour step 5, **Revive server**.
**Say:** "It comes back empty and rejoins the ring. To summarise: gRPC for communication, consistent hashing for placement, replication for fault tolerance, heartbeats for failure detection, and rebalancing that moves only what is needed."

### Optional extra: virtual nodes (about 30 seconds)
Open **Manual controls**, drag **Virtual nodes** to 1, then to 200. Watch the "Products owned" bars on the server cards: uneven at 1, even at 200.

---

## 9. Likely questions and short answers

**Is this really distributed if it runs on one laptop?**
Yes. Each cache server is a separate process with its own memory and its own network port. They share nothing and talk only over gRPC. The same code can run on different machines (see section 10).

**Why consistent hashing instead of `hash % N`?**
When N changes, `% N` remaps most keys and wipes out the cache. Consistent hashing remaps only about 1/N. The panel "How many keys moved" measures this on the live cluster.

**What are virtual nodes for?**
To balance load. With one point per server the ring is cut unevenly. With many points per server the share per server evens out.

**What happens when a server dies?**
Reads fail over to the replica immediately. After two missed heartbeats (about 1.5 to 2 seconds) the server is removed from the ring and the coordinator re-copies data so every key again has two copies.

**What happens to the data on the dead server?**
It is lost, but it was only a copy. The replica still has it, and the database holds the original.

**What if both the owner and the replica die at once?**
Those products become cache misses and are reloaded from the database, then cached on the new owners. Slower, but correct.

**How do you handle consistency?**
This is a read-through cache with a TTL (entries expire after 300 seconds). Writes go to both the owner and the replica. It is eventually consistent. It does not do strong consistency or quorum reads, which a cache does not need because the database is the source of truth.

**What is a limitation of your design?**
The coordinator is a single point of failure. In a production system you would run several coordinators (or let clients compute the ring themselves, as Memcached clients and Cassandra do). Failure detection uses simple heartbeats, not a consensus protocol.

**Is the database real?**
Yes. It is a SQLite file, `flashmart.db`, with a `products` table. Reads run `SELECT ... WHERE id = ?` and price edits run `UPDATE`. A short delay is added to each query to imitate a database on another machine, and every statement is shown in the Database tab.

**Show me the hits on the backend.**
Every lookup is a row in the `access_log` table and a line in the terminal running the app. `show_db.py` prints the totals, hits per server and latest requests straight from the database file, and `show_db.py --tail` follows them live.

**What happens when someone changes a price?**
The coordinator updates the database first, then deletes the cached copies on every server (cache-aside invalidation). The next read misses, reads the new price from the database and caches it.

**Where is the hash computed, and how?**
MD5 of the key text. The first 8 bytes are read as a 64-bit number, and that number divided by 2^64 gives the position on the ring (times 360 for degrees). The Hashing lab shows each value.

**What does LRU mean here?**
Each server holds at most 150 entries. When full, it removes the **Least Recently Used** one. Entries also expire after 300 seconds.

**Which RPC calls exist?**
`Get`, `Put`, `Delete`, `Keys`, `Ping`, `Stats`, `Clear`, defined in `cache.proto`.

**What do you measure?**
Hit ratio, average and 95th-percentile response time, database queries, failover reads, products per server, and keys moved on membership changes (consistent hashing against `% N`).

---

## 10. Optional: running on more than one computer

On the other computer (same Wi-Fi), start a server:

```bash
python node.py --name D --host 0.0.0.0 --port 50054
```

On the main laptop, start the app and point it at that server:

```bash
python app.py --attach D=<other-computer-IP>:50054
```

Stopping that process on the other computer triggers the same failure detection and failover. If the Wi-Fi at your venue is unreliable, skip this and demo on one laptop.

---

## 11. Troubleshooting

| Problem | Fix |
| --- | --- |
| Page will not load | Check the terminal for errors. Make sure you ran `app.py` from the project folder. |
| "port is already in use" | An old run is still alive. End `python.exe` in Task Manager and start again. |
| Numbers look odd after many tests | Press **Start over** in "Run the demo". |
| A server shows "down" and you want it back | Press **Revive server** (tour step 5), or **Add or revive server** under Manual controls. |
| Fonts look different | The page loads two fonts from the internet. Offline, it uses system fonts. Everything still works. |
| Tests | `.venv\Scripts\python -m pytest -q tests` should report 7 passed. |
