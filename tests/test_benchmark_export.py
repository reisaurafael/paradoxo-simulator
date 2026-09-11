import json

from examples.run_benchmark import main


def test_export_keeps_outcomes_consistent_with_summary(tmp_path):
    path = tmp_path / "benchmark.json"
    main(3, 7, str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data["summary"]
    assert data["seed"] == 7
    assert len(data["games"]) == summary["n_games"] == 3
    for name, count in summary["win_counts"].items():
        assert count == sum(g["winner"] == name for g in data["games"])
    assert summary["draws"] == sum(g["winner"] is None for g in data["games"])
    assert summary["game_length"]["mean"] == sum(g["hours"] for g in data["games"]) / 3
    main(3, 7, str(path))
    assert json.loads(path.read_text(encoding="utf-8")) == data
