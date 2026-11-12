"""Suggest group colours from text embeddings, so that similar groups get similar colours.

Anki-free (no `aqt`/`anki` imports) and standard library only: Anki ships without numpy, and a group list is small.

The idea, in four steps:

1. `distance_matrix`: cosine distance between the groups' embeddings.
2. `tsp_tour`: the shortest closed tour through the groups, so that groups that are close in meaning are neighbours.
   A colour wheel is a closed loop, which is why a tour (not a path) is the right shape.
3. `suggest_hues`: walk the tour around the wheel with a hue step proportional to the distance to the next group
   (the step from the last group back to the first included), but never below `floor_deg`, so that two near-duplicate
   groups can not collapse into one colour. The wheel starts after the longest edge of the tour.
4. `oklch_to_hex`: turn each hue into a colour at a fixed lightness and chroma. OKLCH is perceptual: the same lightness
   means the same contrast against the background for every hue.
"""
import math

DEFAULT_FLOOR_DEG = 16.0  # ~0.04 OKLab distance at chroma 0.14: neighbours are clearly different colours
START_HUE = 25.0
EXACT_TOUR_LIMIT = 12  # Held-Karp is exact; 2**n * n * n steps
LIGHTNESS_DARK = 0.78  # for a dark background
LIGHTNESS_LIGHT = 0.55  # for a light background
CHROMA = 0.14
CROWDED_LIGHTNESS_SWING = 0.06  # alternate lightness between neighbours when hue alone can not separate them
_EPSILON = 1e-12


# --- distances -------------------------------------------------------------------------------------------------

def cosine_distance(a, b):
    """1 - cosine similarity. 0 for the same direction, 1 for unrelated vectors, up to 2 for opposite ones."""
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    if norm == 0:
        return 1.0
    return min(2.0, max(0.0, 1.0 - dot / norm))


def distance_matrix(vectors):
    n = len(vectors)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i][j] = matrix[j][i] = cosine_distance(vectors[i], vectors[j])
    return matrix


# --- travelling salesman ----------------------------------------------------------------------------------------

def tour_length(tour, dist):
    """Length of the closed tour (the last group connects back to the first)."""
    return sum(dist[tour[i]][tour[(i + 1) % len(tour)]] for i in range(len(tour)))


def _tour_exact(dist):
    """Held-Karp dynamic programming: the optimal closed tour."""
    n = len(dist)
    size = 1 << n
    infinity = float("inf")
    cost = [[infinity] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]
    cost[1][0] = 0.0
    for mask in range(1, size, 2):  # every subset that contains group 0
        row = cost[mask]
        for last in range(n):
            here = row[last]
            if here == infinity:
                continue
            for nxt in range(1, n):
                bit = 1 << nxt
                if mask & bit:
                    continue
                candidate = here + dist[last][nxt]
                if candidate < cost[mask | bit][nxt]:
                    cost[mask | bit][nxt] = candidate
                    parent[mask | bit][nxt] = last
    full = size - 1
    last = min(range(1, n), key=lambda j: cost[full][j] + dist[j][0])
    tour, mask = [], full
    while last != -1:
        tour.append(last)
        mask, last = mask ^ (1 << last), parent[mask][last]
    return tour[::-1]


def _improve(tour, dist):
    """2-opt (reverse a stretch) and Or-opt (move 1 to 3 groups elsewhere) until neither shortens the tour."""
    n = len(tour)
    improved = True
    while improved:
        improved = False
        for i in range(n - 1):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                a, b, c, e = tour[i], tour[i + 1], tour[j], tour[(j + 1) % n]
                if dist[a][c] + dist[b][e] < dist[a][b] + dist[c][e] - _EPSILON:
                    tour[i + 1:j + 1] = tour[i + 1:j + 1][::-1]
                    improved = True
        for length in (1, 2, 3):
            for start in range(n):
                rotated = tour[start:] + tour[:start]
                segment, rest = rotated[:length], rotated[length:]
                before, after = rest[-1], rest[0]
                removed = dist[before][segment[0]] + dist[segment[-1]][after] - dist[before][after]
                best_gain, best = _EPSILON, None
                for k in range(len(rest) - 1):  # the gap the segment came from is left out on purpose
                    p, q = rest[k], rest[k + 1]
                    for candidate in (segment, segment[::-1]):
                        added = dist[p][candidate[0]] + dist[candidate[-1]][q] - dist[p][q]
                        if removed - added > best_gain:
                            best_gain, best = removed - added, (k, candidate)
                if best is not None:
                    k, candidate = best
                    tour = rest[:k + 1] + candidate + rest[k + 1:]
                    improved = True
    return tour


