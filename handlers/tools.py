# 核心功能：下载、合集、文件处理
# 注意：中间件已迁移到 middleware.py

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat, BotCommandScopeAllPrivateChats, BotCommandScopeDefault, ReplyKeyboardMarkup, KeyboardButton
import asyncio
import time
import re
import os
import unicodedata
from pyrogram.types import Message as PyrogramMessage
from database import db

print("🔁 Loading Handler: tools.py")

# ========== Rate Limiting ==========
RATE_LIMIT_DATA = {}  # {uid: [timestamp1, timestamp2, ...]}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_COUNT = 30   # 30 requests per 60s
RATE_LIMIT_BAN_DURATION = 180  # 3 minutes

# ========== Private Bot Lockdown ==========
@Client.on_message(filters.private, group=-20)
async def admin_only_message_middleware(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    if message.from_user.id == getattr(client, "admin_id", None):
        return

    await message.reply_text("🔒 私有机器人，仅管理员可用。")
    message.stop_propagation()


@Client.on_callback_query(group=-20)
async def admin_only_callback_middleware(client: Client, callback: CallbackQuery):
    if callback.from_user and callback.from_user.id == getattr(client, "admin_id", None):
        return

    await callback.answer("🔒 私有机器人，仅管理员可用。", show_alert=True)
    try:
        callback.stop_propagation()
    except AttributeError:
        raise StopPropagation


# ========== Middleware (Global Terms Check, Priority -10) ==========
@Client.on_message(filters.private, group=-10)
async def terms_middleware(client: Client, message: Message):
    if message.from_user.is_bot:
        return

    uid = message.from_user.id
    import time
    from database import db
    from datetime import datetime
    
    # --- 1. Rate Limiting Check ---
    # (Checking before DB to allow DB-free blocking? No, check DB ban first usually?
    # User said "10 req in 60s -> Ban". If already banned, don't count?
    # Actually, check ban first. If unbanned spammer, THEN ban.)
    
    # However, existing code checks ban at step 0.
    # I will keep Ban Check first.
    
    # ... Existing Ban Check implementation needs Update to show Reason ...
    # This runs BEFORE session check. Banned users cannot interact.
    from database import db
    from datetime import datetime
    
    user_status = db.get_user(message.from_user.id)
    if user_status:
        # Check Ban
        if user_status.get('status') == 'banned':
            ban_until = user_status.get('ban_until')
            is_banned = False
            
            if ban_until:
                # Handle String format from SQLite
                if isinstance(ban_until, str):
                    try:
                        ban_until = datetime.fromisoformat(ban_until)
                    except: pass
                
                if isinstance(ban_until, datetime):
                    if ban_until > datetime.now():
                        is_banned = True
                else:
                    # Permanent or invalid? Assume banned if status is banned
                    is_banned = True
            else:
                 is_banned = True # Status is banned but no time? Permanent.

            if is_banned:
                expiry_str = ban_until.strftime('%Y-%m-%d %H:%M') if isinstance(ban_until, datetime) else "永久"
                reason_str = user_status.get('ban_reason') or "违反规则"
                
                await message.reply_text(
                    f"🚫 **您已被封禁**\n\n"
                    f"原因: {reason_str}\n"
                    f"解封: {expiry_str}", 
                    quote=True
                )
                message.stop_propagation()
                return
    
    # --- Rate Limiting Logic (For Non-Admin Active Users) ---
    # Admin is exempt from rate limiting
    from config import ADMIN_ID
    if uid == ADMIN_ID:
        # Admin bypass rate limit
        pass
    else:
        now = time.time()
        history = RATE_LIMIT_DATA.get(uid, [])
        # Filter 60s window
        history = [t for t in history if now - t < RATE_LIMIT_WINDOW]
        history.append(now)
        RATE_LIMIT_DATA[uid] = history
        
        if len(history) > RATE_LIMIT_COUNT:
             # Ban User
             from datetime import timedelta
             duration = 180 # 3 mins
             until = datetime.now() + timedelta(seconds=duration)
             until_str = until.strftime('%Y-%m-%d %H:%M:%S')
             reason = f"频次限制 ({RATE_LIMIT_COUNT}/{RATE_LIMIT_WINDOW}s)"
             
             db.set_user_ban(uid, "banned", until_str, reason)
             
             # Clear history
             RATE_LIMIT_DATA.pop(uid, None)
             
             await message.reply_text(f"🚫 **操作过快**\n\n已触发频次限制 ({RATE_LIMIT_COUNT}次/{RATE_LIMIT_WINDOW}秒)。\n封禁 3分钟。")
             message.stop_propagation()
             return

    from handlers.session import is_session_active, activate_session
    uid = message.from_user.id
    agree_text = "✅ 我已阅读并同意用户协议"
    start_btn_text = "🚀 开始使用"

    # 1. Check Agreement Click (Transition Stage 2 -> 3)
    if message.text == agree_text:
        # Activate Session
        activate_session(uid)
        db.update_user_terms(uid, True)
        
        await message.reply_text("✅ **协议已签署**\n\n身份验证通过，正在进入系统...", reply_markup=None)
        # Send Main Menu
        from handlers.setup import send_main_menu
        await send_main_menu(client, message)
        
        # Stop propagation
        message.stop_propagation()
        return

    # 2. Check Session Active (Stage 3+)
    if is_session_active(uid):
        message.continue_propagation()
        return

    # 3. Check Start Click (Transition Stage 1 -> 2)
    if message.text == start_btn_text:
        disclaimer_text = (
            "📜 **用户服务协议与免责声明**\n\n"
            "欢迎使用本个人数据管理工具。在使用本服务前，请您务必仔细阅读并理解以下条款：\n\n"
            "**1. 服务定义**\n"
            "本机器人仅为基于 Telegram 平台的第三方数据索引与加密辅助工具。我们不提供任何形式的内容托管、版权分发或互联网接入服务。所有文件实体均由用户自行存储于 Telegram 官方服务器。\n\n"
            "**2. 数据安全与隐私**\n"
            "您的数据索引采用私有化加密存储。用户需自行妥善保管提取码、访问密钥及个人账号。因用户操作不当（如泄露密钥）、设备丢失或 Telegram 平台政策调整导致的数据不可访问，开发者不承担恢复义务与赔偿责任。\n\n"
            "**3. 用户行为规范**\n"
            "用户承诺严禁利用本工具存储、传播以下内容：\n"
            "• 淫秽、色情、赌博、暴力、恐怖主义等违法信息；\n"
            "• 侵犯他人知识产权（版权、商标权）的内容；\n"
            "• 违反用户所在地法律法规或 Telegram 平台公约的其他内容。\n\n"
            "**4. 免责声明**\n"
            "• 本工具按「现状」提供，开发者不对服务的及时性、安全性、准确性作担保。\n"
            "• 对于因不可抗力、黑客攻击、系统不稳定或第三方服务（Telegram）故障导致的服务中断，开发者不承担责任。\n"
            "• 若发现违规用途，我们保留在不通知的情况下配合执法机关进行封禁账号、删除索引或上报数据的权利。\n\n"
            "🔴 **点击下方按钮即表示您已完整阅读并认可上述所有条款。**"
        )
        await message.reply_text(
            disclaimer_text,
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton(agree_text)]], 
                resize_keyboard=True, 
                one_time_keyboard=False, 
                is_persistent=True,
                placeholder="请点击同意以继续..."
            ),
            quote=True
        )
        message.stop_propagation()
        return

    # 4. Default / Initial State (Stage 1)
    # User sent /start or anything else, but NO session and NO specific button click.
    # Show "Start Menu" (Highest Level)
    welcome_text = (
        "👋 **欢迎使用文件下载机器人**\n\n"
        "📌 **使用方法：**\n"
        "直接发送 Telegram 消息链接，机器人会自动帮你下载并发送文件。\n\n"
        "支持格式：\n"
        "• `https://t.me/频道名/消息ID`\n"
        "• `https://t.me/c/频道ID/消息ID`\n\n"
        "👉 请先点击下方按钮阅读并同意用户协议。"
    )
    await message.reply_text(
        welcome_text,
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton(start_btn_text)]],
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True,
            placeholder="点击开始..."
        )
    )
    message.stop_propagation()


# 全局存储
user_download_dest = {}
user_download_chat = {}
pending_download_jobs = {}
cancel_download_users = set()
user_last_action = {}  # 频率限制：记录用户上次操作时间
user_collecting_mode = {}  # 收集模式：{user_id: {"collection_id": xxx, "collection_name": xxx, "files": []}}
user_last_collection = {}  # 最后一次使用的合集 {user_id: {'id': id, 'name': name}}
media_group_states = {} # {media_group_id: {'msg': Message, 'keys': [], 'bound_col_id': None, 'bound_col_name': None, 'count': 0, 'last_update': 0}}
user_interaction_state = {} # 用户交互状态: {user_id: "status_string"}
user_pending_file = {} # 用于存储待处理的文件信息，例如在创建合集时

from datetime import datetime, timedelta

DEFAULT_DOWNLOAD_COLLECTION_NAME = "我的下载"

# User Request History for Rate Limiting
user_request_history = {}

def is_admin_user(client, user_id):
    return user_id == getattr(client, "admin_id", None)

async def require_admin(client, event, alert=False):
    user = getattr(event, "from_user", None)
    user_id = getattr(user, "id", None)
    if is_admin_user(client, user_id):
        return True

    if alert and hasattr(event, "answer"):
        await event.answer("⛔ 此功能仅限管理员使用。", show_alert=True)
    elif hasattr(event, "reply_text"):
        await event.reply_text("⛔ 此功能仅限管理员使用。")
    return False

def get_cancel_keyboard(is_admin_user=False, show_admin_shortcuts=False):
    buttons = [[KeyboardButton("❌ 取消操作"), KeyboardButton("🔙 返回主菜单")]]
    if is_admin_user and show_admin_shortcuts:
        buttons.insert(0, [KeyboardButton("📥 批量下载"), KeyboardButton("☁️ 存储/上传")])
    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        placeholder="可取消或返回主菜单"
    )

async def clear_user_state(user_id):
    user_interaction_state.pop(user_id, None)
    user_pending_file.pop(user_id, None)
    user_download_dest.pop(user_id, None)
    user_download_chat.pop(user_id, None)
    cancel_download_users.discard(user_id)
    for job_id, job in list(pending_download_jobs.items()):
        if job.get("user_id") == user_id:
            pending_download_jobs.pop(job_id, None)

def _generate_collection_key(prefix="file_store"):
    import secrets
    import string

    random_length = secrets.randbelow(17) + 16
    random_chars = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(random_length))
    return f"{prefix}{random_chars}"

def get_or_create_default_download_collection(owner_id):
    collection = db.get_collection_by_name(DEFAULT_DOWNLOAD_COLLECTION_NAME, owner_id)
    if collection:
        return collection

    access_key = _generate_collection_key()
    collection_id = db.create_collection(DEFAULT_DOWNLOAD_COLLECTION_NAME, access_key, owner_id)
    if not collection_id:
        return db.get_collection_by_name(DEFAULT_DOWNLOAD_COLLECTION_NAME, owner_id)

    return {
        "id": collection_id,
        "name": DEFAULT_DOWNLOAD_COLLECTION_NAME,
        "access_key": access_key,
        "owner_id": owner_id,
        "created_at": None,
    }

def create_download_task_collection(owner_id, source_name=None):
    base_name = _short_text(source_name or "来源", 18)
    base_name = re.sub(r"[\r\n\t/\\:*?\"<>|]+", "_", base_name).strip() or "来源"
    stamp = datetime.now().strftime("%m%d_%H%M")
    for suffix in ("", f"_{_generate_collection_key('')[:4]}", f"_{int(time.time())}"):
        name = f"下载_{base_name}_{stamp}{suffix}"
        access_key = _generate_collection_key()
        collection_id = db.create_collection(name, access_key, owner_id)
        if collection_id:
            return {
                "id": collection_id,
                "name": name,
                "access_key": access_key,
                "owner_id": owner_id,
                "created_at": None,
            }
    return get_or_create_default_download_collection(owner_id)

async def check_auth(client, message):
    """
    统一权限验证 (Auth + Rate Limit)
    1. 记录/更新用户信息
    2. 检查封禁状态
    3. 检查频率限制 (60s > 10次 -> 封禁1天)
    """
    user = message.from_user
    if not user: return False
    
    # 0. 更新用户资料
    db.update_user(user.id, user.username, user.first_name)
    
    # 1. 管理员豁免
    if user.id == client.admin_id:
        return True
    
    # 2. 检查封禁
    u_data = db.get_user(user.id)
    if u_data and u_data['status'] == 'banned':
        await message.reply_text("⛔ **你已被封禁**\n请联系管理员解封。")
        return False
        
    # 3. 频率限制 (60s 滑动窗口)
    now = time.time()
    history = user_request_history.get(user.id, [])
    # 清理过期记录
    history = [t for t in history if now - t < 60]
    
    # 判定
    if len(history) >= 10:
        # 触发自动封禁
        ban_until = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.set_user_ban(user.id, 'banned', ban_until)
        await message.reply_text("⛔ **请求过于频繁！**\n系统已自动封禁你 1 天。")
        return False
        
    history.append(now)
    user_request_history[user.id] = history
    return True

# Admin Commands
@Client.on_message(filters.command("users") & filters.private)
async def list_users(client, message):
    if message.from_user.id != client.admin_id: return
    users = db.get_all_users()
    text = f"👥 **用户列表 ({len(users)})**\n\n"
    for u in users:
        status_icon = "🔴" if u['status'] == 'banned' else "🟢"
        text += f"{status_icon} `{u['id']}` {u['first_name']} (@{u['username']})\n"
    await message.reply_text(text)

@Client.on_message(filters.command("ban") & filters.private)
async def ban_user_cmd(client, message):
    if message.from_user.id != client.admin_id: return
    try:
        target_id = int(message.command[1])
        db.set_user_ban(target_id, 'banned', "9999-12-31")
        await message.reply_text(f"🔴 已封禁用户 `{target_id}`")
    except:
        await message.reply_text("用法: `/ban 用户ID`")

@Client.on_message(filters.command("unban") & filters.private)
async def unban_user_cmd(client, message):
    if message.from_user.id != client.admin_id: return
    try:
        target_id = int(message.command[1])
        db.set_user_ban(target_id, 'active')
        await message.reply_text(f"🟢 已解封用户 `{target_id}`")
    except:
        await message.reply_text("用法: `/unban 用户ID`")

