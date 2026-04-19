"""Optional LLM helpers (currently: local Ollama)."""

from __future__ import annotations

import os

from jarvis import config
from jarvis.paths import PROJECT_ROOT


def _load_dotenv() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def rewrite_weather_if_enabled(
    weather_text: str,
    *,
    llm_backend: str,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
) -> str:
    b = (llm_backend or "").strip().lower()
    if b in ("", "none", "off", "false", "0"):
        return weather_text

    _load_dotenv()

    if b == "ollama":
        from jarvis.services.llm_ollama import rewrite_weather_briefing_with_ollama

        host = (
            (ollama_host or "").strip()
            or (os.environ.get("JARVIS_OLLAMA_HOST") or "").strip()
            or config.DEFAULT_OLLAMA_HOST
        )
        model = (
            (ollama_model or "").strip()
            or (os.environ.get("JARVIS_OLLAMA_MODEL") or "").strip()
            or config.DEFAULT_OLLAMA_MODEL
        )
        return rewrite_weather_briefing_with_ollama(weather_text, host=host, model=model)

    return weather_text