def _tour_heuristic(dist, restarts=6):
    """Nearest-neighbour tours from a few starting groups, each polished by `_improve`; the shortest wins."""
    n = len(dist)
    best = None
    for start in range(0, n, max(1, n // restarts)):
        tour, left = [start], set(range(n)) - {start}
        while left:
            nxt = min(left, key=lambda j: (dist[tour[-1]][j], j))
            tour.append(nxt)
            left.remove(nxt)
        tour = _improve(tour, dist)
        if best is None or tour_length(tour, dist) < tour_length(best, dist):
            best = tour
    return best


def tsp_tour(dist):
    """The shortest closed tour through all groups, as a list of indexes. Exact up to EXACT_TOUR_LIMIT groups, a
    2-opt/Or-opt local search from several starts beyond that."""
    n = len(dist)
    if n <= 3:
        return list(range(n))
    if n <= EXACT_TOUR_LIMIT:
        return _tour_exact(dist)
    return _tour_heuristic(dist)


def cut_at_longest_edge(tour, dist):
    """Rotate the closed tour so that it starts right after its longest edge (the least related neighbours). A tour
    can be walked in either direction; the one that starts at the lower index is used, so the same groups always get
    the same colours (callers pass the groups in a stable order, e.g. sorted by name)."""
    n = len(tour)
    if n < 2:
        return list(tour)
    i = max(range(n), key=lambda k: dist[tour[k]][tour[(k + 1) % n]])
    order = tour[i + 1:] + tour[:i + 1]
    return order[::-1] if order[0] > order[-1] else order


# --- hues -------------------------------------------------------------------------------------------------------

def proportional_gaps(edges, floor_deg=DEFAULT_FLOOR_DEG):
    """Split 360 degrees in proportion to `edges`, but give every edge at least `floor_deg`. Edges that fall under the
    floor are pinned to it and the others share what is left (still proportionally), repeating until stable. If the
    wheel is too small to give everyone the floor, the steps are even."""
    n = len(edges)
    if n == 0:
        return []
    if floor_deg * n >= 360 or sum(edges) <= _EPSILON:
        return [360.0 / n] * n
    pinned = set()
    while True:
        free = [i for i in range(n) if i not in pinned]
        budget = 360.0 - floor_deg * len(pinned)
        total = sum(edges[i] for i in free)
        gaps = [floor_deg if i in pinned else budget * edges[i] / total for i in range(n)]
        low = [i for i in free if gaps[i] < floor_deg - _EPSILON]
        if not low:
            return gaps
        pinned.update(low)


def layout(dist, floor_deg=DEFAULT_FLOOR_DEG, start_hue=START_HUE):
    """(order, hues): the groups in wheel order (indexes) and the hue in degrees of each, in that order."""
    n = len(dist)
    if n == 0:
        return [], []
    order = cut_at_longest_edge(tsp_tour(dist), dist)
    edges = [dist[order[k]][order[(k + 1) % n]] for k in range(n)]  # the last edge closes the loop
    gaps = proportional_gaps(edges, floor_deg)
    hues, hue = [], start_hue
    for gap in gaps:
        hues.append(hue % 360.0)
        hue += gap
    return order, hues


def suggest_hues(dist, floor_deg=DEFAULT_FLOOR_DEG, start_hue=START_HUE):
    """The hue in degrees for each group, indexed like `dist`."""
    order, hues = layout(dist, floor_deg, start_hue)
    result = [0.0] * len(dist)
    for position, index in enumerate(order):
        result[index] = hues[position]
    return result


# --- colours ----------------------------------------------------------------------------------------------------

def _oklch_to_linear_rgb(lightness, chroma, hue):
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)


def _in_gamut(rgb):
    return all(-1e-4 <= c <= 1 + 1e-4 for c in rgb)


def oklch_to_hex(lightness, chroma, hue):
    """'#rrggbb' for an OKLCH colour. A colour outside sRGB loses chroma (not lightness or hue) until it fits, so the
    contrast the lightness promises is kept."""
    rgb = _oklch_to_linear_rgb(lightness, chroma, hue)
    if not _in_gamut(rgb):
        low, high = 0.0, chroma
        for _ in range(24):
            middle = (low + high) / 2
            if _in_gamut(_oklch_to_linear_rgb(lightness, middle, hue)):
                low = middle
            else:
                high = middle
        rgb = _oklch_to_linear_rgb(lightness, low, hue)

    def gamma(c):
        c = min(1.0, max(0.0, c))
        return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055

    return "#" + "".join(f"{round(gamma(c) * 255):02x}" for c in rgb)


def suggest_palette(vectors, lightness=LIGHTNESS_DARK, chroma=CHROMA, floor_deg=DEFAULT_FLOOR_DEG):
    """(order, colours): a '#rrggbb' colour for each embedding (same order as `vectors`), such that groups that are
    close in meaning get close hues, and `order`, the indexes in colour-wheel order (neighbours are alike).

    With more groups than the wheel can separate (360 / floor_deg), neighbours alternate between a lighter and a
    darker shade as well."""
    order, hues = layout(distance_matrix(vectors), floor_deg)
    crowded = floor_deg * len(vectors) > 360
    colours = [""] * len(vectors)
    for position, index in enumerate(order):
        shade = lightness
        if crowded:
            shade += CROWDED_LIGHTNESS_SWING if position % 2 == 0 else -CROWDED_LIGHTNESS_SWING
        colours[index] = oklch_to_hex(shade, chroma, hues[position])
    return order, colours


def suggest_colors(vectors, lightness=LIGHTNESS_DARK, chroma=CHROMA, floor_deg=DEFAULT_FLOOR_DEG):
    """Just the colours of `suggest_palette`."""
    return suggest_palette(vectors, lightness, chroma, floor_deg)[1]
