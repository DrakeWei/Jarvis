from pathlib import Path
import os


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


settings = Settings()
