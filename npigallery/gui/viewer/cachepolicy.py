"""Which decoded photos the viewer's cache gives up. No Qt imports, so it can be tested on its own."""

from collections.abc import Collection, Mapping


def eviction_order(
    sizes: Mapping[str, int],
    keep: Collection[str],
    max_items: int,
    max_bytes: int,
) -> list[str]:
    """Keys to drop so the cache fits `max_items` and `max_bytes`.

    `sizes` maps key -> bytes, least recently used first. Keys in `keep` (the photo on screen and the
    neighbours being prefetched) are never dropped, even when that leaves the cache over budget:
    evicting them would only make the viewer decode the same photo again straight away.
    """
    count = len(sizes)
    total = sum(sizes.values())
    dropped: list[str] = []
    for key, size in sizes.items():
        if count <= max_items and total <= max_bytes:
            break
        if key in keep:
            continue
        dropped.append(key)
        count -= 1
        total -= size
    return dropped