"""CLI .env loading tests (run cleanly with no API key)."""

from ablate import cli


def test_load_dotenv_missing_file_runs_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert cli.load_dotenv(tmp_path / "missing.env") is False
    monkeypatch.setattr(cli, "load_dotenv", lambda path=None: False)
    assert cli.main(["preflight", "--no-mde"]) == 0


def test_load_dotenv_reads_pairs_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.delenv("ABLATE_DOTENV_A", raising=False)
    monkeypatch.setenv("ABLATE_DOTENV_B", "keep-me")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "ABLATE_DOTENV_A='loaded-value'\n"
        "export ABLATE_DOTENV_B=\"ignored\"\n",
        encoding="utf-8",
    )
    assert cli.load_dotenv(env_file) is True
    import os
    assert os.environ["ABLATE_DOTENV_A"] == "loaded-value"
    assert os.environ["ABLATE_DOTENV_B"] == "keep-me"
