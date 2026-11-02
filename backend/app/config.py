"""Runtime configuration, read from environment variables (or a .env file).

Every setting has a safe development default except secrets, which must be set
in production. See .env.example at the project root.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _parse_device_keys(raw: str) -> dict[str, str]:
    """'FRIDGE-A:key1,FRIDGE-B:key2' -> {'FRIDGE-A': 'key1', 'FRIDGE-B': 'key2'}"""
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        dev, _, key = part.partition(":")
        out[dev.strip()] = key.strip()
    return out


ROOT = Path(__file__).resolve().parents[2]


class Settings:
    def __init__(self) -> None:
        self.db_path = _env("CC_DB_PATH", str(ROOT / "data" / "coldchain.db"))
        self.jwt_secret = _env("CC_JWT_SECRET", "dev-only-change-me-please-32chars!!")
        self.jwt_expire_min = int(_env("CC_JWT_EXPIRE_MIN", "480"))
        self.log_seal_key = _env("CC_LOG_SEAL_KEY", "dev-only-log-seal-key-change-me")

        self.mqtt_host = _env("CC_MQTT_HOST", "127.0.0.1")
        self.mqtt_port = int(_env("CC_MQTT_PORT", "1883"))
        self.mqtt_user = _env("CC_MQTT_USER", "backend")
        self.mqtt_pass = _env("CC_MQTT_PASS", "")
        self.topic_prefix = "coldchain/v1/"

        # FRIDGE-A:key,FRIDGE-B:key  (must match each node's secrets.h)
        self.device_keys = _parse_device_keys(
            _env("CC_DEVICE_KEYS", "FRIDGE-A:dev-key-fridge-a-change-me,FRIDGE-B:dev-key-fridge-b-change-me")
        )
        self.ts_window_s = int(_env("CC_TS_WINDOW_S", "120"))

        # Heat-exposure budget (brief p.1)
        self.budget_min = float(_env("CC_BUDGET_MIN", "60"))
        self.at_risk_frac = float(_env("CC_AT_RISK_FRAC", "0.20"))

        # Alerts: Telegram and/or e-mail (brief p.1 allows SMS, Telegram or e-mail)
        self.telegram_token = _env("CC_TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = _env("CC_TELEGRAM_CHAT_ID", "")
        self.smtp_host = _env("CC_SMTP_HOST", "")
        self.smtp_port = int(_env("CC_SMTP_PORT", "587"))
        self.smtp_user = _env("CC_SMTP_USER", "")
        self.smtp_pass = _env("CC_SMTP_PASS", "")
        self.alert_email_to = _env("CC_ALERT_EMAIL_TO", "")

        self.public_base_url = _env("CC_PUBLIC_BASE_URL", "http://localhost:8000")
        self.admin_initial_password = _env("CC_ADMIN_PASSWORD", "Admin@12345")
        self.model_path = _env("CC_MODEL_PATH", str(ROOT / "ml" / "model" / "predictor.joblib"))
        self.metrics_path = _env("CC_METRICS_PATH", str(ROOT / "ml" / "model" / "metrics.json"))

        self.ai_period_s = int(_env("CC_AI_PERIOD_S", "30"))
        self.offline_after_s = int(_env("CC_OFFLINE_AFTER_S", "20"))
        self.door_alarm_s = int(_env("CC_DOOR_ALARM_S", "60"))


settings = Settings()
