"""A little alphabet soup hiding in the test directory.

This file is intentionally named ``soup.py`` rather than ``test_soup.py`` so
pytest's default collection (``test_*.py``/``*_test.py``) skips it. It will
not slow CI down or alter the main test run. If you want to taste the soup,
run it directly::

    pytest tests/soup.py
    python -m tests.soup

Inside you'll find a from-scratch solution to LeetCode #4 -
"Median of Two Sorted Arrays" - a hard problem that asks for the median of
two sorted arrays in ``O(log(min(m, n)))`` time. The trick is a binary search
over the shorter array for the partition that balances the two halves.
"""

from __future__ import annotations

from typing import List


def find_median_sorted_arrays(nums1: List[int], nums2: List[int]) -> float:
    """Return the median of two sorted arrays in O(log(min(m, n))) time.

    LeetCode #4 (Hard). We binary-search the partition in the shorter array
    that makes the combined left half line up with the combined right half.
    """
    a, b = (nums1, nums2) if len(nums1) <= len(nums2) else (nums2, nums1)
    m, n = len(a), len(b)
    if m + n == 0:
        raise ValueError("cannot take the median of two empty arrays")

    total = m + n
    half = (total + 1) // 2
    lo, hi = 0, m

    neg_inf = float("-inf")
    pos_inf = float("inf")

    while lo <= hi:
        i = (lo + hi) // 2
        j = half - i

        a_left = a[i - 1] if i > 0 else neg_inf
        a_right = a[i] if i < m else pos_inf
        b_left = b[j - 1] if j > 0 else neg_inf
        b_right = b[j] if j < n else pos_inf

        if a_left <= b_right and b_left <= a_right:
            if total % 2:
                return float(max(a_left, b_left))
            return (max(a_left, b_left) + min(a_right, b_right)) / 2.0
        if a_left > b_right:
            hi = i - 1
        else:
            lo = i + 1

    raise RuntimeError("unreachable: inputs were not sorted")


# ---------------------------------------------------------------------------
# Tests (pytest-style). Run with ``pytest tests/soup.py``.
# ---------------------------------------------------------------------------


def test_odd_total_even_split() -> None:
    assert find_median_sorted_arrays([1, 3], [2]) == 2.0


def test_even_total() -> None:
    assert find_median_sorted_arrays([1, 2], [3, 4]) == 2.5


def test_disjoint_ranges() -> None:
    assert find_median_sorted_arrays([1, 2, 3], [10, 11, 12]) == 6.5


def test_interleaved() -> None:
    assert find_median_sorted_arrays([1, 4, 7, 10], [2, 3, 8, 9]) == 5.5


def test_one_empty() -> None:
    assert find_median_sorted_arrays([], [1, 2, 3, 4]) == 2.5
    assert find_median_sorted_arrays([5], []) == 5.0


def test_duplicates() -> None:
    assert find_median_sorted_arrays([1, 1, 1], [1, 1, 1]) == 1.0


def test_negative_values() -> None:
    assert find_median_sorted_arrays([-5, -3, -1], [-4, -2, 0]) == -2.5


def test_large_random_matches_sorted_merge() -> None:
    import random

    rng = random.Random(0xA1FA_BE75)  # "alfa bets" - appropriate for soup
    for _ in range(50):
        m = rng.randint(0, 40)
        n = rng.randint(1 if m == 0 else 0, 40)
        a = sorted(rng.randint(-100, 100) for _ in range(m))
        b = sorted(rng.randint(-100, 100) for _ in range(n))
        merged = sorted(a + b)
        mid = len(merged) // 2
        expected = (
            merged[mid]
            if len(merged) % 2
            else (merged[mid - 1] + merged[mid]) / 2.0
        )
        assert find_median_sorted_arrays(a, b) == expected


def test_empty_inputs_raise() -> None:
    try:
        find_median_sorted_arrays([], [])
    except ValueError:
        return
    raise AssertionError("expected ValueError when both arrays are empty")


if __name__ == "__main__":  # pragma: no cover - manual taste-test
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("soup's on.")
