from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import config
from handlers.tools import check_auth

# ========== 管理员专用检查 ==========
def is_admin(client, user_id):
    """检查是否是管理员"""
    return user_id == client.admin_id

@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    """Handle /start command"""
    # 权限检查
    if not await check_auth(client, message):
        return

    # === Terms Check (Session Based) ===
    from handlers.session import is_session_active
    from database import db

    # Check if user agreed in THIS session
    if not is_session_active(message.from_user.id):
        s_text = (
            "📜 **免责声明 (Disclaimer)**\n\n"
            "1. 本机器人仅用于个人数据备份与管理，代码开源且透明。\n"
            "2. 用户需自行承担使用本工具产生的一切后果。\n"
            "3. 请勿利用本工具存储或传播任何违反当地法律法规的内容。\n\n"
            "点击下方按钮代表你已阅读并同意以上条款。"
        )
        await message.reply_text(
            s_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ 我同意以上条款", callback_data="agree_terms")]])
        )
        return

    # 显示主菜单
    await send_main_menu(client, message)

async def send_main_menu(client, message):
    from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton

    # Check Admin
    is_adm = message.from_user.id == client.admin_id

    if is_adm:
        welcome = (
            "👋 **欢迎回来，管理员！**\n\n"
            "请通过下方菜单选择功能：\n\n"
            "📥 **批量下载**: 从频道批量抓取文件\n"
            "☁️ **存储/上传**: 加密存储与合集管理\n"
            "👮 **管理员**: 用户管理与系统状态"
        )
    else:
        welcome = (
            "👋 **欢迎使用文件下载机器人！**\n\n"
            "📌 **使用方法：**\n"
            "直接发送 Telegram 消息链接，机器人会自动帮你下载并发送文件。\n\n"
            "支持格式：\n"
            "• `https://t.me/频道名/消息ID`\n"
            "• `https://t.me/c/频道ID/消息ID`\n\n"
            "发送链接即可开始下载 ⬇️"
        )

    await message.reply_text(
        welcome,
        reply_markup=get_main_menu_keyboard(is_adm)
    )

def get_main_menu_keyboard(is_admin_user=False):
    from pyrogram.types import ReplyKeyboardMarkup, KeyboardButton
    if is_admin_user:
        buttons = [
            [KeyboardButton("📥 批量下载")],
            [KeyboardButton("☁️ 存储/上传")],
            [KeyboardButton("👮 管理员")],
            [KeyboardButton("❌ 取消操作")],
        ]
    else:
        buttons = [
            [KeyboardButton("❌ 取消操作")],
        ]

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        placeholder="发送链接下载，或选择功能..."
    )

@Client.on_callback_query(filters.regex("agree_terms"))
async def terms_btn_callback(client: Client, callback):
    from database import db
    from handlers.session import activate_session

    activate_session(callback.from_user.id)
    db.accept_terms(callback.from_user.id)

    await callback.answer("✅ 已同意条款")
    try: await callback.message.delete()
    except: pass

    is_adm = callback.from_user.id == client.admin_id

    if is_adm:
        welcome = (
            "👋 **欢迎回来，管理员！**\n\n"
            "请通过下方菜单选择功能："
        )
    else:
        welcome = (
            "👋 **欢迎使用！**\n\n"
            "直接发送 Telegram 消息链接，机器人会自动帮你下载并发送文件。\n\n"
            "支持格式：\n"
            "• `https://t.me/频道名/消息ID`\n"
            "• `https://t.me/c/频道ID/消息ID`\n\n"
            "发送链接即可开始下载 ⬇️"
        )

    await client.send_message(
        callback.message.chat.id,
        welcome,
        reply_markup=get_main_menu_keyboard(is_adm)
    )


@Client.on_message(filters.forwarded & filters.private)
async def channel_id_sniffer(client: Client, message: Message):
    """Detect forwarded messages - only for admin config helper"""
    # 权限检查
    if not await check_auth(client, message):
        return
    # 只有管理员才显示频道ID信息（普通用户转发文件走 media_handler）
    if message.from_user.id != client.admin_id:
        return
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
        chat_title = message.forward_from_chat.title
        chat_type = message.forward_from_chat.type

        # 支持频道和群组
        if str(chat_type) in ["ChatType.CHANNEL", "ChatType.SUPERGROUP", "ChatType.GROUP"]:
            type_name = "频道" if "CHANNEL" in str(chat_type) else "群组"
            response = (
                f"✅ **成功获取{type_name}信息！**\n\n"
                f"📂 **{type_name}名称**: {chat_title}\n"
                f"🆔 **{type_name} ID**: `{chat_id}`\n\n"
                f"复制这个 ID，填到 `.env` 文件的 `TG_STORAGE_CHANNEL`：\n"
                f"```\nTG_STORAGE_CHANNEL={chat_id}\n```\n"
                f"**然后重启机器人！**"
            )
            await message.reply_text(response)
        else:
            await message.reply_text(
                f"⚠️ **不支持的类型**\n"
                f"检测到的类型: {chat_type}\n"
                f"请转发**群组或频道**的消息。"
            )
    else:
        await message.reply_text(
            "⚠️ **无法读取频道信息**\n"
            "这可能是因为该频道的隐私设置不允许转发来源。\n\n"
            "**尝试方法 B：**\n"
            "1. 在该频道里发一条消息。\n"
            "2. 复制那条消息的链接 (Copy Link)。\n"
            "3. 把链接发给我。"
        )

