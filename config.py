import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_int(name, default=0):
    raw_value = os.getenv(name, str(default)).strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _get_bool(name, default=False):
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_set(name):
    raw_value = os.getenv(name, "")
    values = set()
    for item in raw_value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            pass
    return values


# ======== Telegram 配置 ========
API_ID = _get_int("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH", "").strip()
BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()

# 频道 ID 必须是 -100 开头的整数。未配置时保持 0，启动时给出友好提示。
STORAGE_CHANNEL_ID = _get_int("TG_STORAGE_CHANNEL")
BACKUP_CHANNEL_ID = _get_int("TG_BACKUP_CHANNEL")

# ======== 数据库与加密配置 ========
DB_NAME = os.getenv("DB_NAME", "vault.db").strip() or "vault.db"
DB_PASSWORD = os.getenv("DB_PASSWORD", "").strip()
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "").strip()

# ======== 临时目录与存储配置 ========
TEMP_DOWNLOAD_DIR = os.getenv("TEMP_DIR", "/tmp/telegram_vault_tmp").strip()
STORAGE_MODE = os.getenv("STORAGE_MODE", "telegram_stealth").strip()

LOCAL_STORAGE_PATH = os.getenv(
    "LOCAL_STORAGE_PATH",
    str(Path.cwd() / "Storage"),
).strip()
if STORAGE_MODE == "local":
    Path(LOCAL_STORAGE_PATH).mkdir(parents=True, exist_ok=True)

# ======== S3 / R2 配置 ========
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "").strip()
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "").strip()
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "telegram-vault").strip()
S3_PUBLIC_DOMAIN = os.getenv("S3_PUBLIC_DOMAIN", "").strip()

# ======== Web 播放服务配置 ========
ENABLE_WEB_SERVER = _get_bool("ENABLE_WEB_SERVER", True)
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "0.0.0.0").strip()
WEB_SERVER_PORT = _get_int("WEB_SERVER_PORT", 8080)
WEB_PUBLIC_HOST = os.getenv("WEB_PUBLIC_HOST", "127.0.0.1:8080").strip()

# ======== 安全限制 ========
ADMIN_ID = _get_int("ADMIN_ID")
BLACKLIST = _get_int_set("BLACKLIST")
RATE_LIMIT_SECONDS = _get_int("RATE_LIMIT_SECONDS", 5)
MAX_DOWNLOAD_COUNT = _get_int("MAX_DOWNLOAD_COUNT", 50)
AUTO_JOIN_REQUIRE_APPROVAL = _get_bool("AUTO_JOIN_REQUIRE_APPROVAL", True)


def validate_config():
    """启动前校验必需配置，避免机器人运行到一半才报隐晦错误。"""
    errors = []

    if API_ID <= 0:
        errors.append("TG_API_ID 未配置或不是数字")
    if not API_HASH:
        errors.append("TG_API_HASH 未配置")
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        errors.append("TG_BOT_TOKEN 未配置或格式不正确")
    if ADMIN_ID <= 0:
        errors.append("ADMIN_ID 未配置或不是数字")
    if STORAGE_CHANNEL_ID == 0:
        errors.append("TG_STORAGE_CHANNEL 未配置或不是有效频道 ID")
    if not ENCRYPTION_KEY or ENCRYPTION_KEY == "CHANGE_THIS_TO_RANDOM_STRING_43_CHARS":
        errors.append("ENCRYPTION_KEY 必须设置为随机强密码")

    if errors:
        joined = "\n - ".join(errors)
        raise RuntimeError(
            "配置不完整，请先复制 .env.example 为 .env 并补齐以下项目：\n"
            f" - {joined}"
        )
