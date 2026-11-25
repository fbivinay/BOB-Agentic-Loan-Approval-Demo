"""Deterministic integer PRNG (SplitMix64). Pure Python integer arithmetic, so the same seed gives the
same stream on every platform and Python version (unlike `random`, whose helper methods may change)."""

import hashlib

_M = (1 << 64) - 1


class Rng:
    def __init__(self, seed: int):
        self.s = seed & _M

    @classmethod
    def labelled(cls, seed: int, label: str) -> "Rng":
        """Independent stream per label (e.g. per customer), so changing one place never shifts another."""
        return cls(int.from_bytes(hashlib.sha256(f"{seed}:{label}".encode()).digest()[:8], "big"))

    def u64(self) -> int:
        self.s = (self.s + 0x9E3779B97F4A7C15) & _M
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M
        return z ^ (z >> 31)

    def randint(self, a: int, b: int) -> int:
        return a + self.u64() % (b - a + 1)

    def chance(self, pct: int) -> bool:
        return self.u64() % 100 < pct

    def choice(self, seq):
        return seq[self.u64() % len(seq)]

    def weighted(self, pairs):
        """pairs: [(value, integer weight), ...]"""
        total = sum(w for _, w in pairs)
        r = self.u64() % total
        for v, w in pairs:
            if r < w:
                return v
            r -= w
        raise AssertionError

    def shuffle(self, lst: list) -> list:
        for i in range(len(lst) - 1, 0, -1):
            j = self.u64() % (i + 1)
            lst[i], lst[j] = lst[j], lst[i]
        return lst

    def sample(self, seq, k: int) -> list:
        return self.shuffle(list(seq))[:k]
