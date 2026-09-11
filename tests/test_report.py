"""
tests/test_report.py
====================
Smoke tests for the single-file HTML match report (simulation/report.py).
A full seeded game must record and render into one self-contained document.
"""

import random
from simulation.report import generate_report, record_game, render_html
from simulation.strategies.aggressive import AggressiveStrategy
from simulation.strategies.collector import CollectorStrategy
from simulation.strategies.conservative import ConservativeStrategy
from simulation.strategies.smart import SmartStrategy


def _strategies():
    return {
        "Traveler_A": AggressiveStrategy(),
        "Traveler_C": ConservativeStrategy(),
        "Traveler_S": SmartStrategy(),
        "Traveler_K": CollectorStrategy(),
    }


def test_report_renders_self_contained_html():
    report = record_game(_strategies(), random.Random(42))
    assert report.hours, "no hours recorded"
    assert report.result is not None
    html = render_html(report)
    # Self-contained: one document, inline styles, no external asset references.
    assert html.count("<head>") == 1 and html.count("<body>") == 1
    assert "<style>" in html and "src=" not in html and "<link" not in html
    # Core visual sections rendered.
    for marker in ('class="dashboard"', 'class="matrix"', 'class="market"',
                   'class="standings"', 'id="result"'):
        assert marker in html, f"missing {marker}"


def test_generate_report_writes_file(tmp_path):
    out = tmp_path / "r.html"
    path = generate_report(str(out), seed=7)
    assert out.exists() and out.stat().st_size > 5000
    assert "Paradoxo" in out.read_text(encoding="utf-8")
