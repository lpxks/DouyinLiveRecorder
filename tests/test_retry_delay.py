import random
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retry import retry_delay


class RetryDelayTest(unittest.TestCase):
    def test_first_five_retries_stay_in_2_to_5(self):
        rng = random.Random(7)
        for idx in range(5):
            delay = retry_delay(idx, rng=rng)
            self.assertGreaterEqual(delay, 2)
            self.assertLessEqual(delay, 5)

    def test_last_five_retries_stay_in_5_to_8(self):
        rng = random.Random(7)
        for idx in range(5, 10):
            delay = retry_delay(idx, rng=rng)
            self.assertGreaterEqual(delay, 5)
            self.assertLessEqual(delay, 8)

    def test_fast_band_uses_full_random_range(self):
        rng = random.Random(123)
        values = {retry_delay(i, rng=rng) for i in range(5) for _ in range(200)}
        self.assertEqual(values, {4, 5})

    def test_slow_band_uses_full_random_range(self):
        rng = random.Random(123)
        values = {retry_delay(i, rng=rng) for i in range(5, 10) for _ in range(200)}
        self.assertEqual(values, {5, 6})


if __name__ == '__main__':
    unittest.main()
