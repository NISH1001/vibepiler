"""Metric: the shape every score shares. Bloat is the first; its numbers must not move."""

import json
from pathlib import Path

from vibepiler.config import Config
from vibepiler.ir.builder import build_program
from vibepiler.passes.bloat_score import Bloat
from vibepiler.passes.metric import Metric
from vibepiler.passes.registry import all_metrics, parse_gate, run_passes

FIX = Path(__file__).parent / "fixtures" / "pyproj"
SNAP = Path(__file__).parent / "snapshots" / "pyproj_bloat_score.json"


def scored():
    prog = build_program(FIX, config=Config.load(FIX))
    run_passes(prog, opt_level=2)
    return prog


def test_bloat_score_is_byte_identical_to_the_snapshot():
    b = {k: v for k, v in scored().metrics["bloat_score"].items() if k != "plan"}
    assert json.dumps(b, sort_keys=True) == SNAP.read_text()


def test_bloat_is_a_metric_with_the_bloat_keys():
    b = Bloat.of(scored())
    assert isinstance(b, Metric) and (Bloat.key, Bloat.label, Bloat.better) == ("bloat", "bloat", "low")
    assert b.value == b.score and set(b.terms()) == {"waste", "tangle"}


def test_trips_follows_the_operator():
    b = Bloat(loc=100, removable_loc=30, tangle=0.0)
    assert b.trips(">", 0.25) and not b.trips(">", 0.5) and b.trips("<", 0.5)


def test_moved_needs_half_a_point():
    a = Bloat(loc=100, removable_loc=30, tangle=0.0)
    assert not Bloat(loc=100, removable_loc=30.4, tangle=0.0).moved(a)
    assert Bloat(loc=100, removable_loc=31, tangle=0.0).moved(a)


def test_registry_lists_metrics_and_parses_a_gate():
    assert all_metrics()[0] is Bloat
    assert parse_gate("bloat>0.25") == (Bloat, ">", 0.25) and parse_gate("warning") is None