@Client.on_message(
    filters.text & filters.private
    & ~filters.reply
    & ~filters.command("start") & ~filters.command("recent")
    & ~filters.command("download") & ~filters.command("search")
    & ~filters.command("getid") & ~filters.command("linked")
    & ~filters.command("deleted") & ~filters.command("newcollection")
    & ~filters.command("addto") & ~filters.command("mycollections")
    & ~filters.command("tasks") & ~filters.command("security")
)
async def admin_smart_direct_handler(client: Client, message: Message):
    """Allow admin to paste download sources directly without entering the menu."""
    if not await check_auth(client, message):
        return

    if message.from_user.id != client.admin_id:
        return

    import re
    clean_text = re.sub(r'^@\w+\s+', '', message.text.strip()).strip()

    from handlers.tools import (
        _parse_download_source,
        request_download_confirmation,
        user_collecting_mode,
        user_interaction_state,
        user_pending_newcol,
    )

    in_manual_flow = (
        message.reply_to_message is not None
        or message.from_user.id in user_interaction_state
        or message.from_user.id in user_collecting_mode
        or message.from_user.id in user_pending_newcol
        or clean_text.startswith("/")
    )
    if in_manual_flow:
        return

    looks_like_download_source = (
        "t.me/" in clean_text
        or re.match(r"^-?\d+\s+\d+(?:\s+\d+)?$", clean_text) is not None
    )
    if not looks_like_download_source:
        return

    try:
        chat_id, start_message_id, limit = await _parse_download_source(client, clean_text)
    except Exception:
        return

    await request_download_confirmation(
        client,
        message,
        chat_id,
        limit,
        "fast_collection",
        start_message_id=start_message_id,
    )
    message.stop_propagation()


@Client.on_message(
    filters.text & filters.private
    & ~filters.reply
    & ~filters.command("start") & ~filters.command("recent")
    & ~filters.command("download") & ~filters.command("search")
    & ~filters.command("getid") & ~filters.command("linked")
    & ~filters.command("deleted") & ~filters.command("newcollection")
    & ~filters.command("addto") & ~filters.command("mycollections")
    & ~filters.command("tasks") & ~filters.command("security")
)
async def link_handler(client: Client, message: Message):
    """Handle extraction keys, collection keys, and admin smart direct input."""
    # 权限检查
    if not await check_auth(client, message):
        return
    import re
    import os
    import asyncio
    text = message.text.strip()

    # 如果是 TG 消息链接，不在这里处理，让 transfer.py / public_transfer.py 处理
    if re.search(r"t\.me/", text):
        message.continue_propagation()
        return

    # 清理文本，去除可能的 @username 前缀
    clean_text = re.sub(r'^@\w+\s+', '', text).strip()

    # 检查是否是提取码 (16-32位字母数字)
    if re.match(r'^[a-zA-Z0-9]{16,32}$', clean_text):
        key = clean_text
        from database import db
        file_info = db.get_file_by_key(key)
        if file_info:
            try:
                if file_info.get("is_encrypted"):
                    status_msg = await message.reply_text(
                        f"🔐 **发现加密档案**\n"
                        f"📄 文件: `{file_info['file_name']}`\n"
                        f"⏳ 正在云端解密并提取，请稍候..."
                    )

                    storage_client = client.storage_client

                    enc_msg = await storage_client.get_messages(
                        file_info["chat_id"],
                        file_info["message_id"]
                    )

                    dl_path = await storage_client.download_media(
                        enc_msg,
                        file_name=f"temp_enc_{key}.bin"
                    )

                    from services.crypto_utils import decrypt_file
                    import base64

                    decrypted_path = f"temp_dec_{key}_{file_info['file_name']}"
                    aes_key = base64.b64decode(file_info["encryption_key"])

                    await asyncio.to_thread(decrypt_file, dl_path, decrypted_path, aes_key)

                    await message.reply_document(
                        document=decrypted_path,
                        caption=f"✅ 解密成功: {file_info['file_name']}",
                        file_name=file_info['file_name']
                    )

                    if os.path.exists(dl_path): os.remove(dl_path)
                    if os.path.exists(decrypted_path): os.remove(decrypted_path)

                    await status_msg.delete()
                else:
                    await client.send_cached_media(
                        message.chat.id,
                        file_info["file_id"],
                        caption=file_info["caption"] or ""
                    )
                return
            except Exception as e:
                import traceback
                traceback.print_exc()
                await message.reply_text(f"❌ 文件发送失败: {e}")
                return

    # 检查是否是合集密钥
    from handlers.tools import handle_collection_key
    if await handle_collection_key(client, message, clean_text):
        return

    # 配置未完成提示
    if config.STORAGE_CHANNEL_ID == -1000000000000:
        await message.reply_text(
            "⚠️ **配置未完成**\n\n"
            "如果你已经获取了频道 ID，请去修改 `config.py` 文件。\n"
            "如果你还没获取，请按 `/start` 的提示操作。"
        )

# ========== 群组消息监听 (用于 Peer 缓存) ==========
@Client.on_message(filters.group)
async def group_message_handler(client: Client, message: Message):
    """
    监听群组消息。
    当机器人在群组中收到消息时，Pyrogram 会自动缓存该群组的 peer 信息。
    这解决了机器人无法直接通过 ID 发送消息的问题 (Peer id invalid)。
    """
    if message.chat.id == config.STORAGE_CHANNEL_ID:
        print(f"✅ Bot 收到存储频道 [{message.chat.title}] 的消息，Peer 缓存已更新。")
