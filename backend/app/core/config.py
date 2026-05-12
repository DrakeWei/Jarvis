import json
import os
import tomllib
from pathlib import Path


def _as_str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _env_json_map(name: str) -> dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except Exception:
        return {}
    return _string_map(loaded)


class Settings:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[3]
        self.project_root = root
        self.data_dir = root / "data"
        self.database_url = os.getenv(
            "JARVIS_DATABASE_URL",
            f"sqlite:///{(self.data_dir / 'app.db').as_posix()}",
        )
        self.app_name = "Jarvis Agent Cockpit"
        self.api_prefix = "/api"
        self.codex_config = self._load_codex_config()
        self.codex_provider_config = self._load_codex_provider_config()
        self.codex_auth = self._load_codex_auth()
        self.model_id = os.getenv("MODEL_ID", "").strip() or _as_str(self.codex_config.get("model"))
        self.openai_api_key = (
            os.getenv("OPENAI_API_KEY", "").strip()
            or _as_str(self.codex_auth.get("OPENAI_API_KEY"))
        )
        self.openai_base_url = (
            os.getenv("OPENAI_BASE_URL", "").strip()
            or _as_str(self.codex_provider_config.get("base_url"))
            or "https://api.openai.com/v1"
        )
        self.openai_wire_api = (
            os.getenv("OPENAI_WIRE_API", "").strip()
            or _as_str(self.codex_provider_config.get("wire_api"))
            or "chat_completions"
        )
        self.openai_query_params = _env_json_map("OPENAI_QUERY_PARAMS_JSON") or _string_map(
            self.codex_provider_config.get("query_params")
        )
        self.openai_http_headers = _env_json_map("OPENAI_HTTP_HEADERS_JSON") or _string_map(
            self.codex_provider_config.get("http_headers")
        )
        self.llm_max_tokens = int(os.getenv("JARVIS_LLM_MAX_TOKENS", "1200"))

    def _load_codex_config(self) -> dict[str, object]:
        config_path = Path.home() / ".codex" / "config.toml"
        if not config_path.exists():
            return {}
        try:
            loaded = tomllib.loads(config_path.read_text())
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _load_codex_provider_config(self) -> dict[str, object]:
        providers = self.codex_config.get("model_providers")
        provider_name = self.codex_config.get("model_provider")
        if not isinstance(providers, dict) or not provider_name:
            return {}
        provider = providers.get(str(provider_name))
        return provider if isinstance(provider, dict) else {}

    def _load_codex_auth(self) -> dict[str, object]:
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.exists():
            return {}
        try:
            loaded = json.loads(auth_path.read_text())
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}


settings = Settings()
