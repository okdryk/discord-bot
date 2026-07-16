"""環境変数とSSMパラメータ名の集約。"""
import os

INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
PARAM_PREFIX = os.environ.get("PARAM_PREFIX", "/palworld")
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "")
WORKER_FUNCTION_NAME = os.environ.get("WORKER_FUNCTION_NAME", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

SAVE_DIR = os.environ.get("SAVE_DIR", "")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "palworld")

EMPTY_MINUTES_TO_STOP = int(os.environ.get("EMPTY_MINUTES_TO_STOP", "10"))
MEMORY_ALERT_PERCENT = float(os.environ.get("MEMORY_ALERT_PERCENT", "80"))
MEMORY_ALERT_COOLDOWN_MINUTES = 30

REST_API_PORT = 8212

# シークレット (SecureString, 手動登録)
# discord_public_key はString型で、CDKがデプロイ時に interactions Lambda の
# 環境変数 DISCORD_PUBLIC_KEY に埋め込むため、ここには定義しない
PARAM_DISCORD_BOT_TOKEN = f"{PARAM_PREFIX}/secrets/discord_bot_token"
PARAM_DISCORD_WEBHOOK_URL = f"{PARAM_PREFIX}/secrets/discord_webhook_url"
PARAM_ADMIN_PASSWORD = f"{PARAM_PREFIX}/secrets/admin_password"

# 状態 (未登録なら state.py がデフォルト値で扱う)
PARAM_AUTO_STOP_ENABLED = f"{PARAM_PREFIX}/state/auto_stop_enabled"
PARAM_EMPTY_SINCE = f"{PARAM_PREFIX}/state/empty_since"
PARAM_LAST_MEM_ALERT = f"{PARAM_PREFIX}/state/last_mem_alert"
