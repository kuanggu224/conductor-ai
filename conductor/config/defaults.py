"""配置文件默认路径。"""

from __future__ import annotations

from pathlib import Path


CONFIG_DIR = Path(".conductor")
LLM_CONFIG_PATH = CONFIG_DIR / "llm.config.json"
CLI_CONFIG_PATH = CONFIG_DIR / "cli.config.json"
