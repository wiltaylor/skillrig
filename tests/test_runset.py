"""Sampling: what a set of runs says that one run cannot."""

from skillcheck.harnesses import RunResult
from skillcheck.runset import RunSet


def result(output, cost=None, tokens=0) -> RunResult:
    return RunResult(
        harness="claude",
        prompt="p",
        workspace=None,
        exit_code=0,
        duration_s=1.0,
        output=output,
        tool_uses=[],
        stdout="",
        stderr="",
        cost_usd=cost,
        input_tokens=tokens,
    )


def asked(run):
    return "?" in run.output


def test_the_rate_is_the_fraction_of_runs_that_held():
    runs = RunSet([result("Which one?"), result("Which one?"), result("Done.")])

    assert runs.rate(asked) == 2 / 3
    assert runs.most(asked)
    assert runs.some(asked)
    assert not runs.every(asked)


def test_a_set_where_it_never_held_rates_zero():
    runs = RunSet([result("Done."), result("Done.")])

    assert runs.rate(asked) == 0.0
    assert not runs.most(asked)
    assert not runs.some(asked)


def test_an_empty_set_rates_zero_rather_than_dividing_by_nothing():
    assert RunSet().rate(asked) == 0.0


def test_the_cost_of_a_sample_set_is_the_cost_of_all_of_it():
    runs = RunSet([result("a", cost=0.01, tokens=100), result("b", cost=0.02, tokens=150)])

    assert runs.cost_usd == 0.03
    assert runs.tokens == 250
    assert runs.first.output == "a"


def test_a_set_with_no_price_reported_has_no_cost():
    assert RunSet([result("a"), result("b")]).cost_usd is None


def test_explain_says_which_samples_held_for_a_failing_assertion():
    runs = RunSet([result("Which one?"), result("Done.")])

    lines = runs.explain(asked).splitlines()

    assert "sample 0: yes" in lines[0]
    assert "sample 1: NO" in lines[1]
    assert "Done." in lines[1]
