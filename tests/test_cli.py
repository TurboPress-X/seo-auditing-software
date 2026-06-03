from datetime import date
from spider.cli import build_run_dir, resolve_args


def test_resolve_args_prompts_when_missing(monkeypatch):
    answers = iter(["https://prompted.test", "Prompted Client"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    url, client_slug = resolve_args(None, None)
    assert url == "https://prompted.test"
    assert client_slug == "PROMPTED-CLIENT"


def test_resolve_args_uses_given_values():
    url, client_slug = resolve_args("https://x.test", "Acme Co")
    assert url == "https://x.test"
    assert client_slug == "ACME-CO"


def test_build_run_dir_layout(tmp_path):
    import os
    d = build_run_dir(str(tmp_path), "example.com", date(2026, 6, 3), at="0900")
    assert os.path.isdir(d)
    assert os.path.basename(d) == "2026-06-03_0900"
    assert os.path.basename(os.path.dirname(d)) == "example.com"
