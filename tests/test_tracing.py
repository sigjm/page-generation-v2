import importlib
import os

import pagegen


def test_dotenv_rules(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nLANGSMITH_API_KEY=\nLANGSMITH_PROJECT=from-file\nPAGEGEN_X=1\n", "utf-8")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("LANGSMITH_PROJECT", "from-shell")
    monkeypatch.delenv("PAGEGEN_X", raising=False)
    pagegen.load_dotenv(env)
    assert "LANGSMITH_API_KEY" not in os.environ  # empty values are ignored
    assert os.environ["LANGSMITH_PROJECT"] == "from-shell"  # the shell wins
    assert os.environ["PAGEGEN_X"] == "1"


def test_dev_tracing_needs_a_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert pagegen.enable_dev_tracing() is False and "LANGSMITH_TRACING" not in os.environ
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert pagegen.enable_dev_tracing("pagegen-finetune") is True
    assert os.environ["LANGSMITH_TRACING"] == "true" and os.environ["LANGSMITH_PROJECT"] == "pagegen-finetune"


def test_server_turns_tracing_off_by_default(monkeypatch):
    from pagegen import server
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("PAGEGEN_TRACE", raising=False)
    importlib.reload(server)
    assert os.environ["LANGSMITH_TRACING"] == "false"
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("PAGEGEN_TRACE", "1")
    importlib.reload(server)
    assert os.environ["LANGSMITH_TRACING"] == "true"
