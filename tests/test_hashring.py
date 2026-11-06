from collections import Counter

from hashring import HashRing, modulo_owner

KEYS = [f"product:{i}" for i in range(2000)]


def make_ring(names, vnodes=100):
    ring = HashRing(vnodes)
    for n in names:
        ring.add_node(n)
    return ring


def test_owner_is_deterministic():
    a, b = make_ring("ABC"), make_ring("CBA")
    assert all(a.owner(k) == b.owner(k) for k in KEYS)


def test_preference_list_distinct_and_starts_with_owner():
    ring = make_ring("ABCD")
    for k in KEYS[:200]:
        prefs = ring.preference_list(k, 3)
        assert len(prefs) == len(set(prefs)) == 3
        assert prefs[0] == ring.owner(k)


def test_replica_count_capped_by_node_count():
    ring = make_ring("AB")
    assert len(ring.preference_list("x", 5)) == 2
    assert HashRing().preference_list("x", 2) == []


def test_adding_node_moves_only_a_fraction_of_keys():
    ring = make_ring("ABC")
    before = {k: ring.owner(k) for k in KEYS}
    ring.add_node("D")
    moved = [k for k in KEYS if ring.owner(k) != before[k]]
    assert all(ring.owner(k) == "D" for k in moved)
    assert 0.15 < len(moved) / len(KEYS) < 0.35  # ideal is 1/4


def test_modulo_hashing_moves_most_keys():
    before = {k: modulo_owner(k, list("ABC")) for k in KEYS}
    moved = [k for k in KEYS if modulo_owner(k, list("ABCD")) != before[k]]
    assert len(moved) / len(KEYS) > 0.6


def test_removing_node_only_moves_its_keys():
    ring = make_ring("ABCD")
    before = {k: ring.owner(k) for k in KEYS}
    ring.remove_node("B")
    for k in KEYS:
        if before[k] != "B":
            assert ring.owner(k) == before[k]


def test_more_vnodes_balances_load():
    def spread(vnodes):
        counts = Counter(make_ring("ABC", vnodes).owner(k) for k in KEYS)
        return max(counts.values()) - min(counts.values())

    assert spread(200) < spread(1)


def test_walk_stops_at_distinct_servers_and_labels_vnodes():
    ring = make_ring("ABC", 40)
    hops = ring.walk("product:42", 2)
    assert hops[0]["new"] and hops[-1]["new"]
    assert len({h["node"] for h in hops}) == 2
    assert hops[0]["label"].startswith(hops[0]["node"] + "#")
    assert [h["node"] for h in hops if h["new"]] == ring.preference_list("product:42", 2)


def test_hash_details_matches_ring_position():
    from hashring import hash_details, hash_key, RING_SIZE
    d = hash_details("product:42")
    assert d["number"] == hash_key("product:42")
    assert abs(d["fraction"] - d["number"] / RING_SIZE) < 1e-12
    assert 0 <= d["angle"] < 360


def test_copy_is_independent():
    ring = make_ring("ABC")
    clone = ring.copy()
    clone.add_node("D")
    assert ring.nodes == ["A", "B", "C"] and clone.nodes == ["A", "B", "C", "D"]
