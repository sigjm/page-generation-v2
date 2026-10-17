"""pagegen. Reads KEY=VALUE lines from ./.env (git-ignored) into the environment; the shell always wins."""
import os
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text("utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip())


def enable_dev_tracing(project: str = "pagegen-finetune") -> bool:
    """LangSmith tracing for scripts (dev and fine-tuning data only, spec §4). Needs LANGSMITH_API_KEY."""
    if not os.environ.get("LANGSMITH_API_KEY"):
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", project)
    return True


load_dotenv()
