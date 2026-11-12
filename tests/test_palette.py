import itertools
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palette  # noqa: E402


def points_matrix(points):
    return [[math.dist(p, q) for q in points] for p in points]


def random_points(n, seed, dimensions=3):
    rng = random.Random(seed)
    return [[rng.random() for _ in range(dimensions)] for _ in range(n)]


def brute_force_length(dist):
    n = len(dist)
    return min(palette.tour_length([0, *rest], dist) for rest in itertools.permutations(range(1, n)))


def luminance(hex_color):
    channels = []
    for i in (1, 3, 5):
        c = int(hex_color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(foreground, background):
    a, b = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def hue_distance(a, b):
    """Shortest way round the colour wheel, in degrees."""
    return min((a - b) % 360, (b - a) % 360)


class CosineDistanceTests(unittest.TestCase):
    def test_same_direction_is_zero_and_unrelated_is_one(self):
        self.assertAlmostEqual(palette.cosine_distance([1, 2, 3], [2, 4, 6]), 0.0)
        self.assertAlmostEqual(palette.cosine_distance([1, 0], [0, 1]), 1.0)
        self.assertAlmostEqual(palette.cosine_distance([1, 0], [-1, 0]), 2.0)

    def test_zero_vector_counts_as_unrelated(self):
        self.assertEqual(palette.cosine_distance([0, 0], [1, 1]), 1.0)

    def test_matrix_is_symmetric_with_a_zero_diagonal(self):
        matrix = palette.distance_matrix(random_points(6, 1))
        for i in range(6):
            self.assertEqual(matrix[i][i], 0.0)
            for j in range(6):
                self.assertEqual(matrix[i][j], matrix[j][i])


class TourTests(unittest.TestCase):
    def test_exact_tour_matches_brute_force(self):
        for n in range(4, 9):
            dist = points_matrix(random_points(n, n))
            tour = palette.tsp_tour(dist)
            self.assertEqual(sorted(tour), list(range(n)))
            self.assertAlmostEqual(palette.tour_length(tour, dist), brute_force_length(dist), places=9)

    def test_tiny_inputs(self):
        self.assertEqual(palette.tsp_tour([]), [])
        self.assertEqual(palette.tsp_tour([[0.0]]), [0])
        self.assertEqual(sorted(palette.tsp_tour(points_matrix(random_points(3, 0)))), [0, 1, 2])

    def test_heuristic_is_a_permutation_and_close_to_exact(self):
        for seed in range(6):
            n = 9 + seed % 3
            dist = points_matrix(random_points(n, 100 + seed))
            heuristic = palette._tour_heuristic(dist)
            self.assertEqual(sorted(heuristic), list(range(n)))
            exact = palette.tour_length(palette._tour_exact(dist), dist)
            self.assertLessEqual(palette.tour_length(heuristic, dist), exact * 1.05 + 1e-9, f"seed {seed}")

    def test_heuristic_finds_the_loop_through_points_on_a_circle(self):
        n = 30
        points = [[math.cos(2 * math.pi * k / n), math.sin(2 * math.pi * k / n)] for k in range(n)]
        shuffled = random.Random(5).sample(range(n), n)
        dist = points_matrix([points[i] for i in shuffled])
        tour = palette.tsp_tour(dist)  # more groups than the exact limit
        self.assertGreater(n, palette.EXACT_TOUR_LIMIT)
        side = 2 * math.sin(math.pi / n)
        self.assertAlmostEqual(palette.tour_length(tour, dist), n * side, places=9)

    def test_heuristic_handles_many_groups_quickly(self):
        dist = points_matrix(random_points(60, 9, dimensions=6))
        tour = palette.tsp_tour(dist)
        self.assertEqual(sorted(tour), list(range(60)))

    def test_cut_starts_after_the_longest_edge_and_keeps_the_cycle(self):
        dist = points_matrix(random_points(8, 3))
        tour = palette.tsp_tour(dist)
        order = palette.cut_at_longest_edge(tour, dist)
        closing = dist[order[-1]][order[0]]
        edges = [dist[order[k]][order[(k + 1) % 8]] for k in range(8)]
        self.assertAlmostEqual(closing, max(edges))
        self.assertAlmostEqual(palette.tour_length(order, dist), palette.tour_length(tour, dist))
        self.assertLess(order[0], order[-1])  # canonical direction


class ProportionalGapTests(unittest.TestCase):
    def test_sums_to_a_full_turn_and_follows_the_edges(self):
        edges = [0.35, 0.47, 0.6, 0.63, 0.42, 0.55, 0.44, 0.63, 0.44, 0.67]
        gaps = palette.proportional_gaps(edges, 0)
        self.assertAlmostEqual(sum(gaps), 360.0)
        for edge, gap in zip(edges, gaps):
            self.assertAlmostEqual(gap, 360.0 * edge / sum(edges))

    def test_the_floor_is_respected_and_the_rest_still_sums_to_a_turn(self):
        gaps = palette.proportional_gaps([0.01, 0.5, 0.5, 0.5, 0.02], palette.DEFAULT_FLOOR_DEG)
        self.assertAlmostEqual(sum(gaps), 360.0)
        self.assertTrue(all(g >= palette.DEFAULT_FLOOR_DEG - 1e-9 for g in gaps), gaps)
        self.assertAlmostEqual(gaps[0], palette.DEFAULT_FLOOR_DEG)
        self.assertAlmostEqual(gaps[1], gaps[2])

    def test_a_larger_edge_never_gets_a_smaller_step(self):
        rng = random.Random(4)
        edges = [rng.random() for _ in range(15)]
        gaps = palette.proportional_gaps(edges, palette.DEFAULT_FLOOR_DEG)
        for (e1, g1), (e2, g2) in itertools.combinations(zip(edges, gaps), 2):
            if e1 < e2:
                self.assertLessEqual(g1, g2 + 1e-9)

    def test_more_groups_than_the_wheel_can_separate_get_even_steps(self):
        gaps = palette.proportional_gaps([0.1 * (i + 1) for i in range(30)], palette.DEFAULT_FLOOR_DEG)
        self.assertEqual(gaps, [12.0] * 30)

    def test_degenerate_inputs(self):
        self.assertEqual(palette.proportional_gaps([]), [])
        self.assertEqual(palette.proportional_gaps([0.0, 0.0, 0.0]), [120.0] * 3)
        self.assertEqual(palette.proportional_gaps([0.4]), [360.0])


class HueTests(unittest.TestCase):
    def test_single_group_and_pair(self):
        self.assertEqual(palette.suggest_hues([]), [])
        self.assertEqual(palette.suggest_hues([[0.0]]), [palette.START_HUE])
        pair = palette.suggest_hues([[0.0, 0.5], [0.5, 0.0]])
        self.assertAlmostEqual(hue_distance(pair[0], pair[1]), 180.0)

    def test_the_step_back_to_the_first_group_is_proportional_too(self):
        dist = points_matrix(random_points(9, 21))
        order, hues = palette.layout(dist, floor_deg=0)
        edges = [dist[order[k]][order[(k + 1) % 9]] for k in range(9)]
        closing_step = (hues[0] - hues[-1]) % 360
        self.assertAlmostEqual(closing_step, 360.0 * edges[-1] / sum(edges))
        self.assertEqual(edges[-1], max(edges))  # the wheel starts after the longest edge

    def test_close_groups_get_close_hues(self):
        rng = random.Random(8)
        centres = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        vectors, cluster = [], []
        for c, centre in enumerate(centres):
            for _ in range(3):
                vectors.append([x + rng.gauss(0, 0.05) for x in centre])
                cluster.append(c)
        hues = palette.suggest_hues(palette.distance_matrix(vectors))
        within = [hue_distance(hues[i], hues[j]) for i, j in itertools.combinations(range(12), 2)
                  if cluster[i] == cluster[j]]
        between = [hue_distance(hues[i], hues[j]) for i, j in itertools.combinations(range(12), 2)
                   if cluster[i] != cluster[j]]
        self.assertLess(max(within), min(between) + 1e-9)

    def test_groups_next_to_each_other_on_the_wheel_are_closer_in_meaning_than_a_typical_pair(self):
        for seed in range(5):
            dist = palette.distance_matrix(random_points(10, seed, dimensions=8))
            order, _ = palette.layout(dist)
            neighbours = [dist[order[k]][order[(k + 1) % 10]] for k in range(10)]
            pairs = [dist[i][j] for i, j in itertools.combinations(range(10), 2)]
            self.assertLess(sum(neighbours) / 10, sum(pairs) / len(pairs), f"seed {seed}")

    def test_hues_are_stable(self):
        dist = palette.distance_matrix(random_points(7, 2))
        self.assertEqual(palette.suggest_hues(dist), palette.suggest_hues(dist))


class ColourTests(unittest.TestCase):
    def test_extremes(self):
        self.assertEqual(palette.oklch_to_hex(1.0, 0.0, 0), "#ffffff")
        self.assertEqual(palette.oklch_to_hex(0.0, 0.0, 0), "#000000")

    def test_every_hue_gives_a_valid_colour_with_the_promised_contrast(self):
        dark_background, light_background = "#2c2c2c", "#ffffff"  # Anki's night-mode canvas and the light one
        for hue in range(0, 360, 3):
            dark = palette.oklch_to_hex(palette.LIGHTNESS_DARK, palette.CHROMA, hue)
            light = palette.oklch_to_hex(palette.LIGHTNESS_LIGHT, palette.CHROMA, hue)
            for colour in (dark, light):
                self.assertRegex(colour, r"^#[0-9a-f]{6}$")
            self.assertGreaterEqual(contrast(dark, dark_background), 4.5, f"dark theme, hue {hue}: {dark}")
            self.assertGreaterEqual(contrast(light, light_background), 4.5, f"light theme, hue {hue}: {light}")

    def test_suggest_colors_gives_each_group_its_own_colour(self):
        colours = palette.suggest_colors(random_points(10, 6, dimensions=5))
        self.assertEqual(len(set(colours)), 10)

    def test_neighbours_stay_clearly_different(self):
        vectors = random_points(10, 12, dimensions=5)
        colours = palette.suggest_colors(vectors)
        _, hues = palette.layout(palette.distance_matrix(vectors))
        for k in range(10):
            gap = (hues[(k + 1) % 10] - hues[k]) % 360
            self.assertGreaterEqual(gap, palette.DEFAULT_FLOOR_DEG - 1e-9)
        self.assertEqual(len(colours), 10)

    def test_a_crowd_alternates_lightness_between_neighbours(self):
        vectors = random_points(30, 14, dimensions=6)
        colours = palette.suggest_colors(vectors)
        order, _ = palette.layout(palette.distance_matrix(vectors))
        lums = [luminance(colours[i]) for i in order]
        self.assertTrue(all(lums[k] != lums[k + 1] for k in range(29)))
        self.assertGreater(abs(lums[0] - lums[1]), 0.02)
        self.assertEqual(len(set(colours)), 30)

    def test_no_groups_no_colours(self):
        self.assertEqual(palette.suggest_colors([]), [])


if __name__ == "__main__":
    unittest.main()
