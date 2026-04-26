"""
公开下载处理器
普通用户发送 Telegram 消息链接 → 机器人用后台账号下载 → 直接发文件给用户
无需用户登录，无需管理员权限
"""

import os
import time
import math
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
import config
from database import db

print("🔁 Loading Handler: public_transfer.py")

# ========== 并发控制 ==========
# 同时最多处理的下载任务数，防止服务器过载
MAX_CONCURRENT_DOWNLOADS = 3
# Semaphore 必须在事件循环启动后创建，这里用 None 占位，首次使用时懒初始化
_download_semaphore: asyncio.Semaphore = None

def _get_semaphore() -> asyncio.Semaphore:
    """懒初始化 Semaphore，确保在事件循环运行后才创建"""
    global _download_semaphore
    if _download_semaphore is None:
        _download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    return _download_semaphore

# 用户冷却时间（秒），防止刷屏
USER_COOLDOWN = 10  # 10秒内只能发一个请求
_user_last_request = {}  # {user_id: timestamp}

# ========== 进度回调 ==========
async def _progress(current, total, message, action_text, start_time):
    now = time.time()
    diff = now - start_time
    if diff < 1:
        return
    # 每3秒更新一次进度
    if round(diff % 3.00) != 0 and current != total:
        return

    percentage = current * 100 / total
    speed = current / diff if diff > 0 else 0
    eta = round((total - current) / speed) if speed > 0 else 0

    bar_filled = math.floor(percentage / 10)
    bar = "▰" * bar_filled + "▱" * (10 - bar_filled)

    def _hb(size):
        if not size:
            return "0 B"
        power = 2 ** 10
        n = 0
        units = {0: "B", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
        while size > power and n < 4:
            size /= power
            n += 1
        return f"{size:.1f} {units[n]}"

    try:
        await message.edit_text(
            f"{action_text}\n"
            f"[{bar}] {percentage:.1f}%\n"
            f"{_hb(current)} / {_hb(total)}\n"
            f"速度: {_hb(speed)}/s  剩余: {eta}s"
        )
    except Exception:
        pass


# ========== 链接解析 ==========
def _parse_tg_link(url: str):
    """
    解析 Telegram 消息链接，返回 (chat_id_or_username, message_id)
    支持：
      https://t.me/c/1234567890/123            → (-1001234567890, 123)
      https://t.me/channelname/123             → ("channelname", 123)
      https://t.me/channelname/123?single      → ("channelname", 123)
      https://t.me/channelname/500?single&comment=664  → ("channelname", 500)
    注意：带 comment= 的是评论区链接，取的是主消息 ID（500），不是评论 ID（664）
    """
    import re
    url = url.strip()

    # 统一用正则解析，兼容所有参数格式
    # 私有频道: t.me/c/数字ID/消息ID
    m = re.search(r"t\.me/c/(\d+)/(\d+)", url)
    if m:
        chat_id = int("-100" + m.group(1))
        message_id = int(m.group(2))
        return chat_id, message_id

    # 公开频道/群组: t.me/用户名/消息ID
    m = re.search(r"t\.me/([\w\d_]+)/(\d+)", url)
    if m:
        chat_username = m.group(1)
        message_id = int(m.group(2))
        return chat_username, message_id

    raise ValueError("无法识别的链接格式")


# ========== 主处理器 ==========
@Client.on_message(
    # 兼容带参数的链接，如 ?single&comment=664
    filters.regex(r"https://t\.me/(c/|)([\w\d_]+)/(\d+)") & filters.private,
    group=5  # 优先级低于管理员处理器（管理员处理器在 transfer.py group=0）
)
async def public_transfer_handler(client: Client, message: Message):
    """
    普通用户发送 TG 消息链接 → 机器人下载 → 直接发给用户
    管理员走原来的 transfer.py 流程（存储+加密），此处跳过管理员
    """
    user_id = message.from_user.id

    # 管理员走原来的流程，不在这里处理
    if user_id == getattr(client, "admin_id", None):
        return

    # ---- 冷却检查 ----
    now = time.time()
    last = _user_last_request.get(user_id, 0)
    if now - last < USER_COOLDOWN:
        remaining = int(USER_COOLDOWN - (now - last))
        await message.reply_text(
            f"⏳ 请稍等 {remaining} 秒后再发送新链接。"
        )
        return
    _user_last_request[user_id] = now

    url = message.text.strip()

    # ---- 解析链接 ----
    try:
        chat_id, message_id = _parse_tg_link(url)
    except Exception:
        await message.reply_text("❌ 无法解析链接格式，请发送正确的 Telegram 消息链接。")
        return

    status_msg = await message.reply_text("🔎 正在解析消息，请稍候...")

    # ---- 并发限制（使用懒初始化的 Semaphore）----
    sem = _get_semaphore()
    if sem._value == 0:
        await status_msg.edit_text(
            "⚠️ 当前下载队列已满，请稍后再试。\n"
            f"（最多同时处理 {MAX_CONCURRENT_DOWNLOADS} 个任务）"
        )
        return

    async with sem:
        await _do_download_and_send(client, message, status_msg, chat_id, message_id)


async def _do_download_and_send(client: Client, message: Message, status_msg, chat_id, message_id: int):
    """核心：下载并发送文件给用户"""
    user_client = client.user_client
    download_path = None

    try:
        # 1. 获取消息
        target_msg = None
        try:
            target_msg = await user_client.get_messages(chat_id, message_id)
        except Exception as e1:
            err = str(e1)
            # 尝试讨论区 API 兜底
            if any(k in err for k in ("CHANNEL_PRIVATE", "USER_NOT_PARTICIPANT", "PEER_ID_INVALID", "CHAT_FORBIDDEN")):
                await status_msg.edit_text("⚠️ 直接访问受限，尝试备用方式...")
                try:
                    from pyrogram import raw
                    peer = await user_client.resolve_peer(chat_id)
                    result = await user_client.invoke(
                        raw.functions.messages.GetDiscussionMessage(
                            peer=peer,
                            msg_id=message_id
                        )
                    )
                    if result and result.messages and result.chats:
                        disc_chat_id = int("-100" + str(result.chats[0].id))
                        disc_msg_id = result.messages[0].id
                        target_msg = await user_client.get_messages(disc_chat_id, disc_msg_id)
                except Exception as e2:
                    await status_msg.edit_text(
                        "❌ **无法访问该内容**\n\n"
                        "可能原因：\n"
                        "• 该频道/群组为私密，机器人账号未加入\n"
                        "• 消息已被删除\n"
                        "• 该内容设置了转发限制\n\n"
                        "请联系管理员处理。"
                    )
                    return
            else:
                await status_msg.edit_text(f"❌ 获取消息失败：`{err[:200]}`")
                return

        if not target_msg or getattr(target_msg, "empty", False):
            await status_msg.edit_text("❌ 消息不存在或已被删除。")
            return

        # 2. 检查是否有媒体
        if not target_msg.media:
            # 纯文本消息，直接转发文本内容
            text_content = target_msg.text or target_msg.caption or ""
            if text_content:
                await status_msg.edit_text(
                    f"📝 **该消息为纯文本：**\n\n{text_content[:3000]}"
                )
            else:
                await status_msg.edit_text("⚠️ 该消息没有文件内容。")
            return

        # 3. 获取文件信息
        file_name = "未知文件"
        file_size = 0

        if target_msg.video:
            file_name = target_msg.video.file_name or f"video_{message_id}.mp4"
            file_size = target_msg.video.file_size or 0
        elif target_msg.document:
            file_name = target_msg.document.file_name or f"doc_{message_id}"
            file_size = target_msg.document.file_size or 0
        elif target_msg.photo:
            file_name = f"photo_{message_id}.jpg"
            file_size = target_msg.photo.file_size or 0
        elif target_msg.audio:
            file_name = target_msg.audio.file_name or f"audio_{message_id}.mp3"
            file_size = target_msg.audio.file_size or 0
        elif target_msg.voice:
            file_name = f"voice_{message_id}.ogg"
            file_size = target_msg.voice.file_size or 0
        elif target_msg.video_note:
            file_name = f"video_note_{message_id}.mp4"
            file_size = target_msg.video_note.file_size or 0
        elif target_msg.sticker:
            ext = ".webp" if not target_msg.sticker.is_animated else ".tgs"
            file_name = f"sticker_{message_id}{ext}"
            file_size = target_msg.sticker.file_size or 0
        else:
            await status_msg.edit_text(f"⚠️ 暂不支持该媒体类型：`{target_msg.media}`")
            return

        # 4. 文件大小限制
        # Pyrogram 使用 MTProto 协议，Bot 发送上限为 2GB
        SIZE_LIMIT = 2 * 1024 * 1024 * 1024  # 2 GB
        if file_size > SIZE_LIMIT:
            size_gb = file_size / 1024 / 1024 / 1024
            await status_msg.edit_text(
                f"⚠️ 文件过大（{size_gb:.1f} GB），超过 Telegram 单文件上传限制（2 GB）。\n"
                "无法处理此文件。"
            )
            return

        # 5. 先尝试直接转发（最快，不消耗流量）
        try:
            await status_msg.edit_text("⚡ 正在尝试直接转发...")
            forwarded = await user_client.forward_messages(
                chat_id=message.chat.id,
                from_chat_id=target_msg.chat.id,
                message_ids=target_msg.id
            )
            if forwarded:
                caption = target_msg.caption or ""
                await status_msg.edit_text(
                    f"✅ **发送成功！**\n\n"
                    f"📄 {file_name}"
                    + (f"\n📝 {caption[:200]}" if caption else "")
                )
                return
        except Exception:
            # 转发失败（禁止转发 / 跨账号限制），走下载后重新上传流程
            pass

        # 6. 下载后发送（绕过禁止转发限制）
        await status_msg.edit_text(f"⬇️ 正在下载...\n📄 {file_name}")

        temp_dir = "downloads"
        os.makedirs(temp_dir, exist_ok=True)

        start_time = time.time()
        download_path = await user_client.download_media(
            target_msg,
            file_name=os.path.join(temp_dir, file_name),
            block=True,
            progress=_progress,
            progress_args=(status_msg, f"⬇️ 正在下载 {file_name}", start_time)
        )

        if not download_path or not os.path.exists(download_path):
            await status_msg.edit_text("❌ 下载失败，请稍后重试。")
            return

        # 7. 发送给用户（用 Bot 账号发送，Bot 发送上限 2GB）
        await status_msg.edit_text("⬆️ 正在发送...")
        caption = target_msg.caption or ""

        start_time = time.time()

        if target_msg.video:
            await client.send_video(
                message.chat.id,
                download_path,
                caption=caption[:1024] if caption else None,
                progress=_progress,
                progress_args=(status_msg, "⬆️ 正在发送视频", start_time)
            )
        elif target_msg.photo:
            await client.send_photo(
                message.chat.id,
                download_path,
                caption=caption[:1024] if caption else None,
            )
        elif target_msg.audio:
            await client.send_audio(
                message.chat.id,
                download_path,
                caption=caption[:1024] if caption else None,
            )
        elif target_msg.voice:
            await client.send_voice(
                message.chat.id,
                download_path,
            )
        else:
            # document / video_note / sticker 等统一用 send_document
            await client.send_document(
                message.chat.id,
                download_path,
                caption=caption[:1024] if caption else None,
                force_document=True,
                progress=_progress,
                progress_args=(status_msg, "⬆️ 正在发送文件", start_time)
            )

        await status_msg.edit_text(f"✅ 发送完成！\n📄 {file_name}")

    except Exception as e:
        err_text = str(e)
        hint = ""
        if "CHAT_FORWARDS_RESTRICTED" in err_text or "forwards_restricted" in err_text.lower():
            hint = "\n\n⚠️ 该内容设置了禁止转发，且下载后重新发送也受到限制。"
        elif "FILE_REFERENCE" in err_text:
            hint = "\n\n⚠️ 文件引用已过期，请稍后重试。"
        await status_msg.edit_text(f"❌ 处理失败：`{err_text[:300]}`{hint}")

    finally:
        # 清理临时文件
        if download_path and os.path.exists(download_path):
            try:
                os.remove(download_path)
            except Exception:
                pass
