"""Checks on the pure parts of the GDS pipeline (no database needed)."""

from scripts.gds_setup import HIGH_TIER_POINTS, render_report, tier


def test_tier_threshold():
    """90 points and above is the high tier."""
    assert tier(HIGH_TIER_POINTS) == "high"
    assert tier(HIGH_TIER_POINTS - 1) == "standard"


def test_report_compares_with_baseline():
    """The report shows the model next to the majority-class baseline."""
    report = render_report(
        {
            "wines": 1000,
            "high_share": 0.3,
            "accuracy": 0.71,
            "macro_f1": 0.66,
            "baseline_accuracy": 0.70,
            "baseline_macro_f1": 0.41,
        }
    )
    assert "| Always predict the majority tier | 0.700 | 0.410 |" in report
    assert "| Logistic regression on FastRP | 0.710 | 0.660 |" in report
    assert "30% of 1,000 wines" in report
