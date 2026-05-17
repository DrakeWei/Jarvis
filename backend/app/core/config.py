import json
import os
import tomllib
from pathlib import Path


def _load_dotenv_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _load_local_env(project_root: Path) -> None:
    _load_dotenv_file(project_root / ".env")
    _load_dotenv_file(project_root / "backend" / ".env")


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
        _load_local_env(root)
        self.project_root = root
        self.data_dir = root / "data"
        self.database_url = os.getenv(
            "JARVIS_DATABASE_URL",
            f"sqlite:///{(self.data_dir / 'app.db').as_posix()}",
        )
        self.jarvis_event_bus_backend = (os.getenv("JARVIS_EVENT_BUS_BACKEND", "local").strip().lower() or "local")
        self.jarvis_redis_url = os.getenv("JARVIS_REDIS_URL", "").strip()
        self.jarvis_redis_channel_prefix = os.getenv("JARVIS_REDIS_CHANNEL_PREFIX", "jarvis")
        self.jarvis_runtime_role = (os.getenv("JARVIS_RUNTIME_ROLE", "hybrid").strip().lower() or "hybrid")
        self.db_pool_size = int(os.getenv("JARVIS_DB_POOL_SIZE", "10"))
        self.db_max_overflow = int(os.getenv("JARVIS_DB_MAX_OVERFLOW", "20"))
        self.db_pool_timeout_seconds = int(os.getenv("JARVIS_DB_POOL_TIMEOUT_SECONDS", "30"))
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
        self.llm_max_tokens = int(os.getenv("JARVIS_LLM_MAX_TOKENS", "4000"))
        self.jarvis_mcp_feishu_enabled = os.getenv("JARVIS_MCP_FEISHU_ENABLED", "").strip() in {"1", "true", "TRUE", "yes", "YES"}
        self.jarvis_mcp_feishu_base_url = os.getenv("JARVIS_MCP_FEISHU_BASE_URL", "").strip()
        self.jarvis_mcp_feishu_bearer_token = os.getenv("JARVIS_MCP_FEISHU_BEARER_TOKEN", "").strip()
        self.jarvis_mcp_feishu_timeout_ms = int(os.getenv("JARVIS_MCP_FEISHU_TIMEOUT_MS", "10000"))
        self.jarvis_mcp_cache_ttl_seconds = float(os.getenv("JARVIS_MCP_CACHE_TTL_SECONDS", "15"))
        self.jarvis_agent_iteration_limit = int(os.getenv("JARVIS_AGENT_ITERATION_LIMIT", "24"))
        self.jarvis_subagent_iteration_limit = int(os.getenv("JARVIS_SUBAGENT_ITERATION_LIMIT", "18"))
        self.jarvis_asset_max_upload_count = int(os.getenv("JARVIS_ASSET_MAX_UPLOAD_COUNT", "8"))
        self.jarvis_asset_max_file_bytes = int(os.getenv("JARVIS_ASSET_MAX_FILE_BYTES", str(50 * 1024 * 1024)))
        self.jarvis_asset_max_image_bytes = int(os.getenv("JARVIS_ASSET_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))
        self.jarvis_asset_chunk_char_limit = int(os.getenv("JARVIS_ASSET_CHUNK_CHAR_LIMIT", "1400"))
        self.jarvis_execution_lease_ttl_seconds = int(os.getenv("JARVIS_EXECUTION_LEASE_TTL_SECONDS", "900"))
        self.jarvis_execution_lease_heartbeat_seconds = int(os.getenv("JARVIS_EXECUTION_LEASE_HEARTBEAT_SECONDS", "30"))
        self.jarvis_job_dispatch_poll_seconds = float(os.getenv("JARVIS_JOB_DISPATCH_POLL_SECONDS", "1.0"))
        self.jarvis_background_job_max_attempts = int(os.getenv("JARVIS_BACKGROUND_JOB_MAX_ATTEMPTS", "3"))
        self.jarvis_background_job_base_backoff_seconds = int(os.getenv("JARVIS_BACKGROUND_JOB_BASE_BACKOFF_SECONDS", "5"))
        self.jarvis_max_concurrent_turn_jobs = int(os.getenv("JARVIS_MAX_CONCURRENT_TURN_JOBS", "4"))
        self.jarvis_max_concurrent_ingestion_jobs = int(os.getenv("JARVIS_MAX_CONCURRENT_INGESTION_JOBS", "2"))
        self.jarvis_ephemeral_event_queue_size = int(os.getenv("JARVIS_EPHEMERAL_EVENT_QUEUE_SIZE", "256"))
        self.jarvis_ephemeral_event_ttl_seconds = int(os.getenv("JARVIS_EPHEMERAL_EVENT_TTL_SECONDS", "300"))
        self.jarvis_completed_background_job_ttl_seconds = int(os.getenv("JARVIS_COMPLETED_BACKGROUND_JOB_TTL_SECONDS", str(7 * 24 * 3600)))
        self.jarvis_completed_ingestion_job_ttl_seconds = int(os.getenv("JARVIS_COMPLETED_INGESTION_JOB_TTL_SECONDS", str(7 * 24 * 3600)))

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
