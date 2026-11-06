"""Consistent hash ring with virtual nodes."""
import bisect
import hashlib

RING_SIZE = 1 << 64


def hash_key(value: str) -> int:
    """First 8 bytes of the MD5 digest, read as a 64-bit number."""
    return int.from_bytes(hashlib.md5(value.encode()).digest()[:8], "big")


def hash_details(value: str) -> dict:
    """Every intermediate value of the hash, for showing the work on screen."""
    digest = hashlib.md5(value.encode()).hexdigest()
    number = int(digest[:16], 16)
    return {
        "md5": digest,
        "first8": digest[:16],
        "number": number,
        "fraction": number / RING_SIZE,
        "angle": round(360 * number / RING_SIZE, 2),
    }


def angle_of(point: int) -> float:
    return round(360 * point / RING_SIZE, 2)


class HashRing:
    def __init__(self, vnodes: int = 40):
        self.vnodes = vnodes
        self._nodes: set[str] = set()
        self._points: list[int] = []
        self._owner: dict[int, str] = {}
        self._label: dict[int, str] = {}

    @property
    def nodes(self) -> list[str]:
        return sorted(self._nodes)

    def copy(self) -> "HashRing":
        ring = HashRing(self.vnodes)
        for name in self._nodes:
            ring._nodes.add(name)
        ring._rebuild()
        return ring

    def add_node(self, name: str) -> None:
        if name in self._nodes:
            return
        self._nodes.add(name)
        self._rebuild()

    def remove_node(self, name: str) -> None:
        if name not in self._nodes:
            return
        self._nodes.discard(name)
        self._rebuild()

    def set_vnodes(self, vnodes: int) -> None:
        self.vnodes = vnodes
        self._rebuild()

    def _rebuild(self) -> None:
        self._owner, self._label = {}, {}
        for name in self._nodes:
            for i in range(self.vnodes):
                point = hash_key(f"{name}#{i}")
                self._owner[point] = name
                self._label[point] = f"{name}#{i}"
        self._points = sorted(self._owner)

    def points(self) -> list[tuple[int, str]]:
        return [(p, self._owner[p]) for p in self._points]

    def walk(self, key: str, distinct: int = 2) -> list[dict]:
        """Walk clockwise from the key's position, stopping once `distinct`
        different servers have been met. Each hop is one virtual node."""
        if not self._points:
            return []
        distinct = min(distinct, len(self._nodes))
        start = bisect.bisect_left(self._points, hash_key(key))
        seen: list[str] = []
        hops: list[dict] = []
        for step in range(len(self._points)):
            point = self._points[(start + step) % len(self._points)]
            node = self._owner[point]
            hops.append({"label": self._label[point], "node": node, "angle": angle_of(point),
                         "new": node not in seen})
            if node not in seen:
                seen.append(node)
                if len(seen) == distinct:
                    break
        return hops

    def preference_list(self, key: str, count: int = 2) -> list[str]:
        """Owner first, then the next distinct nodes clockwise (replicas)."""
        hops = self.walk(key, count)
        return [h["node"] for h in hops if h["new"]]

    def owner_point(self, key: str) -> int | None:
        """Ring position of the virtual node that owns the key."""
        if not self._points:
            return None
        return self._points[bisect.bisect_left(self._points, hash_key(key)) % len(self._points)]

    def owner(self, key: str) -> str | None:
        prefs = self.preference_list(key, 1)
        return prefs[0] if prefs else None


def modulo_owner(key: str, node_names: list[str]) -> str | None:
    """The naive alternative: hash(key) % N over a sorted node list."""
    if not node_names:
        return None
    names = sorted(node_names)
    return names[hash_key(key) % len(names)]