@Client.on_message(filters.command("security") & filters.private)
async def security_cmd(client: Client, message: Message):
    if not await require_admin(client, message):
        return
    users = db.get_all_users()
    active_jobs = len([job for job in pending_download_jobs.values() if job.get("user_id") == message.from_user.id])
    await message.reply_text(
        "🔐 **安全状态**\n\n"
        f"管理员 ID: `{client.admin_id}`\n"
        f"用户数: `{len(users)}`\n"
        f"待确认下载: `{active_jobs}`\n"
        "✅ 批量下载功能仅管理员可见/可用。\n"
        "✅ 普通用户只能使用存储、提取码和自己的合集功能。\n"
        "⚠️ 如果机器人泄露，请第一时间去 BotFather 重置 Token，并停止服务器进程。"
    )

@Client.on_message(filters.command("tasks") & filters.private)
async def download_tasks_cmd(client: Client, message: Message):
    if not await require_admin(client, message):
        return
    tasks = db.get_user_download_tasks(message.from_user.id, limit=10)
    if not tasks:
        await message.reply_text("📋 暂无下载任务记录。")
        return

    lines = ["📋 **最近下载任务**\n"]
    buttons = []
    for task in tasks:
        done = int(task.get("success_count") or 0) + int(task.get("fail_count") or 0)
        total = int(task.get("limit_count") or 0)
        title = _short_text(task.get("source_title") or str(task.get("source_chat_id")), 28)
        lines.append(
            f"• `{task['task_key']}` | {task.get('status')}\n"
            f"  来源: **{title}** (`{task.get('source_chat_id')}`)\n"
            f"  进度: `{done}/{total}` | ✅ {task.get('success_count') or 0} | ❌ {task.get('fail_count') or 0}\n"
            f"  提取码: `{task.get('collection_key') or '-'}`\n"
        )
        if task.get("status") in ("stopped", "failed") and done < total:
            buttons.append([InlineKeyboardButton(f"继续 {task['task_key']}", callback_data=f"resume_{task['task_key']}")])

    await message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )

@Client.on_callback_query(filters.regex(r"^resume_"))
async def resume_download_task_callback(client: Client, callback: CallbackQuery):
    if not await require_admin(client, callback, alert=True):
        return
    task_key = callback.data.split("_", 1)[1]
    task = db.get_download_task_by_key(task_key)
    if not task or task.get("owner_id") != callback.from_user.id:
        await callback.answer("任务不存在或无权限", show_alert=True)
        return

    done = int(task.get("success_count") or 0) + int(task.get("fail_count") or 0)
    total = int(task.get("limit_count") or 0)
    remaining = max(0, total - done)
    if remaining <= 0:
        await callback.answer("任务已经没有剩余数量。", show_alert=True)
        return

    await callback.answer("开始续跑")
    from types import SimpleNamespace

    class MockMessage:
        def __init__(self, bot_client, user_id):
            self.chat = SimpleNamespace(id=user_id, type="private", title="User")
            self.from_user = SimpleNamespace(id=user_id, is_bot=False, username="User", first_name="")
            self._client = bot_client
            self.text = ""

        async def reply_text(self, text, **kwargs):
            return await self._client.send_message(self.chat.id, text, **kwargs)

    await callback.message.edit_text(
        f"🔁 正在续跑任务 `{task_key}`\n"
        f"剩余数量: `{remaining}`\n"
        "提示：续跑会按原来源重新拉取剩余数量，极少数情况下可能产生重复文件。"
    )
    mock_msg = MockMessage(client, callback.from_user.id)
    await do_batch_download(
        client,
        mock_msg,
        int(task["source_chat_id"]),
        remaining,
        task.get("dest") or "collection",
        start_message_id=task.get("start_message_id"),
        source_name=task.get("source_title"),
    )

# ========== 安全检查 ==========

def is_blacklisted(client, user_id):
    """检查用户是否在黑名单中"""
    return hasattr(client, 'blacklist') and user_id in client.blacklist

def check_rate_limit(user_id, limit_seconds=5):
    """检查频率限制，返回 True 表示通过，False 表示被限制"""
    now = time.time()
    last_time = user_last_action.get(user_id, 0)
    if now - last_time < limit_seconds:
        return False
    user_last_action[user_id] = now
    return True

@Client.on_message(filters.reply & filters.private & filters.text)
async def handle_reply_input(client: Client, message: Message):
    """Handle reply to download/newcollection prompts."""
    # 权限检查
    if message.from_user.id != client.admin_id:
        return

    if not message.reply_to_message:
        return
    
    prompt_text = message.reply_to_message.text or ""
    
    # 处理下载回复 (匹配新旧两种提示格式)
    if ("频道ID" in prompt_text and "数量" in prompt_text) or "请按格式输入" in prompt_text:
        try:
            chat_id, start_message_id, limit = await _parse_download_source(client, message.text)
            dest = user_download_dest.get(message.from_user.id, "fast_collection")
            await request_download_confirmation(client, message, chat_id, limit, dest, start_message_id=start_message_id)
        except Exception:
            await message.reply_text(
                "❌ 格式错误！\n\n"
                "可以直接发消息链接，例如：`https://t.me/c/1234567890/4567`\n"
                "或输入：`频道ID 消息ID 数量`（精准模式）"
            )
        message.stop_propagation()
        return
    
    # 处理创建合集回复
    elif "请输入合集名称" in prompt_text:
        collection_name = message.text.strip()
        if collection_name:
            await do_create_collection(client, message, collection_name)
            message.stop_propagation()
            return

@Client.on_message(filters.command("download") & filters.private)
async def batch_download(client: Client, message: Message):
    """
    Batch download messages from a specific channel ID.
    Usage:
      /download <chat_id> <limit>
      /download <chat_id> <start_message_id> <limit>
    """
    if not await require_admin(client, message):
        return
    if not await check_auth(client, message):
        return

    try:
        args = message.command
        if len(args) < 3:
            # 显示带目的地选择的引导提示
            await message.reply_text(
                "📥 **批量下载**\n\n"
                "**第一步：选择下载目的地**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚡ 快速合集（推荐视频）", callback_data="dl_dest_fast_collection")],
                    [InlineKeyboardButton("🔐 加密合集（私密文件）", callback_data="dl_dest_collection")],
                    [InlineKeyboardButton("📁 仅存储频道", callback_data="dl_dest_channel")],
                    [InlineKeyboardButton("⭐ 收藏夹 (Saved Messages)", callback_data="dl_dest_saved")]
                ])
            )
            return

        chat_id = int(args[1])
        start_message_id = None
        if len(args) >= 4:
            start_message_id = int(args[2])
            limit = int(args[3])
        else:
            limit = int(args[2])
        
        # 默认走快速合集；私密文件可以在菜单里改为加密合集。
        dest = user_download_dest.get(message.from_user.id, "fast_collection")
        await request_download_confirmation(client, message, chat_id, limit, dest, start_message_id=start_message_id)
        
    except Exception as e:
        await message.reply_text(f"❌ 发生严重错误: {e}")

@Client.on_callback_query(filters.regex(r"^dl_dest_(fast_collection|collection|channel|saved)$"))
async def download_dest_callback(client: Client, callback: CallbackQuery):
    """Handle destination selection."""
    if not await require_admin(client, callback, alert=True):
        return
    dest = callback.data.replace("dl_dest_", "")
    user_download_dest[callback.from_user.id] = dest
    
    dest_names = {
        "fast_collection": "⚡ 快速合集",
        "collection": "🔐 加密合集",
        "channel": "📁 仅存储频道",
        "saved": "⭐ 收藏夹",
    }
    dest_name = dest_names.get(dest, "⚡ 快速合集")
    
    from pyrogram.types import ForceReply
    await callback.message.edit_text(
        f"📥 **批量下载**\n\n"
        f"✅ 已选择目的地：{dest_name}\n\n"
        f"⚡ 快速合集：直接把文件发给你，不存储到频道。遇到禁止转发会自动下载后发给你。\n"
        f"🔐 加密合集：下载后加密存到存储频道，可用提取码随时取回。\n\n"
        f"**第二步：输入来源**\n"
        f"最简单：复制目标视频的消息链接直接发给我。\n"
        f"消息 ID 就是链接最后一段数字。\n\n"
        f"消息链接：`https://t.me/c/1234567890/4567`\n"
        f"其中频道 ID 为 `-1001234567890`，消息 ID 为 `4567`\n\n"
        f"精准下载：`频道ID 消息ID 数量`\n\n"
        f"例如：`-1001234567890 4567 1`（下载消息4567起共1条）\n"
        f"例如：`-1001234567890 4567 5`（下载消息4567起共5条）\n\n"
        f"发 `取消` 或点 **❌ 取消操作** 可退出。"
    )
    await client.send_message(
        callback.message.chat.id,
        "请发送消息链接或下载参数：",
        reply_markup=get_cancel_keyboard(callback.from_user.id == client.admin_id)
    )
    await callback.answer(f"已选择: {dest_name}")

@Client.on_callback_query(filters.regex(r"^dl(ok|no)_"))
async def download_confirm_callback(client: Client, callback: CallbackQuery):
    if not await require_admin(client, callback, alert=True):
        return

    action, job_id = callback.data.split("_", 1)
    job = pending_download_jobs.pop(job_id, None)
    if not job or job.get("user_id") != callback.from_user.id:
        await callback.answer("任务已过期，请重新发起。", show_alert=True)
        return

    if action == "dlno":
        await callback.message.edit_text("✅ 已取消下载任务。")
        await callback.answer("已取消")
        return

    await callback.answer("开始下载")

    from types import SimpleNamespace

    class MockMessage:
        def __init__(self, bot_client, user_id):
            self.chat = SimpleNamespace(id=user_id, type="private", title="User")
            self.from_user = SimpleNamespace(id=user_id, is_bot=False, username="User", first_name="")
            self._client = bot_client
            self.text = ""

        async def reply_text(self, text, **kwargs):
            return await self._client.send_message(self.chat.id, text, **kwargs)

    await callback.message.edit_text("🚀 已确认，开始创建下载任务...")
    mock_msg = MockMessage(client, callback.from_user.id)
    await do_batch_download(
        client,
        mock_msg,
        job["chat_id"],
        job["limit"],
        job["dest"],
        start_message_id=job.get("start_message_id"),
        source_name=job.get("source_name"),
    )

@Client.on_callback_query(filters.regex(r"^dlstop_"))
async def download_stop_callback(client: Client, callback: CallbackQuery):
    if not await require_admin(client, callback, alert=True):
        return
    uid = int(callback.data.split("_", 1)[1])
    if uid != callback.from_user.id:
        await callback.answer("不能取消别人的任务。", show_alert=True)
        return
    cancel_download_users.add(uid)
    await callback.answer("已请求停止，当前文件结束后会停。", show_alert=True)

def _safe_download_name(file_name, fallback):
    """Sanitize Telegram filenames for local temp paths."""
    file_name = file_name or fallback
    file_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", file_name)
    file_name = file_name.strip().strip(".")
    return file_name or fallback

def _message_media_info(target_msg):
    file_name = "unknown"
    file_size = 0
    mime_type = "application/octet-stream"

    if target_msg.video:
        file_name = target_msg.video.file_name or f"video_{target_msg.id}.mp4"
        file_size = target_msg.video.file_size
        mime_type = target_msg.video.mime_type or mime_type
    elif target_msg.document:
        file_name = target_msg.document.file_name or f"doc_{target_msg.id}"
        file_size = target_msg.document.file_size
        mime_type = target_msg.document.mime_type or mime_type
    elif target_msg.photo:
        file_name = f"photo_{target_msg.id}.jpg"
        file_size = target_msg.photo.file_size
        mime_type = "image/jpeg"
    elif target_msg.audio:
        file_name = target_msg.audio.file_name or f"audio_{target_msg.id}.mp3"
        file_size = target_msg.audio.file_size
        mime_type = target_msg.audio.mime_type or mime_type
    else:
        return None

    return _safe_download_name(file_name, f"media_{target_msg.id}"), file_size or 0, mime_type

def _download_error_hint(error):
    error_text = str(error)
    lowered = error_text.lower()
    rules = [
        (("chat_forwards_restricted", "forwards_restricted", "protected content"), "API受限：受保护内容，当前账号无法通过 Telegram API 取回原文件"),
        (("file_reference", "file reference", "file id invalid"), "文件引用过期：可稍后重试或重新定位消息 ID"),
        (("peer_id_invalid", "channel_private", "user not participant", "forbidden", "not enough rights"), "账号无权限：用户号未加入/无权访问该频道或群组"),
        (("message_id_invalid", "message empty", "media_empty", "message not found"), "消息不存在：消息 ID 错误、已删除或该条不是媒体"),
        (("timeout", "timed out", "connection", "network", "flood"), "网络/限流：连接超时或 Telegram 限流"),
        (("database is locked", "sqlite"), "本地数据库/Session锁：通常是机器人重复启动导致"),
    ]
    for markers, label in rules:
        if any(marker in lowered for marker in markers):
            return label
    return f"未知错误：{error_text[:100]}"

def _message_type_text(msg):
    if getattr(msg, "video", None):
        return "视频"
    if getattr(msg, "document", None):
        return "文件"
    if getattr(msg, "photo", None):
        return "图片"
    if getattr(msg, "audio", None):
        return "音频"
    return "消息"

def human_size(size):
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    unit_idx = 0
    while value >= 1024 and unit_idx < len(units) - 1:
        value /= 1024
        unit_idx += 1
    return f"{value:.2f} {units[unit_idx]}"

def _format_message_preview(msg):
    if not msg or getattr(msg, "empty", False):
        return "预览: `消息为空或不可访问`"
    media_info = _message_media_info(msg)
    file_line = ""
    if media_info:
        file_name, file_size, mime_type = media_info
        file_line = (
            f"类型: `{_message_type_text(msg)}`\n"
            f"文件: `{_short_text(file_name, 48)}`\n"
            f"大小: `{human_size(file_size)}`\n"
        )
    content = _message_content_preview(msg)
    content_line = f"内容: `{_short_text(content, 90)}`\n" if content else ""
    return (
        f"消息 ID: `{msg.id}`\n"
        f"时间: `{_message_time_text(msg)}`\n"
        f"发送: `{_short_text(_message_sender_name(msg), 42)}`\n"
        f"{file_line}"
        f"{content_line}"
    ).strip()

