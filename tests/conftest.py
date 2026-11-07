"""pytest 公共配置：强制离线模式、隔离测试数据库、保证包可导入。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在导入 app.config 之前设置，否则会尝试访问真实 API
os.environ["AI_MODE"] = "mock"
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="campusmind-test-")) / "test.db")
