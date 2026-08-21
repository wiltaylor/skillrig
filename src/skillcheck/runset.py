"""Several runs of the same prompt, and what fraction of them behaved.

Agents are not deterministic, so a single run passing says less than it looks
like. `samples=` runs the same prompt more than once and hands back one of these,
which turns "did it work" into "how often does it work".
"""

from collections.abc import Callable

from .harnesses import RunResult

Predicate = Callable[[RunResult], bool]


class RunSet(list):
    """The results of running one prompt several times."""

    @property
    def first(self) -> RunResult:
        return self[0]

    def rate(self, predicate: Predicate) -> float:
        """The fraction of runs the predicate held for, 0.0 to 1.0."""
        if not self:
            return 0.0
        return sum(1 for result in self if predicate(result)) / len(self)

    def every(self, predicate: Predicate) -> bool:
        return all(predicate(result) for result in self)

    def some(self, predicate: Predicate) -> bool:
        return any(predicate(result) for result in self)

    def most(self, predicate: Predicate) -> bool:
        """True when the predicate held for more than half the runs."""
        return self.rate(predicate) > 0.5

    @property
    def cost_usd(self) -> float | None:
        costs = [result.cost_usd for result in self if result.cost_usd is not None]
        return sum(costs) if costs else None

    @property
    def tokens(self) -> int:
        return sum(result.tokens for result in self)

    def explain(self, predicate: Predicate) -> str:
        """One line per run saying whether it held, for a failing assertion."""
        lines = []
        for index, result in enumerate(self):
            held = "yes" if predicate(result) else "NO "
            lines.append(f"  sample {index}: {held}  {result.output.strip()[:120]!r}")
        return "\n".join(lines)