async def _parse_download_source(client, text):
    """
    Parse download input.

    Supported:
    - -1001234567890 50
    - -1001234567890 4567 1
    - https://t.me/c/1234567890/4567
    - https://t.me/channelname/4567
    - https://t.me/channelname/4567 3
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = (
        text
        .replace("\u00a0", " ")   # NBSP
        .replace("\u3000", " ")   # 全角空格
        .replace("\ufeff", "")    # BOM
        .replace("\u200b", "")    # Zero-width space
        .replace("\u200c", "")
        .replace("\u200d", "")
    ).strip()

    link_match = re.search(r"(?:https?://)?t\.me/(c/)?([^/\s]+)/(\d+)", text)
    if link_match:
        is_private = bool(link_match.group(1))
        chat_part = link_match.group(2)
        message_id = int(link_match.group(3))
        comment_match = re.search(r"[?&]comment=(\d+)", text)
        comment_id = int(comment_match.group(1)) if comment_match else None

        trailing = text[link_match.end():].strip()
        limit = 1
        if trailing:
            maybe_count = trailing.split()[0]
            if maybe_count.isdigit():
                limit = int(maybe_count)

        if is_private:
            chat_id = int(f"-100{chat_part}")
        else:
            chat = await client.user_client.get_chat(chat_part)
            chat_id = chat.id

        # 支持评论区链接：.../500?single&comment=665
        # comment 参数存在时，切换到讨论组并从评论消息 ID 开始下载
        if comment_id and not is_private:
            try:
                from pyrogram import raw

                peer = await client.user_client.resolve_peer(chat_part)
                discussion = await client.user_client.invoke(
                    raw.functions.messages.GetDiscussionMessage(
                        peer=peer,
                        msg_id=message_id,
                    )
                )
                if discussion and discussion.chats:
                    discussion_chat_id = int(f"-100{discussion.chats[0].id}")
                    return discussion_chat_id, comment_id, limit
            except Exception:
                # 兜底：如果讨论组解析失败，则继续按主贴消息处理
                pass

        return chat_id, message_id, limit

    parts = text.split()
    if len(parts) < 2:
        raise ValueError("格式错误")

    chat_id = int(parts[0])
    if len(parts) >= 3:
        return chat_id, int(parts[1]), int(parts[2])

    second_value = int(parts[1])
    if second_value > 100:
        return chat_id, second_value, 1
    return chat_id, None, second_value

def _download_dest_name(dest):
    return {
        "collection": f"📂 合集：{DEFAULT_DOWNLOAD_COLLECTION_NAME}",
        "fast_collection": "⚡ 快速合集",
        "channel": "📁 仅存储频道",
        "saved": "⭐ 收藏夹",
    }.get(dest, f"📂 合集：{DEFAULT_DOWNLOAD_COLLECTION_NAME}")

async def request_download_confirmation(client, message, chat_id, limit, dest="collection", start_message_id=None):
    import secrets
    import config

    limit = int(limit)
    if limit <= 0:
        await message.reply_text("❌ 下载数量必须大于 0。")
        return

    mode_text = (
        f"精准下载：从消息 `{start_message_id}` 往前取 `{limit}` 条"
        if start_message_id
        else f"扫描最近媒体：最多下载 `{limit}` 个媒体"
    )
    source_name = "未知"
    source_type = "未知"
    source_error = ""
    preview_text = ""
    try:
        chat = await client.user_client.get_chat(chat_id)
        source_name = chat.title or chat.first_name or chat.username or str(chat.id)
        source_type = str(chat.type).replace("ChatType.", "")
    except Exception as e:
        source_error = f"\n⚠️ 名称解析失败: `{_short_text(str(e), 80)}`"

    if start_message_id:
        try:
            preview_msg = await client.user_client.get_messages(chat_id, start_message_id)
            preview_text = "\n\n👀 **下载前预览**\n" + _format_message_preview(preview_msg)
        except Exception as e:
            preview_text = f"\n\n👀 **下载前预览**\n预览失败: `{_download_error_hint(e)}`"

    job_id = secrets.token_urlsafe(6).replace("_", "").replace("-", "")[:8]
    pending_download_jobs[job_id] = {
        "user_id": message.from_user.id,
        "chat_id": chat_id,
        "limit": limit,
        "dest": dest,
        "start_message_id": start_message_id,
        "source_name": source_name,
        "source_type": source_type,
    }

    second_number_tip = (
        "如果第二个数字其实是消息 ID，请取消后输入：`频道ID 消息ID 1`。\n\n"
        if not start_message_id else ""
    )
    await message.reply_text(
        "⚠️ **请确认下载任务**\n\n"
        f"来源名称: **{_short_text(source_name, 48)}**\n"
        f"来源类型: `{source_type}`\n"
        f"来源 ID: `{chat_id}`\n"
        f"模式: {mode_text}\n"
        f"目的地: {_download_dest_name(dest)}\n\n"
        f"{preview_text}\n\n"
        f"{second_number_tip}"
        "确认无误后再开始，避免输错 ID 后批量下载大量文件。"
        f"{source_error}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认开始", callback_data=f"dlok_{job_id}")],
            [InlineKeyboardButton("❌ 取消", callback_data=f"dlno_{job_id}")]
        ])
    )

async def do_batch_download(client, message, chat_id, limit, dest="collection", start_message_id=None, source_name=None, task_key=None):
    """Core download logic."""
    # Use User Client (Admin's account) for downloading
    user = client.user_client
    
    # 确定目的地
    from handlers.transfer import progress, humanbytes, config, db, os, time, math
    
    user_id = message.from_user.id
    cancel_download_users.discard(user_id)
    default_collection = None
    task_key = task_key or _generate_collection_key("task_")[:18]
    source_name = source_name or str(chat_id)
    try:
        if not source_name or source_name == str(chat_id):
            chat = await user.get_chat(chat_id)
            source_name = chat.title or chat.first_name or chat.username or str(chat.id)
    except Exception:
        pass

    if dest == "saved":
        # 发送到用户的 Saved Messages，用 user client
        target_chat_id = "me"
        dest_name = "⭐ 收藏夹"
        send_client = user
    elif dest == "collection":
        default_collection = create_download_task_collection(message.from_user.id, source_name)
        if not default_collection:
            await message.reply_text("❌ 无法创建本次下载合集，请稍后重试。")
            return

        target_chat_id = config.STORAGE_CHANNEL_ID
        dest_name = f"📂 合集：{default_collection['name']}"
        send_client = client
    elif dest == "fast_collection":
        default_collection = create_download_task_collection(message.from_user.id, source_name)
        if not default_collection:
            await message.reply_text("❌ 无法创建本次快速合集，请稍后重试。")
            return

        target_chat_id = config.STORAGE_CHANNEL_ID
        dest_name = f"⚡ 快速合集：{default_collection['name']}"
        send_client = user
    else:
        target_chat_id = config.STORAGE_CHANNEL_ID
        dest_name = _download_dest_name(dest)
        send_client = client
    
    mode_text = f"从消息 `{start_message_id}` 往前精准读取 {limit} 条" if start_message_id else f"扫描最后 {limit} 个媒体"
    try:
        db.create_download_task(
            task_key=task_key,
            owner_id=user_id,
            source_chat_id=chat_id,
            source_title=source_name,
            start_message_id=start_message_id,
            limit_count=limit,
            dest=dest,
            collection_id=default_collection["id"] if default_collection else None,
            collection_key=default_collection["access_key"] if default_collection else None,
            status="running",
        )
    except Exception as e:
        print(f"Create download task record failed: {e}")

    status_msg = await message.reply_text(
        f"🚀 开始处理频道 `{chat_id}`\n"
        f"📌 任务: `{task_key}`\n"
        f"🔎 模式: {mode_text}\n"
        f"📍 目的地: {dest_name}"
    )
    
    # Get history with error handling
    try:
        # 先尝试解析 peer
        try:
            await user.get_chat(chat_id)
        except:
            pass
        
        messages_to_process = []
        scan_count = 0

        if start_message_id:
            # 精准模式：从指定消息 ID 往前取 N 条消息。
            first_id = max(1, start_message_id - limit + 1)
            message_ids = list(range(start_message_id, first_id - 1, -1))
            try:
                fetched = await user.get_messages(chat_id, message_ids)
            except Exception as e_get:
                # 直接获取失败，尝试通过讨论区 API 兜底（适用于评论区绑定群组但不允许加入的情况）
                err_str = str(e_get)
                if any(k in err_str for k in ("CHANNEL_PRIVATE", "USER_NOT_PARTICIPANT", "PEER_ID_INVALID", "CHAT_FORBIDDEN")):
                    await status_msg.edit_text("⚠️ 直接访问失败，尝试通过讨论区 API 获取...")
                    try:
                        from pyrogram import raw
                        peer = await user.resolve_peer(chat_id)
                        # 用第一个消息 ID 尝试获取讨论群信息
                        disc_result = await user.invoke(
                            raw.functions.messages.GetDiscussionMessage(
                                peer=peer,
                                msg_id=start_message_id
                            )
                        )
                        if disc_result and disc_result.chats:
                            discussion_chat = disc_result.chats[0]
                            disc_chat_id = int("-100" + str(discussion_chat.id))
                            # 重新构造讨论群的消息 ID 列表
                            # GetDiscussionMessage 返回的消息在讨论群中的 ID
                            disc_msg_id = disc_result.messages[0].id if disc_result.messages else start_message_id
                            disc_first_id = max(1, disc_msg_id - limit + 1)
                            disc_message_ids = list(range(disc_msg_id, disc_first_id - 1, -1))
                            fetched = await user.get_messages(disc_chat_id, disc_message_ids)
                            # 更新 chat_id 为讨论群 ID，后续下载用
                            chat_id = disc_chat_id
                        else:
                            raise Exception("讨论区 API 未返回群组信息")
                    except Exception as e_disc:
                        raise Exception(f"直接访问失败: {err_str} | 讨论区兜底也失败: {e_disc}")
                else:
                    raise e_get
            if not isinstance(fetched, list):
                fetched = [fetched]
            scan_count = len(fetched)
            messages_to_process = [
                msg for msg in fetched
                if msg and not getattr(msg, "empty", False) and msg.media
            ]
        else:
            # Scan up to 500 messages or 10x the limit to find media
            max_scan = max(500, limit * 10)

            try:
                async for msg in user.get_chat_history(chat_id):
                    scan_count += 1
                    if msg.media:
                        messages_to_process.append(msg)

                    if len(messages_to_process) >= limit:
                        break

                    if scan_count >= max_scan:
                        break
            except Exception as e_hist:
                # get_chat_history 失败（非成员），扫描模式无法通过讨论区 API 兜底
                # 提示用户改用精准模式（需要指定消息 ID）
                err_str = str(e_hist)
                if any(k in err_str for k in ("CHANNEL_PRIVATE", "USER_NOT_PARTICIPANT", "PEER_ID_INVALID", "CHAT_FORBIDDEN")):
                    raise Exception(
                        f"{err_str}\n\n"
                        f"💡 提示：该群组不允许加入，无法扫描历史消息。\n"
                        f"请改用精准模式：`频道ID 消息ID 数量`\n"
                        f"例如：`-1001234567890 4567 5`"
                    )
                else:
                    raise e_hist
                
    except Exception as e:
        error_msg = str(e)
        from handlers.setup import get_main_menu_keyboard
        is_adm = message.from_user.id == client.admin_id
        
        # Try to delete status message to clean up
        try: await status_msg.delete()
        except: pass
        
        if "PEER_ID_INVALID" in error_msg:
            await message.reply_text(
                f"❌ 无法访问该对话！\n\n"
                f"错误: `PEER_ID_INVALID`\n\n"
                f"**这个 ID ({chat_id}) 在你的账号里找不到。**\n\n"
                f"可能原因：\n"
                f"1. 你已经删除了和这个账号的聊天记录\n"
                f"2. 这个账号从未给你发过消息\n"
                f"3. 需要先在 Telegram 里打开那个聊天",
                reply_markup=get_main_menu_keyboard(is_adm)
            )
        else:
            await message.reply_text(f"❌ 无法访问该频道！\n\n错误: `{_download_error_hint(e)}`", reply_markup=get_main_menu_keyboard(is_adm))
        db.update_download_task(task_key, status="failed", error_summary=_download_error_hint(e))
        return
    
    if not messages_to_process:
        from handlers.setup import get_main_menu_keyboard
        is_adm = message.from_user.id == client.admin_id
        try: await status_msg.delete()
        except: pass
        help_text = "请确认消息 ID 是否正确，或把数量设为 1 精准下载该条消息。" if start_message_id else "可以改用 `频道ID 消息ID 数量` 精准模式。"
        await message.reply_text(f"❌ 未找到包含媒体文件的消息 (已检查 {scan_count} 条)。\n\n{help_text}", reply_markup=get_main_menu_keyboard(is_adm))
        db.update_download_task(task_key, status="failed", error_summary=f"未找到媒体，已检查 {scan_count} 条")
        return

    # Initialize Dashboard
    dashboard_msg = await status_msg.edit_text(
        f"🚀 **批量下载任务启动**\n"
        f"📌 任务: `{task_key}`\n"
        f"📦 目标: `{dest_name}`\n"
        f"📊 进度: 0/{len(messages_to_process)}\n"
        f"⏳ 正在初始化...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛑 停止任务", callback_data=f"dlstop_{user_id}")]
        ])
    )
    
    import secrets
    import string
    import base64
    from services.crypto_utils import generate_key, encrypt_file
    
    success_count = 0
    fail_count = 0
    fail_reasons = []
    success_keys = []
    total_count = len(messages_to_process)
    
    # Process from oldest to newest
    for index, target_msg in enumerate(reversed(messages_to_process)):
        if user_id in cancel_download_users:
            fail_reasons.append("用户已手动停止任务")
            db.update_download_task(
                task_key,
                status="stopped",
                success_count=success_count,
                fail_count=fail_count,
                error_summary="用户已手动停止任务",
            )
            break

        current_idx = index + 1
        
        try:
            media_info = _message_media_info(target_msg)
            if not media_info:
                continue
            file_name, file_size, mime_type = media_info

            try:
                await dashboard_msg.edit_text(
                    f"🚀 **批量下载任务**\n"
                    f"📦 目标: `{dest_name}`\n"
                    f"🔄 正在处理: `{file_name}`\n"
                    f"📊 进度: {current_idx}/{total_count} | ✅ {success_count} | ❌ {fail_count}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🛑 停止任务", callback_data=f"dlstop_{user_id}")]
                    ])
                )
            except: pass

            if dest == "fast_collection":
                # ⚡ 快速模式：直接下载后发给用户，不存储到频道
                # 先尝试 copy_message 直接发给用户（无需下载，速度最快）
                send_ok = False
                copy_hint = ""
                try:
                    await user.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=chat_id,
                        message_id=target_msg.id,
                    )
                    send_ok = True
                except Exception as copy_err:
                    copy_hint = _download_error_hint(copy_err)

                if send_ok:
                    success_count += 1
                    db.update_download_task(
                        task_key,
                        status="running",
                        success_count=success_count,
                        fail_count=fail_count,
                        last_message_id=getattr(target_msg, "id", None),
                    )
                    continue

                # copy 失败（禁止转发）→ 下载后直接发给用户
                try:
                    await dashboard_msg.edit_text(
                        f"🚀 **批量下载任务**\n"
                        f"📦 目标: 直接发给你\n"
                        f"⬇️ 受保护内容，正在下载: `{file_name}`\n"
                        f"📊 进度: {current_idx}/{total_count} | ✅ {success_count} | ❌ {fail_count}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🛑 停止任务", callback_data=f"dlstop_{user_id}")]
                        ])
                    )
                except: pass

                temp_dir = config.TEMP_DOWNLOAD_DIR
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir, exist_ok=True)

                safe_name = _safe_download_name(file_name, f"media_{target_msg.id}")
                dl_path = await user.download_media(target_msg, file_name=os.path.join(temp_dir, safe_name))

                if not dl_path or not os.path.exists(dl_path) or os.path.getsize(dl_path) < 1:
                    fail_count += 1
                    actual_size = os.path.getsize(dl_path) if dl_path and os.path.exists(dl_path) else 0
                    fail_reasons.append(f"#{target_msg.id}: 下载失败 ({actual_size}B)，原因: {copy_hint}")
                    try:
                        if dl_path and os.path.exists(dl_path):
                            os.remove(dl_path)
                    except: pass
                    db.update_download_task(
                        task_key,
                        success_count=success_count,
                        fail_count=fail_count,
                        last_message_id=getattr(target_msg, "id", None),
                        error_summary="\n".join(fail_reasons[-3:]),
                    )
                    continue

                # 直接发给用户（按媒体类型发送，尽量保持 Telegram 原生播放形态）
                send_caption = _message_content_preview(target_msg) or ""
                try:
                    if target_msg.video:
                        await client.send_video(
                            message.chat.id,
                            dl_path,
                            caption=send_caption[:1024] if send_caption else None,
                            supports_streaming=True,
                            file_name=file_name,
                        )
                    elif target_msg.photo:
                        await client.send_photo(
                            message.chat.id,
                            dl_path,
                            caption=send_caption[:1024] if send_caption else None,
                        )
                    elif target_msg.audio:
                        await client.send_audio(
                            message.chat.id,
                            dl_path,
                            caption=send_caption[:1024] if send_caption else None,
                            file_name=file_name,
                        )
                    elif target_msg.voice:
                        await client.send_voice(
                            message.chat.id,
                            dl_path,
                        )
                    else:
                        # 其他类型仍按文件发送
                        await client.send_document(
                            message.chat.id,
                            dl_path,
                            caption=send_caption[:1024] if send_caption else None,
                            force_document=True,
                            file_name=file_name,
                        )
                    send_ok = True
                except Exception as send_err:
                    fail_count += 1
                    fail_reasons.append(f"#{target_msg.id}: 发送失败: {_download_error_hint(send_err)}")
                    db.update_download_task(
                        task_key,
                        success_count=success_count,
                        fail_count=fail_count,
                        last_message_id=getattr(target_msg, "id", None),
                        error_summary="\n".join(fail_reasons[-3:]),
                    )
                finally:
                    try:
                        if dl_path and os.path.exists(dl_path):
                            os.remove(dl_path)
                    except: pass

                if send_ok:
                    success_count += 1
                    db.update_download_task(
                        task_key,
                        status="running",
                        success_count=success_count,
                        fail_count=fail_count,
                        last_message_id=getattr(target_msg, "id", None),
                    )
                continue

            # 1. Download
            temp_dir = config.TEMP_DOWNLOAD_DIR
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir, exist_ok=True)
                
            dl_path = await user.download_media(target_msg, file_name=os.path.join(temp_dir, file_name))
            
            if not dl_path or not os.path.exists(dl_path) or os.path.getsize(dl_path) < 1024:
                fail_count += 1
                actual_size = os.path.getsize(dl_path) if dl_path and os.path.exists(dl_path) else 0
                fail_reasons.append(f"#{target_msg.id}: 下载得到异常小文件 ({actual_size}B)，疑似 API 受限/下载中断")
                try:
                    if dl_path and os.path.exists(dl_path):
                        os.remove(dl_path)
                except: pass
                db.update_download_task(
                    task_key,
                    success_count=success_count,
                    fail_count=fail_count,
                    last_message_id=getattr(target_msg, "id", None),
                    error_summary="\n".join(fail_reasons[-3:]),
                )
                continue

            # 2. Encrypt
            aes_key = generate_key()
            aes_key_b64 = base64.b64encode(aes_key).decode('utf-8')
            
            random_name = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
            enc_path = os.path.join(os.path.dirname(dl_path), f"{random_name}.bin")
            
            await asyncio.to_thread(encrypt_file, dl_path, enc_path, aes_key)
            
            # Clean raw file
            try: os.remove(dl_path)
            except: pass
            
            # 3. Upload (Dual)
            caption = f"📦 {file_name}\n🔒 [AES-256 Encrypted]"
            
            # Primary Upload
            primary_msg = await send_client.send_document(
                target_chat_id,
                enc_path,
                caption=caption
            )
            
            # Backup Upload
            backup_msg_id = 0
            backup_chat_id = 0
            if dest in ("channel", "collection") and config.BACKUP_CHANNEL_ID and config.BACKUP_CHANNEL_ID != 0:
                try:
                    # Prefer using storage_client for backup if possible, or same client
                    backup_uploader = client.storage_client if hasattr(client, 'storage_client') else send_client
                    b_msg = await backup_uploader.send_document(
                        config.BACKUP_CHANNEL_ID,
                        enc_path, 
                        caption=caption + " [Backup]"
                    )
                    backup_msg_id = b_msg.id
                    backup_chat_id = config.BACKUP_CHANNEL_ID
                except Exception as e:
                    print(f"Backup upload failed: {e}")
            
            # 4. DB Record
            key_length = secrets.randbelow(17) + 16
            access_key = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(key_length))
            
            if primary_msg and primary_msg.document:
                if dest == "saved":
                    success_count += 1
                    db.update_download_task(
                        task_key,
                        status="running",
                        success_count=success_count,
                        fail_count=fail_count,
                        last_message_id=getattr(target_msg, "id", None),
                    )
                    try: os.remove(enc_path)
                    except: pass
                    continue

                new_file_id = db.add_file(
                    message_id=primary_msg.id,
                    chat_id=target_chat_id,
                    file_id=primary_msg.document.file_id,
                    file_unique_id=primary_msg.document.file_unique_id,
                    file_name=file_name,
                    caption="",
                    file_size=file_size,
                    mime_type=mime_type,
                    storage_mode='telegram_stealth',
                    access_key=access_key,
                    is_encrypted=True,
                    encryption_key=aes_key_b64,
                    backup_message_id=backup_msg_id,
                    backup_chat_id=backup_chat_id
                )
                if dest == "collection":
                    db.add_file_to_collection(default_collection["id"], new_file_id)
                success_keys.append((file_name, access_key))
                success_count += 1
                db.update_download_task(
                    task_key,
                    status="running",
                    success_count=success_count,
                    fail_count=fail_count,
                    last_message_id=getattr(target_msg, "id", None),
                )
            else:
                fail_count += 1
                fail_reasons.append(f"#{target_msg.id}: 上传后未返回 document")
                db.update_download_task(
                    task_key,
                    success_count=success_count,
                    fail_count=fail_count,
                    last_message_id=getattr(target_msg, "id", None),
                    error_summary="\n".join(fail_reasons[-3:]),
                )
            
            # Clean enc file
            try: os.remove(enc_path)
            except: pass
            
        except Exception as e:
            fail_count += 1
            hint = _download_error_hint(e)
            fail_reasons.append(f"#{getattr(target_msg, 'id', '?')}: {hint}")
            db.update_download_task(
                task_key,
                success_count=success_count,
                fail_count=fail_count,
                last_message_id=getattr(target_msg, "id", None),
                error_summary="\n".join(fail_reasons[-3:]),
            )
            print(f"Batch file error: {e}")

    reason_text = ""
    if fail_reasons:
        reason_text = "\n\n⚠️ **失败明细（前5条）**\n" + "\n".join(f"- {item}" for item in fail_reasons[:5])

    access_tip = ""
    if dest == "collection" and default_collection:
        # 加密合集：需要密钥才能提取
        access_tip = (
            f"\n\n🔑 **提取码 / 合集密钥**\n"
            f"`{default_collection['access_key']}`\n"
            f"你之后直接把这个密钥发给机器人，就能提取本次下载归档。"
        )
    elif dest == "fast_collection":
        # 快速合集：文件已直接发给你，无需密钥
        access_tip = f"\n\n✅ 文件已直接发送给你，无需提取码。"
    elif dest == "channel" and success_keys:
        shown = "\n".join(
            f"- `{key}` | {_short_text(name, 24)}"
            for name, key in success_keys[:10]
        )
        more = f"\n...还有 {len(success_keys) - 10} 个未显示" if len(success_keys) > 10 else ""
        access_tip = f"\n\n🔑 **单文件提取码（前10个）**\n{shown}{more}"

    await dashboard_msg.edit_text(
        f"✅ **批量任务结束**\n"
        f"📌 任务: `{task_key}`\n"
        f"📊 总数: {total_count}\n"
        f"✅ 成功: {success_count}\n"
        f"❌ 失败: {fail_count}\n"
        f"📂 目的地: {dest_name}"
        f"{access_tip}"
        f"{reason_text}"
    )
    
    collection_tip = ""
    if dest == "collection" and default_collection:
        # 加密合集：需要密钥提取
        collection_tip = (
            f"\n🔑 提取码 / 合集密钥: `{default_collection['access_key']}`\n"
            f"把这个密钥发给机器人即可提取。"
        )
    elif dest == "fast_collection":
        # 快速合集：文件已直接发给你，无需密钥
        collection_tip = f"\n✅ 文件已直接发送给你，无需提取码。"
    elif dest == "channel" and success_keys:
        first_name, first_key = success_keys[0]
        collection_tip = f"\n🔑 首个文件提取码: `{first_key}`"

    await message.reply_text(f"🎉 **批量任务结束！**\n共处理: {total_count}\n成功: {success_count}\n目的地: {dest_name}{collection_tip}")
    if fail_reasons and fail_reasons[-1] == "用户已手动停止任务":
        final_status = "stopped"
    elif success_count == 0 and fail_count > 0:
        final_status = "failed"
    else:
        final_status = "completed"
    db.update_download_task(
        task_key,
        status=final_status,
        success_count=success_count,
        fail_count=fail_count,
        error_summary="\n".join(fail_reasons[-5:]) if fail_reasons else None,
        collection_id=default_collection["id"] if default_collection else None,
        collection_key=default_collection["access_key"] if default_collection else None,
    )
    cancel_download_users.discard(user_id)


def _short_text(text, max_len=36):
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"

def _message_content_preview(msg):
    return (
        getattr(msg, "caption", None)
        or getattr(msg, "text", None)
        or getattr(msg, "media_caption", None)
        or ""
    )

def _message_sender_name(msg):
    if getattr(msg, "from_user", None):
        user = msg.from_user
        name = " ".join(part for part in [user.first_name, user.last_name] if part)
        if user.username:
            return f"{name or user.username} (@{user.username})"
        return name or str(user.id)
    if getattr(msg, "sender_chat", None):
        return msg.sender_chat.title or str(msg.sender_chat.id)
    return "未知发送者"

def _message_time_text(msg):
    if not getattr(msg, "date", None):
        return "未知时间"
    return msg.date.strftime("%m-%d %H:%M")

# ========== 合集功能 ==========

@Client.on_message(filters.command("newcollection") & filters.private)
async def create_collection_cmd(client: Client, message: Message):
    """创建新合集，自动生成密钥"""
    from database import db
    from pyrogram.types import ForceReply
    import secrets
    import string
    
    args = message.text.split(maxsplit=1)
    
    # Handle Button Trigger
    if message.text == "🆕 新建合集" or len(args) < 2:
        await message.reply_text(
            "📁 **创建合集**\n\n"
            "请输入合集名称：\n"
            "（例如：我的电影）\n\n"
            "💡 发送 /cancel 可取消",
            reply_markup=ForceReply(placeholder="输入合集名称...")
        )
        return
    
    # Check if args[0] is command (ignore /newcollection)
    # If standard command: /newcollection Name -> args[1] = Name
    # If triggered by text button? "🆕 新建合集" handled above.
    
    collection_name = args[1]
    
    # Do Create
    await do_create_collection(client, message, collection_name)

async def do_create_collection(client, message, name):
    """创建合集的实际逻辑"""
    from database import db
    import secrets
    import string
    
    owner_id = message.from_user.id
    
    # 自动生成密钥：file_store + 16-32位随机字符
    random_length = secrets.randbelow(17) + 16
    random_chars = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(random_length))
    access_key = f"file_store{random_chars}"
    
    collection_id = db.create_collection(name, access_key, owner_id)
    
    if collection_id:
        # 进入收集模式
        # 进入收集模式
        sent_msg = await message.reply_text(
            f"✅ **合集 [{name}] 创建成功！**\n\n"
            f"🔑 密钥: `{access_key}`\n\n"
            f"📥 **现在进入收集模式！**\n"
            f"• 直接批量转发文件给我\n"
            f"• 我会静默添加到此合集\n"
            f"• 状态将实时更新在此消息中\n"
            f"• 发 **结束** 完成收集\n\n"
            f"⏳ 等待文件..."
        )
        
        user_collecting_mode[owner_id] = {
            "collection_id": collection_id,
            "collection_name": name,
            "access_key": access_key,
            "files": [],
            "status_msg_id": sent_msg.id,
            "status_chat_id": sent_msg.chat.id,
            "success": 0,
            "total": 0,
            "fail": 0,
            "last_update": 0
        }
    else:
        await message.reply_text("❌ 创建失败！请重试。")

@Client.on_message(filters.regex(r"^(结束|finish|完成)$", re.IGNORECASE) & filters.private)
async def finish_collection_cmd(client: Client, message: Message):
    """结束收集模式"""
    user_id = message.from_user.id
    if user_id in user_collecting_mode:
        mode = user_collecting_mode.pop(user_id)
        
        # 最终汇总
        try:
            # 尝试更新 Dashboard 为最终状态
            await client.edit_message_text(
                chat_id=mode['status_chat_id'],
                message_id=mode['status_msg_id'],
                text=(
                    f"✅ **合集 [{mode['collection_name']}] 收集完成！**\n\n"
                    f"📊 总共: {mode['total']} | ✅ 成功: {mode['success']} | ❌ {mode['fail']}\n"
                    f"🔑 密钥: `{mode['access_key']}`"
                )
            )
        except: pass
        
        await message.reply_text(
            f"🎉 **任务结束！**\n"
            f"已退出收集模式。"
        )

@Client.on_message(filters.command("addto") & filters.private & filters.reply)
async def add_to_collection_cmd(client: Client, message: Message):
    """添加文件到合集（需回复文件消息）"""
    from database import db
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text(
            "📁 **添加到合集**\n\n"
            "用法: 回复一条文件消息，发送 `/addto 合集名`"
        )
        return
    
    collection_name = args[1]
    owner_id = message.from_user.id
    
    collection = db.get_collection_by_name(collection_name, owner_id)
    if not collection:
        await message.reply_text(f"❌ 找不到合集 **{collection_name}**\n\n用 `/mycollections` 查看你的合集。")
        return
    
    replied = message.reply_to_message
    if not replied:
        await message.reply_text("❌ 请回复一条文件消息。")
        return
    
    file_id = None
    if replied.video:
        file_id = replied.video.file_id
    elif replied.photo:
        file_id = replied.photo.file_id
    elif replied.document:
        file_id = replied.document.file_id
    elif replied.audio:
        file_id = replied.audio.file_id
    
    if not file_id:
        await message.reply_text("❌ 回复的消息不包含文件。")
        return
    
    db.cursor.execute('SELECT id FROM files WHERE file_id = ?', (file_id,))
    row = db.cursor.fetchone()
    
    if not row:
        await message.reply_text("❌ 这个文件还没有入库。请先转发文件给机器人。")
        return
    
    if db.add_file_to_collection(collection["id"], row[0]):
        await message.reply_text(f"✅ 已添加到合集 **{collection_name}**！")
    else:
        await message.reply_text("❌ 添加失败，可能文件已在合集中。")

@Client.on_message(filters.command("mycollections") & filters.private)
async def my_collections_cmd(client: Client, message: Message):
    # === Terms Check ===
    from database import db
    user = db.get_user(message.from_user.id)
    # 强制显示免责声明 (如果未接受 OR 用户读取失败)
    if not user or not user.get('accepted_terms'):
        s_text = (
            "📜 **免责声明 (Disclaimer)**\n\n"
            "1. 本机器人仅用于个人数据备份与管理，代码开源且透明。\n"
            "2. 用户需自行承担使用本工具产生的一切后果。\n"
            "3. 请勿利用本工具存储或传播任何违反当地法律法规的内容。\n\n"
            "点击下方按钮代表你已阅读并同意以上条款。"
        )
        # Assuming InlineKeyboardMarkup and InlineKeyboardButton are imported or available
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        await message.reply_text(
            s_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ 我已阅读并同意", callback_data="accept_terms")]
            ])
        )
        return
    
    owner_id = message.from_user.id
    collections = db.get_user_collections(owner_id)
    
    if not collections:
        await message.reply_text(
            "📁 **你还没有创建任何合集**\n\n"
            "用 `/newcollection 名称` 创建一个！"
        )
        return
    
    output = "📁 **我的合集**\n\n"
    for c in collections:
        output += f"• **{c['name']}**\n"
        output += f"  🔑 密钥: `{c['access_key']}`\n"
        output += f"  📄 文件: {c['file_count']} 个\n\n"
    
    output += "💡 分享密钥给他人，他们直接发送密钥即可获取合集。"
    await message.reply_text(output)

async def send_collection_files(client: Client, message: Message, files: list, collection_name: str, edit_msg=None):
    """
    发送合集文件（核心逻辑抽离）
    优化：使用临时目录，每发送一批就立即清理，防止磁盘爆满
    """
    import config
    
    if edit_msg:
        status_msg = edit_msg
        await status_msg.edit_text(f"📁 **{collection_name}**\n准备发送 {len(files)} 个文件...")
    else:
        has_encrypted_files = any(f.get('is_encrypted') for f in files)
        prepare_text = "正在准备下载与解密..." if has_encrypted_files else "正在快速发送..."
        status_msg = await message.reply_text(
            f"📁 **{collection_name}**\n"
            f"共 {len(files)} 个文件，{prepare_text}"
        )
    
    from pyrogram.types import InputMediaPhoto, InputMediaVideo
    import os
    import asyncio
    from services.crypto_utils import decrypt_file
    import base64
    
    # 使用临时目录
    temp_dir = getattr(config, 'TEMP_DOWNLOAD_DIR', './temp')
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)
    
    media_group = []
    batch_temp_paths = []  # 当前批次的临时文件
    storage_client = getattr(client, 'storage_client', client)
    sent_count = 0
    
    async def cleanup_batch(paths):
        """清理一批临时文件"""
        for p in paths:
            if p and os.path.exists(p):
                try: os.remove(p)
                except: pass
    
    async def send_and_cleanup_batch():
        """发送当前媒体组并清理临时文件"""
        nonlocal media_group, batch_temp_paths, sent_count
        if media_group:
            await client.send_media_group(message.chat.id, media_group)
            sent_count += len(media_group)
            media_group = []
        # 立即清理本批次临时文件
        await cleanup_batch(batch_temp_paths)
        batch_temp_paths = []
    
    for idx, f in enumerate(files):
        try:
            local_path = None
            is_video = False
            is_image = False
            
            mime = (f.get('mime_type') or "").lower()
            fname = (f.get('file_name') or "").lower()
            if mime.startswith('image') or fname.endswith(('.jpg', '.jpeg', '.png', '.webp', '.heic')):
                is_image = True
            elif mime.startswith('video') or fname.endswith(('.mp4', '.mov', '.avi', '.mkv')):
                is_video = True
            
            if f.get('is_encrypted'):
                enc_msg = await storage_client.get_messages(f["chat_id"], f["message_id"])
                
                is_valid = enc_msg and not enc_msg.empty and enc_msg.document
                
                if not is_valid:
                    b_cid = f.get('backup_chat_id', 0)
                    b_mid = f.get('backup_message_id', 0)
                    if b_cid and b_mid:
                         try:
                             enc_msg = await storage_client.get_messages(b_cid, b_mid)
                         except: pass

                if not enc_msg or enc_msg.empty: continue
                
                try:
                    dl_path = await storage_client.download_media(enc_msg, file_name=os.path.join(temp_dir, f"enc_{f['id']}"))
                    batch_temp_paths.append(dl_path)
                except: continue

                if not dl_path: continue

                dec_path = os.path.join(temp_dir, f"dec_{f['id']}_{f['file_name']}")
                aes_key = base64.b64decode(f["encryption_key"])
                
                try:
                    await asyncio.to_thread(decrypt_file, dl_path, dec_path, aes_key)
                    local_path = dec_path
                    batch_temp_paths.append(dec_path)
                except: continue
                    
            else:
                await send_and_cleanup_batch()
                try:
                    await client.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=f["chat_id"],
                        message_id=f["message_id"],
                    )
                    sent_count += 1
                    continue
                except Exception:
                    try:
                        await client.send_cached_media(
                            message.chat.id,
                            f["file_id"],
                            caption=f.get("caption") or "",
                        )
                        sent_count += 1
                        continue
                    except Exception:
                        msg = await storage_client.get_messages(f["chat_id"], f["message_id"])
                        dl_path = await storage_client.download_media(msg, file_name=os.path.join(temp_dir, f"plain_{f['id']}"))
                        local_path = dl_path
                        batch_temp_paths.append(local_path)
            
            if not local_path or not os.path.exists(local_path):
                continue

            caption = f['caption'] or ""
            
            if is_image:
                media_group.append(InputMediaPhoto(local_path, caption=caption))
            elif is_video:
                media_group.append(InputMediaVideo(local_path, caption=caption))
            else:
                # 文档：先发送当前媒体组，再单独发送文档
                await send_and_cleanup_batch()
                await client.send_document(message.chat.id, local_path, caption=caption, file_name=f['file_name'])
                sent_count += 1
                # 单独清理这个文档的临时文件
                await cleanup_batch([local_path])

            # 每10个媒体项发送一批并立即清理
            if len(media_group) >= 10:
                await send_and_cleanup_batch()
        
        except Exception as e:
            print(f"Error processing file {f.get('id')}: {e}")
    
    # 发送剩余的媒体组
    await send_and_cleanup_batch()
        
    await status_msg.edit_text(f"✅ 合集 **{collection_name}** 发送完成！共 {sent_count} 个文件。")
    return status_msg

def make_pagination_keyboard(total_pages, current_page, callback_prefix, extra_buttons=None):
    """
    生成分页键盘 (10页一组)
    callback_prefix: 例如 "col_pg_KEY_" (后面接页码)
    """
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = []
    
    # 1. 功能按钮 (放在最上面)
    if extra_buttons:
        for btn_row in extra_buttons:
            buttons.append(btn_row)

    # 2. 翻页导航 (Prev/Next)
    nav_row = []
    if current_page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{callback_prefix}{current_page-1}"))
    if current_page < total_pages:
        nav_row.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"{callback_prefix}{current_page+1}"))
    if nav_row:
        buttons.append(nav_row)
        
    # 3. 页码网格 (10页)
    # 计算当前显示的10页范围 (例如 Page 1 -> 1-10)
    start_num = ((current_page - 1) // 10) * 10 + 1
    end_num = min(start_num + 9, total_pages)
    
    page_buttons = []
    row = []
    for p in range(start_num, end_num + 1):
        # 高亮当前页
        text = f"· {p} ·" if p == current_page else str(p)
        row.append(InlineKeyboardButton(text, callback_data=f"{callback_prefix}{p}"))
        if len(row) == 5:
            page_buttons.append(row)
            row = []
    if row:
        page_buttons.append(row)
            
    buttons.extend(page_buttons)
            
    return InlineKeyboardMarkup(buttons)

async def show_collection_page(client, message, collection, files, page=1, is_callback=False, send_new=False):
    """显示合集的分页内容 (Smart Pagination)
    :param send_new: 如果为 True，强制发送新消息（用于 Floating Menu 效果）
    """
    from pyrogram.types import InlineKeyboardButton
    
    per_page = 10
    total_files = len(files)
    total_pages = max(1, (total_files + per_page - 1) // per_page)
    
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_files = files[start_idx:end_idx]
    
    # 1. 构建文本内容 (精简版)
    text = f"📁 **{collection['name']}**\n"
    text += f"📊 共 {total_files} 个文件 (第 {page}/{total_pages} 页)\n"
    text += f"-------------------------\n"
    text += f"🔑 提取码: `{collection['access_key']}`"

    # 2. 构建按钮 (使用 Smart Pagination)
    extra_btns = []
    # 发送本页
    extra_btns.append([InlineKeyboardButton(f"⬇️ 发送本页 ({len(page_files)}个)", callback_data=f"col_dl_{collection['access_key']}_{page}")])
    # 发送全部 (智能: 发送剩余)
    remaining_count = max(0, total_files - 10)
    if remaining_count > 0:
        extra_btns.append([InlineKeyboardButton(f"🚀 发送剩余 ({remaining_count}个 - 慎点)", callback_data=f"col_all_{collection['access_key']}")])
    
    keyboard = make_pagination_keyboard(
        total_pages, 
        page, 
        f"col_pg_{collection['access_key']}_",
        extra_buttons=extra_btns
    )
    
    try:
        if send_new:
            # Floating Menu: 发送新消息
            await client.send_message(message.chat.id, text, reply_markup=keyboard, disable_web_page_preview=True)
        elif is_callback:
            await message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
        else:
            await message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)
    except: pass
async def handle_collection_key(client: Client, message: Message, key: str):
    """通过密钥获取合集文件"""
    from database import db
    
    collection = db.get_collection_by_key(key)
    
    # === 情景1: 是合集密钥 ===
    if collection:
        files = db.get_collection_files(collection["id"])
        if not files:
            await message.reply_text(f"📁 合集 **{collection['name']}** 还没有文件。")
            return True
        
        # 超过10个，显示分页菜单
        # 超过10个，显示分页菜单
        if len(files) > 10:
            # 自动发送第一页 (Direct Send)
            first_page_files = files[:10]
            status_msg = await send_collection_files(client, message, first_page_files, f"{collection['name']} (第1页)")
            
            # 删除完成提示，减少干扰
            try: await status_msg.delete()
            except: pass
            
            # 显示浮动菜单
            await show_collection_page(client, message, collection, files, 1, send_new=True)
        else:
            # <= 10个，直接发送
            await send_collection_files(client, message, files, collection['name'])
        return True

    # === 情景2: 是单个文件密钥 ===
    file_info = db.get_file_by_key(key)
    if file_info:
        try:
            # 检查是否加密
            if file_info.get("is_encrypted"):
                start_time = time.time()
                status_msg = await message.reply_text(
                    f"🔐 **发现加密档案**\n"
                    f"📄 文件: `{file_info['file_name']}`\n"
                    f"⏳ 正在云端解密并提取，请稍候..."
                )
                
                # 1. 下载加密文件
                dl_path = await client.download_media(
                    file_info["file_id"],
                    file_name=f"temp_enc_{key}.bin"
                )
                
                # 2. 解密
                from services.crypto_utils import decrypt_file
                import base64
                decrypted_path = f"temp_dec_{key}_{file_info['file_name']}"
                aes_key = base64.b64decode(file_info["encryption_key"])
                
                await asyncio.to_thread(decrypt_file, dl_path, decrypted_path, aes_key)
                
                # 3. 发送解密后的文件
                await message.reply_document(
                    document=decrypted_path,
                    caption=f"✅ 解密成功: {file_info['file_name']}",
                    file_name=file_info['file_name']
                )
                
                # 4. 清理
                if os.path.exists(dl_path): os.remove(dl_path)
                if os.path.exists(decrypted_path): os.remove(decrypted_path)
                
                await status_msg.delete()
                
            else:
                # 普通文件直接转发
                await client.send_cached_media(
                    message.chat.id,
                    file_info["file_id"],
                    caption=file_info["caption"] or ""
                )
            return True
        except Exception as e:
            await message.reply_text(f"❌ 提取失败: {e}")
            return True

    return False


# ========== 收集模式处理 ==========

@Client.on_message(filters.regex(r"^(结束|完成|done|finish|end)$", re.IGNORECASE) & filters.private)
async def end_collecting_mode(client: Client, message: Message):
    """退出收集模式"""
    import re
    user_id = message.from_user.id
    
    if user_id not in user_collecting_mode:
        return  # 不在收集模式，忽略
    
    mode = user_collecting_mode.pop(user_id)
    file_count = len(mode["files"])
    
    await message.reply_text(
        f"✅ **收集完成！**\n\n"
        f"📁 合集: **{mode['collection_name']}**\n"
        f"📊 共收集: **{file_count}** 个文件\n"
        f"🔑 密钥: `{mode['access_key']}`\n\n"
        f"分享密钥给他人即可获取整个合集！"
    )

async def get_collection_picker_keyboard(user_id, file_access_key, page=1):
    """生成合集选择键盘(支持分页和快速添加) - Smart Pagination"""
    from database import db
    from pyrogram.types import InlineKeyboardButton
    
    collections = db.get_user_collections(user_id)
    # 按ID倒序（最新的在前）
    collections.sort(key=lambda x: x['id'], reverse=True)
    
    per_page = 10 # 升级为10个每页
    total_pages = max(1, (len(collections) + per_page - 1) // per_page)
    
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start = (page - 1) * per_page
    end = start + per_page
    page_items = collections[start:end]
    
    extra_btns = []
    
    # 快速添加 (Last Used) - 仅当 page=1 时显示
    if page == 1:
        last_col = user_last_collection.get(user_id)
        if last_col:
            exists = any(c['id'] == last_col['id'] for c in collections)
            if exists:
                extra_btns.append([InlineKeyboardButton(
                    f"⚡ 快速添加: {last_col['name']}",
                    callback_data=f"addcol_{file_access_key}_{last_col['id']}"
                )])
        
    # 构建当前页集合列表按钮
    for c in page_items:
        extra_btns.append([InlineKeyboardButton(
            f"📁 {c['name']} ({c['file_count']})", 
            callback_data=f"addcol_{file_access_key}_{c['id']}"
        )])
        
    extra_btns.append([InlineKeyboardButton("➕ 新建合集", callback_data=f"newcol_{file_access_key}")])
    extra_btns.append([InlineKeyboardButton("❌ 不添加", callback_data=f"skipcol_{file_access_key}")])
    
    # 使用 Smart Pagination Helper
    # 如果只有1页，隐藏页码显示? Helper 内部逻辑?
    # 我们这里控制 Picker 的 Title 文本，Page Info 在 make_pagination_keyboard 处理
    return make_pagination_keyboard(
        total_pages,
    # ... Wait, make_pagination_keyboard logic:
    # return InlineKeyboardMarkup(...)
    # I should verify make_pagination_keyboard logic later. 
    # For now, I update the CALLER to hide text.
        page,
        f"pick_pg_{file_access_key}_",
        extra_buttons=extra_btns
    )

@Client.on_message(filters.private & (filters.document | filters.video | filters.photo | filters.audio | filters.forwarded))
async def media_handler(client: Client, message: Message):
    """
    Handle media files and forwards.
    """
    # 权限检查
    if not await check_auth(client, message):
        return
    """处理收到的媒体文件 (包括转发的文件) - 自动加密存储"""
    from database import db
    import config
    
    user_id = message.from_user.id
    
    # 仅管理员可用 -> 已移除，改为 check_auth
    # if user_id != config.ADMIN_ID:
    #     return
    
    # 获取文件信息
    file_id = None
    file_name = "未知文件"
    file_size = 0
    
    if message.video:
        file_id = message.video.file_id
        file_name = message.video.file_name or "video.mp4"
        file_size = message.video.file_size
    elif message.photo:
        file_id = message.photo.file_id
        file_name = "photo.jpg"
        file_size = message.photo.file_size
    elif message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "document"
        file_size = message.document.file_size
    elif message.audio:
        file_id = message.audio.file_id
        file_name = message.audio.file_name or "audio"
        file_size = message.audio.file_size
    
    if not file_id:
        return
    
    # 检查是否在收集模式
    in_collection_mode = user_id in user_collecting_mode
    mode = user_collecting_mode.get(user_id) if in_collection_mode else None
    
    # 检查文件是否已入库
    db.cursor.execute('SELECT id, access_key FROM files WHERE file_id = ?', (file_id,))
    row = db.cursor.fetchone()
    
    if row:
        # 文件已入库
        existing_file_id = row[0]
        existing_access_key = row[1]
        
        if in_collection_mode:
            # 收集模式：添加到合集
            mode['total'] += 1
            if db.add_file_to_collection(mode["collection_id"], existing_file_id):
                mode["files"].append(file_name)
                mode['success'] += 1
            else:
                mode['success'] += 1 # 重复添加也算成功
            
            # Dashboard
            now = time.time()
            if now - mode.get('last_update', 0) > 2.0:
                mode['last_update'] = now
                try:
                    await client.edit_message_text(
                        chat_id=mode['status_chat_id'],
                        message_id=mode['status_msg_id'],
                        text=(
                            f"📁 接收合集: **{mode['collection_name']}**\n"
                            f"🔄 秒传成功: `{file_name}`\n"
                            f"📊 进度: {mode['total']} | ✅ {mode['success']} | ❌ {mode['fail']}\n"
                            f"⏳ 发 **结束** 完成"
                        )
                    )
                except: pass
        else:
            # 非收集模式：告知已存在
            await message.reply_text(
                f"📄 文件已存在！\n\n"
                f"📁 `{file_name}`\n"
                f"🔑 提取码: `{existing_access_key}`"
            )
        return
    
    # 文件未入库 -> 自动下载、加密、上传、入库
    status_msg = None
    if in_collection_mode:
        mode['total'] += 1
        now = time.time()
        if now - mode.get('last_update', 0) > 2.0:
            mode['last_update'] = now
            try:
                await client.edit_message_text(
                    chat_id=mode['status_chat_id'],
                    message_id=mode['status_msg_id'],
                    text=(
                        f"📁 接收合集: **{mode['collection_name']}**\n"
                        f"🔄 正在处理: `{file_name}`\n"
                        f"📊 进度: {mode['total']} | ✅ {mode['success']} | ❌ {mode['fail']}\n"
                        f"⏳ 发 **结束** 完成"
                    )
                )
            except: pass
    else:
        status_msg = await message.reply_text(f"📥 正在处理 `{file_name}`...")
    
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    temp_file_name = f"temp_{unique_id}_{file_name}"
    
    # ...
    
    try:
        # 1. 下载文件
        # 使用唯一文件名避免冲突
        download_path = await client.download_media(message, file_name=temp_file_name)
        
        # 2. AES 加密
        from services.crypto_utils import generate_key, encrypt_file
        import base64
        import secrets
        import string
        
        aes_key = generate_key()
        aes_key_b64 = base64.b64encode(aes_key).decode('utf-8')
        
        random_name = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        encrypted_filename = f"{random_name}.bin"
        # 确保下载路径存在再操作
        if not download_path:
             raise Exception("Download failed, path is empty")
             
        encrypted_path = os.path.join(os.path.dirname(download_path), encrypted_filename)
        
        if status_msg: await status_msg.edit_text(f"🔒 正在加密 `{file_name}`...")
        await asyncio.to_thread(encrypt_file, download_path, encrypted_path, aes_key)
        
        # 删除原文件 (添加延时避免文件锁定)
        await asyncio.sleep(0.5)
        try:
            if os.path.exists(download_path):
                os.remove(download_path)
        except:
            pass
        
        # 3. 上传到存储频道 (优先用 Bot，失败则用闲置账号)
        if status_msg: await status_msg.edit_text(f"⬆️ 正在上传 `{file_name}`...")
        
        storage_msg = None
        upload_method = "Bot"
        
        # 先尝试用 Bot 上传
        try:
            storage_msg = await client.send_document(
                config.STORAGE_CHANNEL_ID,
                encrypted_path,
                caption=f"📦 {file_name}\n🔒 [AES-256 Encrypted]"
            )
        except Exception as bot_err:
            # Bot 失败，使用闲置账号
            upload_method = "存储账号"
            if status_msg:
                await status_msg.edit_text(f"⬆️ Bot上传失败，切换到存储账号...")
            storage_client = client.storage_client
            storage_msg = await storage_client.send_document(
                config.STORAGE_CHANNEL_ID,
                encrypted_path,
                caption=f"📦 {file_name}\n🔒 [AES-256 Encrypted]"
            )
        
        # 获取正确的 file_id 和 file_unique_id
        doc = storage_msg.document
        file_id_str = doc.file_id if doc else ""
        file_unique_id = doc.file_unique_id if doc else ""
        msg_id = storage_msg.id
        
        # 3.b 备份上传 (Dual Upload)
        backup_msg_id = 0
        backup_chat_id = 0
        
        if config.BACKUP_CHANNEL_ID and config.BACKUP_CHANNEL_ID != 0:
            try:
                if status_msg: await status_msg.edit_text(f"↻ 正在备份 `{file_name}`...")
                # 使用相同的上传方式 (Bot or User) 或 强制使用 User (更安全?) -> 这里跟随 primary logic
                uploader = client if upload_method == "Bot" else client.storage_client
                
                backup_msg = await uploader.send_document(
                    config.BACKUP_CHANNEL_ID,
                    encrypted_path,
                    caption=f"📦 {file_name}\n🔒 [AES-256 Encrypted Backup]"
                )
                backup_msg_id = backup_msg.id
                backup_chat_id = config.BACKUP_CHANNEL_ID
            except Exception as e:
                print(f"Backup failed: {e}")
                # 备份失败不阻断主流程

        # 4. 入库
        key_length = secrets.randbelow(17) + 16
        access_key = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(key_length))
        
        db.add_file(
            message_id=msg_id,
            chat_id=config.STORAGE_CHANNEL_ID,
            file_id=file_id_str,
            file_unique_id=file_unique_id,
            file_name=file_name,
            caption="",
            file_size=file_size,
            mime_type="application/octet-stream",
            storage_mode='telegram_stealth',
            access_key=access_key,
            is_encrypted=True,
            encryption_key=aes_key_b64,
            backup_message_id=backup_msg_id,
            backup_chat_id=backup_chat_id
        )
        
        # 清理加密文件
        await asyncio.sleep(0.3)
        try:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)
        except:
            pass
        
        # 5. 如果在收集模式，添加到合集
        # 5. 如果在收集模式，添加到合集
        if in_collection_mode:
            # ... (Existing Logic)
            db.cursor.execute('SELECT id FROM files WHERE access_key = ?', (access_key,))
            new_row = db.cursor.fetchone()
            if new_row:
                db.add_file_to_collection(mode["collection_id"], new_row[0])
            
            mode["files"].append(file_name)
            mode['success'] += 1
            
            now = time.time()
            if now - mode.get('last_update', 0) > 2.0:
                mode['last_update'] = now
                try:
                    await client.edit_message_text(
                        chat_id=mode['status_chat_id'],
                        message_id=mode['status_msg_id'],
                        text=(
                            f"📁 接收合集: **{mode['collection_name']}**\n"
                            f"✅ 刚刚完成: `{file_name}`\n"
                            f"📊 进度: {mode['total']} | ✅ {mode['success']} | ❌ {mode['fail']}\n"
                            f"⏳ 发 **结束** 完成"
                        )
                    )
                except: pass
        
        # === NEW: Media Group Logic ===
        elif message.media_group_id:
            mg_id = message.media_group_id
            
            # Init state if needed
            if mg_id not in media_group_states:
                # 发送初始消息 (带 Picker)
                # 使用 Group ID 作为 Picker Key 的一部分 -> pick_mg_GROUPID_
                # 但 Picker 需要 File Access Key? 
                # 我们这里 Picker 用于 Bind Group.
                # Callback: bind_mg_GROUPID_COLID
                
                start_text = f"📦 **收到相册/多文件组**\n⏳ 正在处理第 1 个文件..."
                
                # 获取 Picker (Page 1) - 使用 mg_ 前缀
                # helper definition: get_collection_picker_keyboard(user_id, key, page)
                # key used for callback: addcol_KEY_ID
                # We need addcol_mg_MGID_ID
                
                # Hack: Pass "mg_" + mg_id as the 'access_key' to the helper?
                # Helper uses key string in callback.
                # If key starts with "mg_", we handle it in `add_to_collection_callback`.
                
                fake_key = f"mg_{mg_id}"
                keyboard = await get_collection_picker_keyboard(user_id, fake_key, page=1)
                
                status_msg = await message.reply_text(start_text, reply_markup=keyboard)
                
                media_group_states[mg_id] = {
                    'msg': status_msg,
                    'keys': [],
                    'bound_col_id': None,
                    'bound_col_name': None,
                    'count': 0,
                    'last_update': time.time()
                }
            
            state = media_group_states[mg_id]
            state['count'] += 1
            state['keys'].append(access_key)
            
            # Check Binding
            if state['bound_col_id']:
                # Auto add
                db.cursor.execute('SELECT id FROM files WHERE access_key = ?', (access_key,))
                frow = db.cursor.fetchone()
                if frow:
                    db.add_file_to_collection(state['bound_col_id'], frow[0])
            
            # Debounced Update
            now = time.time()
            if now - state['last_update'] > 2.0 or state['count'] == 1:
                state['last_update'] = now
                try:
                    col_status = f"📂 存入: **{state['bound_col_name']}**" if state['bound_col_name'] else "Wait 选择合集..."
                    await state['msg'].edit_text(
                        f"📦 **收到相册/多文件组**\n"
                        f"📊 已处理: {state['count']} 个文件\n"
                        f"{col_status}\n\n"
                        f"📄 最新: `{file_name}`\n"
                        f"🔑 最新Key: `{access_key}`",
                        reply_markup=state['msg'].reply_markup
                    )
                except: pass

        else:
            # 非收集模式 & 单文件：返回提取码 + 可选添加到合集 (使用分页键盘)
            keyboard = await get_collection_picker_keyboard(user_id, access_key, page=1)
            
            await status_msg.edit_text(
                f"✅ **已加密存储！**\n\n"
                f"📄 文件: `{file_name}`\n"
                f"🔑 提取码: `{access_key}`\n\n"
                f"**添加到哪个合集？**",
                reply_markup=keyboard
            )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        await status_msg.edit_text(f"❌ 处理失败: {e}")


# ========== 合集选择回调 ==========

# 临时存储等待新合集名称的用户
user_pending_newcol = {}  # {user_id: access_key}

@Client.on_callback_query(filters.regex(r"^addcol_"))
async def add_to_collection_callback(client: Client, callback: CallbackQuery):
    """添加文件到现有合集"""
    from database import db
    
    # Handle Normal File or Media Group
    is_mg = False
    mg_id = None
    
    parts = callback.data.split("_")
    # check prefix used
    # callback data: addcol_KEY_COLID
    # if KEY startswith "mg", then it is Media Group
    
    collection_id = int(parts[-1])
    access_key = "_".join(parts[1:-1])
    
    if access_key.startswith("mg_"):
        is_mg = True
        mg_id = access_key[3:]
    
    # 获取文件 ID (Only for non-MG)
    if not is_mg:
        db.cursor.execute('SELECT id FROM files WHERE access_key = ?', (access_key,))
        row = db.cursor.fetchone()
        if row:
            db.add_file_to_collection(collection_id, row[0])
            
            # 获取合集名称用于缓存
            db.cursor.execute("SELECT name FROM collections WHERE id=?", (collection_id,))
            col_res = db.cursor.fetchone()
            col_name = col_res[0] if col_res else "合集"
            
            if col_res:
                user_last_collection[callback.from_user.id] = {'id': collection_id, 'name': col_name}
            
            await callback.message.edit_text(
                f"✅ 已添加到合集 **{col_name}**！\n\n"
                f"🔑 提取码: `{access_key}`"
            )
        else:
            await callback.answer("❌ 文件未找到", show_alert=True)
            
    else:
        # === Handle Media Group Binding ===
        if mg_id not in media_group_states:
             await callback.answer("❌ 任务已过期", show_alert=True)
             return

        state = media_group_states[mg_id]
        
        # Get Collection Name
        db.cursor.execute("SELECT name FROM collections WHERE id=?", (collection_id,))
        col_res = db.cursor.fetchone()
        col_name = col_res[0] if col_res else "合集"
        
        # 1. Bind
        state['bound_col_id'] = collection_id
        state['bound_col_name'] = col_name
        user_last_collection[callback.from_user.id] = {'id': collection_id, 'name': col_name}
        
        # 2. Add Existing Keys
        added_count = 0
        for key in state['keys']:
             db.cursor.execute('SELECT id FROM files WHERE access_key = ?', (key,))
             frow = db.cursor.fetchone()
             if frow:
                 db.add_file_to_collection(collection_id, frow[0])
                 added_count += 1
        
        # 3. Update Msg
        await state['msg'].edit_text(
            f"✅ **已绑定合集: {col_name}**\n"
            f"📊 当前处理: {state['count']} 个文件\n"
            f"📥 后续文件将自动存入此合集..."
        )
        await callback.answer(f"已存入 {added_count} 个文件")

@Client.on_callback_query(filters.regex(r"^pick_pg_"))
async def picker_pagination_callback(client: Client, callback: CallbackQuery):
    from database import db
    import config
    
    parts = callback.data.split("_")
    page = int(parts[-1])
    access_key = "_".join(parts[2:-1])
    
    # 1. 获取文件名称以重建文本
    file_name = db.get_file_name_by_access_key(access_key) or "未知文件"
    
    # 2. 获取总页数 (用于文本显示) 
    # 这里有点低效，但为了显示 "Page X/Y" 必须算一次
    collections = db.get_user_collections(callback.from_user.id)
    per_page = 10
    total_pages = max(1, (len(collections) + per_page - 1) // per_page)
    
    # 3. 构建文本
    page_info = f" (第 {page}/{total_pages} 页)" if total_pages > 1 else ""
    
    text = (
        f"✅ **已加密存储！**\n\n"
        f"📄 文件: `{file_name}`\n"
        f"🔑 提取码: `{access_key}`\n\n"
        f"**添加到哪个合集？**{page_info}"
    )
    
    keyboard = await get_collection_picker_keyboard(callback.from_user.id, access_key, page)
    
    # Floating Menu: Delete Old -> Send New
    try: await callback.message.delete()
    except: pass
    
    await client.send_message(callback.message.chat.id, text, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer(f"第 {page} 页")


@Client.on_callback_query(filters.regex(r"^newcol_"))
async def new_collection_callback(client: Client, callback: CallbackQuery):
    """创建新合集并添加文件"""
    parts = callback.data.split("_")
    access_key = "_".join(parts[1:])
    user_id = callback.from_user.id
    
    user_pending_newcol[user_id] = access_key
    
    await callback.message.edit_text(
        f"✅ 文件已保存！\n\n"
        f"🔑 提取码: `{access_key}`\n\n"
        f"📝 **请输入新合集的名称：**"
    )
    await callback.answer()

@Client.on_callback_query(filters.regex(r"^skipcol_"))
async def skip_collection_callback(client: Client, callback: CallbackQuery):
    """跳过添加合集"""
    parts = callback.data.split("_")
    access_key = "_".join(parts[1:])
    
    await callback.message.edit_text(
        f"✅ **已加密存储！**\n\n"
        f"🔑 提取码: `{access_key}`\n\n"
        f"发送提取码即可解密获取文件"
    )

@Client.on_message(filters.text & filters.private, group=-1)
async def pending_collection_name_handler(client: Client, message: Message):
    """处理等待中的新合集名称输入"""
    from database import db
    import config
    
    user_id = message.from_user.id
    
    if user_id not in user_pending_newcol:
        message.continue_propagation()  # 让其他处理器处理
    
    # 移除 Admin 检查，允许所有用户创建合集
    # if user_id != config.ADMIN_ID:
    #     message.continue_propagation()
    
    access_key = user_pending_newcol.pop(user_id)
    collection_name = message.text.strip()
    
    # 创建合集
    import secrets
    import string
    random_chars = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
    col_access_key = f"col_{random_chars}"
    
    collection_id = db.create_collection(collection_name, col_access_key, user_id)
    
    if collection_id:
        # 添加文件到合集
        db.cursor.execute('SELECT id FROM files WHERE access_key = ?', (access_key,))
        row = db.cursor.fetchone()
        if row:
            db.add_file_to_collection(collection_id, row[0])
        
        await message.reply_text(
            f"✅ **已创建合集并添加文件！**\n\n"
            f"📁 合集: **{collection_name}**\n"
            f"🔑 合集密钥: `{col_access_key}`\n"
            f"📄 文件提取码: `{access_key}`"
        )
    else:
        await message.reply_text("❌ 创建合集失败")

# ========== 分页回调 ==========

@Client.on_callback_query(filters.regex(r"^col_(pg|dl|all)_"))
async def collection_pagination_callback(client: Client, callback: CallbackQuery):
    from database import db
    parts = callback.data.split("_")
    action = parts[1]
    
    if action == "all":
        access_key = "_".join(parts[2:])
        page = 1
    else:
        # pg or dl
        try:
            page = int(parts[-1])
            access_key = "_".join(parts[2:-1])
        except ValueError:
            # Fallback for unexpected formats
            page = 1
            access_key = "_".join(parts[2:])

    collection = db.get_collection_by_key(access_key)
    if not collection:
        await callback.answer(f"合集不存在或密钥已失效\n(Key: {access_key})", show_alert=True)
        return
        
    files = db.get_collection_files(collection["id"])
    
    if action == "pg":
        # Direct Send + Floating Menu
        per_page = 10
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_files = files[start_idx:end_idx]
        
        await callback.answer(f"正在发送第 {page} 页...", show_alert=False)
        
        # 1. Update Menu -> Sending
        await send_collection_files(client, callback.message, page_files, f"{collection['name']} (第{page}页)", edit_msg=callback.message)
        
        # 2. Delete Old Menu
        try: await callback.message.delete()
        except: pass
        
        # 3. New Menu at Bottom
        await show_collection_page(client, callback.message, collection, files, page, send_new=True)
        
    elif action == "dl":
        per_page = 10
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_files = files[start_idx:end_idx]
        
        await callback.answer("开始发送...", show_alert=False)
        # 1. 使用当前菜单消息显示 "正在下载..." (edit_msg)
        await send_collection_files(client, callback.message, page_files, f"{collection['name']} (第{page}页)", edit_msg=callback.message)
        
        # 2. 发送完成后，删除旧菜单 (Floating Menu 效果)
        try:
            await callback.message.delete()
        except: pass
        
        # 3. 发送新菜单到最底部
        await show_collection_page(client, callback.message, collection, files, page, send_new=True)
        
    elif action == "all":
        # Smart Send All: 发送剩余 (跳过第一页)
        remaining_files = files[10:]
        if not remaining_files:
             await callback.answer("没有更多文件了 (第一页已发)", show_alert=True)
             return
             
        await callback.answer(f"开始发送剩余 {len(remaining_files)} 个文件...", show_alert=True)
        # 这里的 "发送剩余" 会自动处理 float menu 吗？
        # Send All 通常是终结操作，发送完后应该显示 "发送完成"
        # 或者 我们可以 Float 到最后一页？
        # 这里维持原样，只是把文件列表改成剩余的。
        await send_collection_files(client, callback.message, remaining_files, collection['name'], edit_msg=callback.message)


# ========== Interactive Menu Handlers (Priority -3: Always First) ==========
@Client.on_message(filters.regex("📥 批量下载") & filters.private, group=-3)
async def menu_download_handler(client, message):
    from handlers.setup import is_admin
    if not is_admin(client, message.from_user.id):
        await message.reply_text(
            "🚫 **权限不足**\n\n"
            "批量下载功能仅限管理员使用。\n"
            "如需下载禁止转发的受限资源，请联系机器人客服 (管理员)。"
        )
        return

    # Only keep the download workflow. Dialog discovery helpers are intentionally removed.
    from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
    buttons = [
        [KeyboardButton("📥 开始下载")],
        [KeyboardButton("❌ 取消操作"), KeyboardButton("🔙 返回主菜单")]
    ]
    await message.reply_text(
        "📥 **批量下载工具箱 (管理员)**\n\n"
        "这里只保留下载功能，不再提供最近对话、搜索对话、删除账户、媒体定位、频道 ID 查询或关联群查询。\n\n"
        "请自行提供消息链接，或输入：`频道ID 消息ID 数量`（精准模式）。",
        reply_markup=ReplyKeyboardMarkup(
            buttons,
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True
        )
    )

@Client.on_message(filters.regex("📥 开始下载") & filters.private, group=-3)
async def sub_start_download_handler(client, message):
    if not await require_admin(client, message):
        return
    from pyrogram.types import ForceReply
    user_interaction_state[message.from_user.id] = "waiting_dl_chat_id"
    is_adm = message.from_user.id == client.admin_id
    await message.reply_text(
        "📥 **批量下载**\n\n"
        "最简单：复制目标视频的消息链接直接发给我。\n"
        "消息 ID 就是链接最后一段数字。\n\n"
        "消息链接：`https://t.me/c/1234567890/4567`\n"
        "其中频道 ID 为 `-1001234567890`，消息 ID 为 `4567`\n\n"
        "也可使用两步模式（推荐）：\n"
        "① 先发频道ID（如 `-1001234567890`）\n"
        "② 再发消息ID（如 `4567`），默认递减下载10条\n"
        "   或发 `消息ID 数量`（如 `4567 20`）\n\n"
        "默认使用 ⚡ 快速合集：优先直接复制，速度更快。\n"
        "如果源消息禁止复制，会自动改为下载后直发给你。\n\n"
        "精准下载：`频道ID 消息ID 数量`\n"
        "例如：`8080158525 21037`（下载消息21037这一条）\n"
        "例如：`-1001234567890 4567 1`（下载消息4567起共1条）\n"
        "例如：`-1001234567890 4567 5`（下载消息4567起共5条）\n\n"
        "发 `取消` 或点 **❌ 取消操作** 可退出。",
        reply_markup=get_cancel_keyboard(is_adm)
    )
    message.stop_propagation()

@Client.on_message(filters.regex("☁️ 存储/上传") & filters.private, group=-3)
async def menu_storage_handler(client, message):
    # 仅管理员可用
    if message.from_user.id != client.admin_id:
        return
    from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
    buttons = [
        [KeyboardButton("📂 我的合集"), KeyboardButton("🆕 新建合集")],
        [KeyboardButton("🔍 查找文件"), KeyboardButton("📊 统计信息")],
        [KeyboardButton("❌ 取消操作"), KeyboardButton("🔙 返回主菜单")]
    ]
    await message.reply_text(
        "☁️ **存储中心**\n\n"
        "请选择操作：\n"
        "🔹 **我的合集**: 管理和浏览现有合集\n"
        "🔹 **新建合集**: 创建新的加密保险箱\n"
        "🔹 **查找文件**: 全局搜索已存储文件\n\n"
        "💡 当然，你也可以随时直接发送文件给我，我会自动处理。",
        reply_markup=ReplyKeyboardMarkup(
            buttons,
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True
        )
    )
    message.stop_propagation()

# Sub-menu handlers for Storage
@Client.on_message(filters.regex("📂 我的合集") & filters.private, group=-3)
async def sub_my_collections(client, message):
    # Trigger existing /mycollections logic
    # We can reuse my_collections_cmd (which is command-based) by creating a mock or extracting logic
    # Ideally, just call the function if it accepts (client, message)
    # my_collections_cmd is at ~line 942
    await my_collections_cmd(client, message)
    message.stop_propagation()

@Client.on_message(filters.regex("🆕 新建合集") & filters.private, group=-3)
async def sub_new_collection(client, message):
    await create_collection_cmd(client, message)
    message.stop_propagation()

@Client.on_message(filters.regex("🔍 查找文件") & filters.private, group=-3)
async def sub_find_file(client, message):
    from handlers.tools import find_cmd
    # find_cmd logic might assume args?
    # Let's check find_cmd later.
    # It probably needs logic like create_collection_cmd to Prompt "What to find?"
    # For now, just call it.
    await find_cmd(client, message)
    message.stop_propagation()

@Client.on_message(filters.regex("📊 统计信息") & filters.private, group=-3)
async def sub_stats_info(client, message):
    from handlers.tools import stats_cmd
    await stats_cmd(client, message)
    message.stop_propagation()


@Client.on_message(filters.regex("👮 管理员") & filters.private, group=-3)
async def menu_admin_handler(client, message):
    # Check Admin
    from handlers.setup import is_admin
    if not is_admin(client, message.from_user.id):
        return
        
    from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
    buttons = [
        [KeyboardButton("👥 用户管理"), KeyboardButton("📉 系统统计")],
        [KeyboardButton("🔐 安全状态")],
        # [KeyboardButton("🔍 搜索文件"), KeyboardButton("🗑 近期删除")],
        [KeyboardButton("🔙 返回主菜单")]
    ]
    await message.reply_text(
        "👮 **管理员控制台**\n请选择管理功能：",
        reply_markup=ReplyKeyboardMarkup(
            buttons,
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True
        )
    )
    message.stop_propagation()

# Sub-menu handlers for Admin
@Client.on_message(filters.regex("👥 用户管理") & filters.private, group=-3)
async def sub_admin_users(client, message):
    # Trigger list_users_handler
    await list_users_handler(client, message)
    message.stop_propagation()

@Client.on_message(filters.regex("📉 系统统计") & filters.private, group=-3)
async def sub_admin_stats(client, message):
    await admin_stats_cmd(client, message)
    message.stop_propagation()

@Client.on_message(filters.regex("🔐 安全状态") & filters.private, group=-3)
async def sub_security_status(client, message):
    await security_cmd(client, message)
    message.stop_propagation()


@Client.on_message(filters.regex("🔙 返回主菜单") & filters.private, group=-3)
async def back_to_main(client, message):
    from handlers.setup import send_main_menu
    await clear_user_state(message.from_user.id)
    await send_main_menu(client, message)
    message.stop_propagation()

@Client.on_message((filters.regex(r"^(❌ 取消操作|取消|cancel|/cancel)$", re.IGNORECASE)) & filters.private, group=-4)
async def cancel_text_handler(client, message):
    from handlers.setup import send_main_menu
    await clear_user_state(message.from_user.id)
    await message.reply_text("✅ 已取消当前操作。")
    await send_main_menu(client, message)
    message.stop_propagation()

@Client.on_callback_query(filters.regex("cancel_action"))
async def cancel_action_callback(client, callback):
    uid = callback.from_user.id
    if uid in user_interaction_state:
        del user_interaction_state[uid]
    await callback.message.edit_text("✅ 已取消操作")

# Enhanced Link Handler for Download State
# We hook into existing link_handler logic or pre-empt it?
# link_handler matches text. We can add a check at top of old link_handler OR add a new priority handler.
# New priority handler is better.

@Client.on_message(filters.text & filters.private, group=-2)
async def download_state_handler(client, message):

    uid = message.from_user.id
    state = user_interaction_state.get(uid)

    # Step 1: two-step mode - wait for channel id (or direct link/full args)
    if state == "waiting_dl_chat_id":
        if not await require_admin(client, message):
            await clear_user_state(uid)
            message.stop_propagation()
            return
        try:
            raw_text = message.text.strip()

            # 兼容：直接发链接 / 完整格式，直接进入确认
            if "t.me/" in raw_text or len(raw_text.split()) >= 2:
                chat_id, start_message_id, limit = await _parse_download_source(client, raw_text)
                user_interaction_state.pop(uid, None)
                user_download_chat.pop(uid, None)
                dest = user_download_dest.get(uid, "fast_collection")
                await request_download_confirmation(client, message, chat_id, limit, dest, start_message_id=start_message_id)
                message.stop_propagation()
                return

            chat_id = int(raw_text)
            user_download_chat[uid] = chat_id
            user_interaction_state[uid] = "waiting_dl_msg_id"
            is_adm = message.from_user.id == client.admin_id
            await message.reply_text(
                f"✅ 已选择频道: `{chat_id}`\n\n"
                "请发送消息ID（默认递减下载10条）：\n"
                "• `4567`（默认10条）\n"
                "• `4567 20`（递减20条）\n"
                "• 也可直接发完整链接",
                reply_markup=get_cancel_keyboard(is_adm)
            )
            message.stop_propagation()
            return
        except Exception:
            is_adm = message.from_user.id == client.admin_id
            await message.reply_text(
                "❌ 格式错误！\n\n"
                "第一步请先输入频道ID，例如：`-1001234567890`\n"
                "或直接发消息链接：`https://t.me/c/1234567890/4567`\n\n"
                "也可以输入完整格式：`频道ID 消息ID 数量`\n"
                "点 **❌ 取消操作** 可退出当前输入。",
                reply_markup=get_cancel_keyboard(is_adm)
            )
            return

    # Step 2: wait for msg id (or msg_id count)
    if state == "waiting_dl_msg_id":
        if not await require_admin(client, message):
            await clear_user_state(uid)
            message.stop_propagation()
            return
        try:
            text = message.text.strip()
            chat_id = user_download_chat.get(uid)
            if not chat_id:
                raise ValueError("missing chat")

            # 允许在第二步仍然直接贴链接
            if "t.me/" in text:
                parsed_chat_id, start_message_id, limit = await _parse_download_source(client, text)
                chat_id = parsed_chat_id
            else:
                parts = text.split()
                if len(parts) == 1:
                    start_message_id = int(parts[0])
                    limit = 10
                elif len(parts) >= 2:
                    start_message_id = int(parts[0])
                    limit = int(parts[1])
                else:
                    raise ValueError("invalid")

            user_interaction_state.pop(uid, None)
            user_download_chat.pop(uid, None)
            dest = user_download_dest.get(uid, "fast_collection")
            await request_download_confirmation(client, message, chat_id, limit, dest, start_message_id=start_message_id)
            message.stop_propagation()
            return
        except Exception:
            is_adm = message.from_user.id == client.admin_id
            await message.reply_text(
                "❌ 消息ID格式错误。\n\n"
                "请发送：\n"
                "• `4567`（默认递减10条）\n"
                "• `4567 20`（递减20条）\n"
                "• 或直接发消息链接",
                reply_markup=get_cancel_keyboard(is_adm)
            )
            return
    
    message.continue_propagation()

# ========== User Management (Admin) ==========

@Client.on_message(filters.command("users") & filters.private)
async def list_users_handler(client, message):
    from handlers.setup import is_admin
    if not is_admin(client, message.from_user.id):
        return

    await show_user_list(client, message, page=1)

async def show_user_list(client, message, page=1):
    from database import db
    users = db.get_all_users()
    total_users = len(users)
    per_page = 10
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_users = users[start_idx:end_idx]
    
    text = f"👥 **用户列表** (共 {total_users} 人)\n页码: {page}/{total_pages}\n\n"
    
    keyboard = []
    
    for u in page_users:
        status_icon = "🟢" if u['status'] == 'active' else "🔴"
        name = u['first_name'] or "未知"
        uid = u['id']
        username = f"@{u['username']}" if u['username'] else "No Username"
        
        # Add Manage Button for each user
        # Limit row width? 1 per row clearly
        btn_text = f"{status_icon} {name} ({uid})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"mng_u_{uid}")])
        
        text += f"{status_icon} **{name}** `{uid}`\nStatus: {u['status']}\n\n"
        
    # Pagination
    nav_btns = []
    if page > 1:
        nav_btns.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"users_pg_{page-1}"))
    if page < total_pages:
        nav_btns.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"users_pg_{page+1}"))
        
    if nav_btns:
        keyboard.append(nav_btns)
        
    # Add Refresh
    keyboard.append([InlineKeyboardButton("🔄 刷新", callback_data=f"users_pg_{page}")])
    
    try:
        if isinstance(message, Message):
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except: pass

@Client.on_callback_query(filters.regex(r"^users_pg_"))
async def users_page_callback(client, callback):
    if not await require_admin(client, callback, alert=True):
        return
    page = int(callback.data.split("_")[-1])
    await show_user_list(client, callback.message, page)
    await callback.answer()

@Client.on_callback_query(filters.regex(r"^mng_u_"))
async def manage_user_callback(client, callback):
    if not await require_admin(client, callback, alert=True):
        return
    uid = int(callback.data.split("_")[-1])
    from database import db
    user = db.get_user(uid)
    
    if not user:
        await callback.answer("用户不存在", show_alert=True)
        return
        
    info = (
        f"👤 **用户管理**\n\n"
        f"名字: {user['first_name']}\n"
        f"ID: `{user['id']}`\n"
        f"用户名: @{user['username']}\n"
        f"状态: {user['status']}\n"
        f"封禁至: {user['ban_until'] or '无'}\n"
        f"同意条款: {'✅' if user['accepted_terms'] else '❌'}\n"
    )
    
    btns = [
        [
            InlineKeyboardButton("🚫 封禁 1天", callback_data=f"ban_u_{uid}_1d"),
            InlineKeyboardButton("🚫 封禁 3天", callback_data=f"ban_u_{uid}_3d")
        ],
        [
            InlineKeyboardButton("🚫 永久封禁", callback_data=f"ban_u_{uid}_forever"),
            InlineKeyboardButton("✅ 解封", callback_data=f"ban_u_{uid}_unban")
        ],
        [InlineKeyboardButton("🔙 返回列表", callback_data="users_pg_1")]
    ]
    
    await callback.message.edit_text(info, reply_markup=InlineKeyboardMarkup(btns))
    await callback.answer()

@Client.on_callback_query(filters.regex(r"^ban_u_"))
async def execute_ban_callback(client, callback):
    if not await require_admin(client, callback, alert=True):
        return
    parts = callback.data.split("_")
    uid = int(parts[2])
    action = parts[3]
    
    from database import db
    from datetime import datetime, timedelta
    
    status = "active"
    until = None
    msg = "已解封"
    

    if action == "unban":
        status = "active"
        until = None
        msg = "✅ 用户已解封"
    elif action == "forever":
        status = "banned"
        until = datetime.now() + timedelta(days=36500) # 100 years
        msg = "🚫 用户已永久封禁"
    elif action.endswith("d"):
        days = int(action[:-1])
        status = "banned"
        until = datetime.now() + timedelta(days=days)
        msg = f"🚫 用户封禁 {days} 天"
        
    db.set_user_ban(uid, status, until)
    
    await callback.answer(msg, show_alert=True)
    
    # Refresh View
    # Call manage_user_callback logic again
    # Reuse via fake callback data?
    # Or just copy logic
    user = db.get_user(uid)
    info = (
        f"👤 **用户管理**\n\n"
        f"名字: {user['first_name']}\n"
        f"ID: `{user['id']}`\n"
        f"用户名: @{user['username']}\n"
        f"状态: {user['status']}\n"
        f"封禁至: {user['ban_until'] or '无'}\n"
        f"同意条款: {'✅' if user['accepted_terms'] else '❌'}\n"
    )
    
    btns = [
        [
            InlineKeyboardButton("🚫 封禁 1天", callback_data=f"ban_u_{uid}_1d"),
            InlineKeyboardButton("🚫 封禁 3天", callback_data=f"ban_u_{uid}_3d")
        ],
        [
            InlineKeyboardButton("🚫 永久封禁", callback_data=f"ban_u_{uid}_forever"),
            InlineKeyboardButton("✅ 解封", callback_data=f"ban_u_{uid}_unban")
        ],
        [InlineKeyboardButton("🔙 返回列表", callback_data="users_pg_1")]
    ]
    await callback.message.edit_text(info, reply_markup=InlineKeyboardMarkup(btns))


# ========== Terms Agreement Handler ==========
@Client.on_callback_query(filters.regex("agree_terms"))
async def agree_terms_callback(client, callback):
    from database import db
    from handlers.session import activate_session
    uid = callback.from_user.id
    
    # Security Check: Re-verify Ban Status before activating session
    # (Prevents restart-bypass)
    from datetime import datetime
    u_data = db.get_user(uid)
    if u_data and u_data.get('status') == 'banned':
        ban_until = u_data.get('ban_until')
        blocked = False
        if ban_until:
             if isinstance(ban_until, str):
                 try: ban_until = datetime.fromisoformat(ban_until)
                 except: pass
             if isinstance(ban_until, datetime) and ban_until > datetime.now():
                 blocked = True
        else:
             blocked = True
             
        if blocked:
            await callback.answer("🚫 无法操作: 您已被封禁。", show_alert=True)
            return

    # Update Session (Vital for "Restart" logic)
    activate_session(uid)
    
    # Update DB (Just for records)
    db.update_user_terms(uid, True)
    
    await callback.answer("✅ 你已同意条款，欢迎使用！")
    try:
        await callback.message.delete()
    except: pass
    
    # Send Main Menu
    from handlers.setup import send_main_menu
    await send_main_menu(client, callback.message)


# ========== 补充功能: 查找与统计 ==========

@Client.on_message(filters.command("find") & filters.private)
async def find_cmd(client: Client, message: Message):
    """查找文件"""
    from database import db
    from pyrogram.types import ForceReply
    
    args = message.text.split(maxsplit=1)
    
    # Handle Button Trigger
    if message.text == "🔍 查找文件" or len(args) < 2:
        await message.reply_text(
            "🔍 **查找文件**\n\n"
            "请输入关键词：",
            reply_markup=ForceReply(placeholder="输入关键词...")
        )
        return
        
    keyword = args[1]
    owner_id = message.from_user.id
    
    # Simple Search (Exclude deleted/banned? Not handled yet for files)
    # Search User's Collections first? Or All Files?
    # User owns collections, files are global but encrypted? 
    # Usually "My Files" -> Files in My Collections.
    # But current DB structure: Files don't have owner_id directly, Collections do.
    # Files are linked to Collections via collection_files.
    # So finding USER'S files means: 
    # JOIN collections ON collection_files.collection_id = collections.id WHERE collections.owner_id = ? AND files.file_name LIKE ?
    
    rows = db.search_user_files(owner_id, keyword, limit=20)
    
    if not rows:
        await message.reply_text(f"❌ 未找到包含 **{keyword}** 的文件。")
        return
        
    text = f"🔍 **搜索结果: {keyword}**\n\n"
    for r in rows:
        text += f"📄 `{r['file_name']}`\n   └ 📁 {r['collection_name']} | 🔑 `{r['access_key']}`\n"
        
    await message.reply_text(text)

@Client.on_message(filters.command("stats") & filters.private)
async def stats_cmd(client: Client, message: Message):
    """统计信息"""
    from database import db
    owner_id = message.from_user.id
    
    # Count User's Collections
    db.cursor.execute("SELECT COUNT(*) FROM collections WHERE owner_id=?", (owner_id,))
    c_count = db.cursor.fetchone()[0]
    
    # Count User's Files (Distinct)
    db.cursor.execute("""
        SELECT COUNT(DISTINCT f.id) 
        FROM files f
        JOIN collection_files cf ON f.id = cf.file_id
        JOIN collections c ON cf.collection_id = c.id
        WHERE c.owner_id = ?
    """, (owner_id,))
    f_count = db.cursor.fetchone()[0]
    
    await message.reply_text(
        f"📊 **统计信息**\n\n"
        f"👤 用户: {message.from_user.first_name}\n"
        f"📂 合集总数: {c_count}\n"
        f"📄 文件总数: {f_count}\n"
    )

@Client.on_message(filters.reply & filters.private)
async def search_reply_handler(client: Client, message: Message):
    """Handle Reply to Search Prompt"""
    reply = message.reply_to_message
    if not reply or not reply.text: return
    
    if "🔍 **查找文件**" in reply.text and "请输入关键词" in reply.text:
         # Execute Search
         keyword = message.text.strip()
         from database import db
         owner_id = message.from_user.id
         
         rows = db.search_user_files(owner_id, keyword, limit=20)
        
         if not rows:
            await message.reply_text(f"❌ 未找到包含 **{keyword}** 的文件。")
            return
            
         text = f"🔍 **搜索结果: {keyword}**\n\n"
         for r in rows:
            text += f"📄 `{r['file_name']}`\n   └ 📁 {r['collection_name']} | 🔑 `{r['access_key']}`\n"
            
         await message.reply_text(text)


async def admin_stats_cmd(client: Client, message: Message):
    """管理员查看系统级统计"""
    from handlers.setup import is_admin
    if not is_admin(client, message.from_user.id):
        await message.reply_text("🚫 权限不足")
        return
        
    from database import db
    
    # 1. User Count
    db.cursor.execute("SELECT COUNT(*) FROM users")
    user_count = db.cursor.fetchone()[0]
    
    # 2. Collection Count
    db.cursor.execute("SELECT COUNT(*) FROM collections")
    col_count = db.cursor.fetchone()[0]
    
    # 3. File Count
    db.cursor.execute("SELECT COUNT(*) FROM files")
    file_count = db.cursor.fetchone()[0]
    
    await message.reply_text(
        f"📉 **系统全局统计 (管理员)**\n\n"
        f"👥 **注册用户**: `{user_count}` 人\n"
        f"📂 **合集总数**: `{col_count}` 个\n"
        f"📄 **文件总存量**: `{file_count}` 个\n\n"
        f"✅ 系统运行正常。"
    )


@Client.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client: Client, message: Message):
    """取消当前操作，返回主菜单"""
    await clear_user_state(message.from_user.id)
    await message.reply_text(
        "✅ 操作已取消。"
    )
    from handlers.setup import send_main_menu
    await send_main_menu(client, message)
