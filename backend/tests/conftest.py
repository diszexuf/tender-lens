from __future__ import annotations

import os

# Тесты не должны зависеть от локального .env
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_JSON", "false")
