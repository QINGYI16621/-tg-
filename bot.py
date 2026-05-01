import asyncio
import os
import logging
import sys

# Fix for "There is no current event loop" error on newer Python versions
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, idle
from config import API_ID, API_HASH, BOT_TOKEN

# Configure logging - 禁用敏感日志
# 生产环境建议设置为 WARNING 或 ERROR
logging.basicConfig(
    level=logging.WARNING,  # 只记录警告和错误
    format='%(asctime)s - %(levelname)s - %(message)s'
)
# 禁用 Pyrogram 的详细日志
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("pyrogram.session.session").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

_LOCK_FILE_HANDLE = None


def acquire_single_instance_lock():
    """Prevent multiple bot.py processes from sharing the same Pyrogram sessions."""
    global _LOCK_FILE_HANDLE

    lock_path = os.getenv("BOT_LOCK_FILE", os.path.abspath("bot.lock"))
    _LOCK_FILE_HANDLE = open(lock_path, "a+", encoding="utf-8")

    try:
        import fcntl

        fcntl.flock(_LOCK_FILE_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        print("⚠️ 当前系统不支持 fcntl，已跳过单实例锁。")
        return
    except BlockingIOError:
        _LOCK_FILE_HANDLE.seek(0)
        old_pid = _LOCK_FILE_HANDLE.read().strip() or "unknown"
        print(f"❌ 机器人已经在运行，PID: {old_pid}")
        print("如需重启，请先停止旧进程，或直接运行: bash restart_bot.sh")
        sys.exit(1)

    _LOCK_FILE_HANDLE.seek(0)
    _LOCK_FILE_HANDLE.truncate()
    _LOCK_FILE_HANDLE.write(str(os.getpid()))
    _LOCK_FILE_HANDLE.flush()

# Ensure handlers directory is a package
if not os.path.exists("handlers/__init__.py"):
    with open("handlers/__init__.py", "w") as f:
        f.write("")

async def main():
    # 导入安全配置
    from config import ADMIN_ID, validate_config

    validate_config()
    
    # 1. Initialize the Bot Client
    bot = Client(
        "vault_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        plugins=dict(root="handlers")
    )

    # 2. Initialize the User Client (只用你的主账号)
    user = Client(
        "vault_user",
        api_id=API_ID,
        api_hash=API_HASH
    )

    # 3. Initialize the Storage Client (闲置账号，专用于存储上传)
    storage = Client(
        "vault_storage",
        api_id=API_ID,
        api_hash=API_HASH
    )

    # 挂载到 bot
    bot.user_client = user
    bot.storage_client = storage  # 新增：存储专用账号
    bot.admin_id = ADMIN_ID  # 只有管理员能用

    logger.info("Starting Bot Client...")
    await bot.start()
    
    # 设置机器人菜单命令
    from pyrogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat, BotCommandScopeAllPrivateChats
    try:
        # 1. 设置普通用户的命令菜单。Reply Keyboard 仍然是主导航，这里作为兜底入口。
        public_commands = [
            BotCommand("start", "显示主菜单"),
            BotCommand("cancel", "取消当前操作"),
        ]
        await bot.set_bot_commands(public_commands, scope=BotCommandScopeAllPrivateChats())        # 同时设置 Default 以防万一
        await bot.set_bot_commands(public_commands, scope=BotCommandScopeDefault())
        
        # 2. 设置管理员的菜单 (精简版)
        admin_commands = public_commands + [
             BotCommand("download", "批量下载"),
             BotCommand("tasks", "下载任务记录"),
             BotCommand("security", "安全状态"),
             BotCommand("stats", "统计信息"),
        ]
        await bot.set_bot_commands(admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID))

        print("✅ 机器人菜单命令已更新")
    except Exception as e:
        print(f"⚠️ 无法设置菜单: {e}")

    me = await bot.get_me()
    logger.info(f"Bot started as @{me.username}")
    print(f"\n📢📢📢 请再次确认你是在给这个机器人发消息: @{me.username} 📢📢📢")
    print(f"👉 点击这里直接跳转: https://t.me/{me.username}")
    print(f"👉 点击这里直接跳转: https://t.me/{me.username}")
    print(f"👉 点击这里直接跳转: https://t.me/{me.username}\n")

    logger.info("Starting User Client...")
    await user.start()
    user_me = await user.get_me()
    print(f"✅ 主账号: {user_me.first_name} (@{user_me.username if user_me.username else 'No Username'})")

    # 启动存储账号
    logger.info("Starting Storage Client...")
    await storage.start()
    storage_me = await storage.get_me()
    print(f"✅ 存储账号: {storage_me.first_name} (@{storage_me.username if storage_me.username else 'No Username'})")

    # --- 验证存储频道连接 ---
    from config import STORAGE_CHANNEL_ID
    
    # 先同步存储账号的对话列表来填充 peer 缓存
    print("\n正在同步存储账号对话列表...")
    try:
        async for _ in storage.get_dialogs(limit=100):
            pass
        print("✅ 存储账号对话列表同步完成")
    except Exception as e:
        print(f"⚠️ 同步对话列表失败: {e}")
    
    print("=" * 40)
    print(f"【正在验证存储频道: {STORAGE_CHANNEL_ID}】")
    try:
        # 使用存储账号测试发送
        print("正在测试存储账号发送...")
        sent_msg = await storage.send_message(STORAGE_CHANNEL_ID, "✅ **存储账号已成功连接！**\n系统已就绪。")
        print("✅ 存储账号发送成功！可以正常上传文件。")
    except Exception as e:
        print(f"❌ 存储账号发送失败: {e}")
        print("请确保闲置账号已加入存储频道并有发送权限！")
    print("=" * 40 + "\n")
    # -----------------------

    # Keep the application running
    logger.info("Telegram Private Vault is running...")
    await idle()
    
    logger.info("Stopping clients...")
    await bot.stop()
    await user.stop()
    await storage.stop()

if __name__ == "__main__":
    try:
        acquire_single_instance_lock()
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
