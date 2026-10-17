"""Load versioned LLM prompts from the repository's prompts/ folder."""
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Return the full text of prompts/<name>.md (for example "search_query_v1")."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
