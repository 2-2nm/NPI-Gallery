"""Pure layout maths for the justified gallery grid (no Qt dependency)."""


def justify_rows(
    aspects: list[float], width: int, target: float, gap: int
) -> list[tuple[int, list[tuple[int, int, int]]]]:
    """Split items into rows of equal height that exactly fill `width`.

    Returns (row_height, [(index, x, width), ...]) per row. The last row keeps the
    target height instead of being stretched.
    """
    rows: list[tuple[int, list[tuple[int, int, int]]]] = []
    count = len(aspects)
    start = 0
    while start < count:
        total = 0.0
        end = count
        height = float(target)
        for index in range(start, count):
            total += aspects[index]
            size = index - start + 1
            gaps = gap * (size - 1)
            if total * target + gaps < width:
                continue
            fitted = (width - gaps) / total
            end = index + 1
            height = fitted
            if size >= 2:
                shorter = (width - gap * (size - 2)) / (total - aspects[index])
                if abs(shorter - target) < abs(fitted - target):
                    end = index
                    height = shorter
            break
        tiles = []
        cursor = 0.0
        for offset, index in enumerate(range(start, end)):
            left = round(cursor + gap * offset)
            cursor += aspects[index] * height
            right = round(cursor + gap * offset)
            tiles.append((index, left, max(1, right - left)))
        rows.append((max(1, round(height)), tiles))
        start = end
    return rows


def square_rows(
    count: int, width: int, target: float, gap: int
) -> list[tuple[int, list[tuple[int, int, int]]]]:
    """Grid of equal squares that exactly fill `width`; the side is as close to `target` as fits."""
    if count <= 0:
        return []
    columns = max(1, round((width + gap) / (target + gap)))
    side = (width - gap * (columns - 1)) / columns
    rows: list[tuple[int, list[tuple[int, int, int]]]] = []
    for start in range(0, count, columns):
        tiles = []
        for offset, index in enumerate(range(start, min(count, start + columns))):
            left = round(offset * (side + gap))
            right = round(offset * (side + gap) + side)
            tiles.append((index, left, max(1, right - left)))
        rows.append((max(1, round(side)), tiles))
    return rows
