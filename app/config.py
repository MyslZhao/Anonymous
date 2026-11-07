"""运行时配置。优先级：环境变量 > .env 文件 > 默认值。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings:
    """集中管理所有可配置项，避免散落 os.getenv。"""

    def __init__(self) -> None:
        self.api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.ai_mode: str = os.getenv("AI_MODE", "auto").strip().lower()
        self.max_concurrency: int = max(1, _env_int("AI_MAX_CONCURRENCY", 4))
        self.timeout: float = float(os.getenv("AI_TIMEOUT", "60"))
        self.db_path: str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "campusmind.db"))
        self.max_retry_rounds: int = max(0, _env_int("DECISION_MAX_RETRY", 2))

    @property
    def mock_mode(self) -> bool:
        """没有 API Key，或显式要求 mock 时走离线模式。

        离线模式下六角色建议由本地规则生成，形态与真实返回完全一致，
        因此没有网络也能完整演示、跑测试。
        """
        return self.ai_mode == "mock" or not self.api_key

    @property
    def mode_label(self) -> str:
        return "mock（离线模拟）" if self.mock_mode else f"live（{self.model}）"


settings = Settings()
