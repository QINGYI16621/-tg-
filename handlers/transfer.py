import os
import time
import math
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
import config
from database import db

# Progress callback for download/upload
async def progress(current, total, message, action_text, start_time):
    now = time.time()
    diff = now - start_time
    if round(diff % 3.00) == 0 or current == total:  # Update every 3 seconds
        percentage = current * 100 / total
        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff) * 1000
        time_to_completion = round((total - current) / speed) * 1000 if speed > 0 else 0
        estimated_total_time = elapsed_time + time_to_completion

        elapsed_time_str = "{:.0f}s".format(elapsed_time / 1000)
        estimated_total_time_str = "{:.0f}s".format(estimated_total_time / 1000)

        progress_str = "[{0}{1}] {2}%\n".format(
            ''.join(["▰" for i in range(math.floor(percentage / 10))]),
            ''.join(["▱" for i in range(10 - math.floor(percentage / 10))]),
            round(percentage, 2)
        )

        tmp = progress_str + \
              "{0} of {1}\n".format(humanbytes(current), humanbytes(total)) + \
              "Speed: {0}/s\n".format(humanbytes(speed)) + \
              "ETA: {0}".format(estimated_total_time_str)

        try:
            await message.edit_text(f"{action_text}...\n{tmp}")
        except:
            pass

def humanbytes(size):
    if not size:
        return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + Dic_powerN[n] + 'B'

@Client.on_message(filters.regex(r"https://t\.me/(c/|)(\d+|[\w\d_]+)/(\d+)") & filters.private, group=0)
async def transfer_handler(client: Client, message: Message):
    """Handle Telegram links - 管理员专用（存储+加密流程）"""
    user_id = message.from_user.id
    
    # 非管理员：不处理，让消息继续传播到 public_transfer.py（group=5）
    if user_id != client.admin_id:
        message.continue_propagation()
        return
    
    url = message.text.strip()
    user_client = client.user_client  # 只用主账号
    
    # 1. Parse Link
    try:
        if "t.me/c/" in url:
            # Private: https://t.me/c/1234567890/123
            parts = url.split("t.me/c/")[1].split("/")
            chat_id = int("-100" + parts[0])
            message_id = int(parts[1])
        else:
            # Public: https://t.me/channelname/123
            parts = url.split("t.me/")[1].split("/")
            chat_id = parts[0]
            message_id = int(parts[1])
    except:
        await message.reply_text("❌ 无法解析链接格式")
        return

    status_msg = await message.reply_text("🔎 正在解析消息...")

    try:
        # 尝试先解析 peer（如果失败则继续，后面会用 raw API 兜底）
        try:
            await user_client.get_chat(chat_id)
        except Exception as e:
            if "CHANNEL_PRIVATE" in str(e) or "INVITE_HASH" in str(e):
                # 不直接报错，继续尝试 raw API 获取消息
                pass

        # 2. Get Message (using User Client to bypass restrictions)
        # 先尝试直接获取
        target_msg = None
        try:
            target_msg = await user_client.get_messages(chat_id, message_id)
        except Exception as e1:
            # 如果直接获取失败，尝试通过 GetDiscussionMessage raw API
            # 适用于：频道评论区绑定的群组，可见但不允许加入
            err1 = str(e1)
            if any(k in err1 for k in ("CHANNEL_PRIVATE", "USER_NOT_PARTICIPANT", "PEER_ID_INVALID", "CHAT_FORBIDDEN")):
                await status_msg.edit_text("⚠️ 直接访问失败，尝试通过讨论区 API 获取...")
                try:
                    from pyrogram import raw
                    # chat_id 可能是用户名(str)或数字ID
                    if isinstance(chat_id, str):
                        peer = await user_client.resolve_peer(chat_id)
                    else:
                        peer = await user_client.resolve_peer(chat_id)
                    result = await user_client.invoke(
                        raw.functions.messages.GetDiscussionMessage(
                            peer=peer,
                            msg_id=message_id
                        )
                    )
                    if result and result.messages:
                        # GetDiscussionMessage 返回的消息对象需要转换
                        # 取第一条（即讨论消息本身）
                        raw_msg = result.messages[0]
                        # 用讨论群的 peer 和消息 ID 重新获取
                        discussion_peer = result.chats[0] if result.chats else None
                        if discussion_peer:
                            disc_chat_id = int("-100" + str(discussion_peer.id))
                            disc_msg_id = raw_msg.id
                            try:
                                target_msg = await user_client.get_messages(disc_chat_id, disc_msg_id)
                            except Exception:
                                # 直接用 raw 消息构造下载
                                target_msg = await user_client.get_messages(disc_chat_id, disc_msg_id)
                except Exception as e2:
                    # raw API 也失败，报原始错误
                    await status_msg.edit_text(
                        f"❌ 无法访问该频道/群组！\n\n"
                        f"频道 ID: `{chat_id}`\n\n"
                        f"**可能原因：**\n"
                        f"1. 账号未加入该频道/群组\n"
                        f"2. 该群组为私密且不允许加入\n"
                        f"3. 消息已被删除\n\n"
                        f"**解决方法：**\n"
                        f"• 如有邀请链接，发送 `https://t.me/+xxxxxx` 让 Bot 加入\n"
                        f"• 或先手动加入频道后重试\n\n"
                        f"错误: `{err1}`"
                    )
                    return
            else:
                raise e1
        
        if not target_msg or target_msg.empty:
            await status_msg.edit_text("❌ 无法获取消息 (可能被删除或无权限)")
            return

        # 3. Check for Media
        media_type = target_msg.media
        if not media_type:
            await status_msg.edit_text("⚠️ 这条消息没有包含文件 (仅仅是文本?)")
            return
        
        # Determine file name and metadata
        file_name = "unknown"
        mime_type = "unknown"
        file_size = 0
        file_id_ref = None # For Pyrogram file_id (user client's view)

        if target_msg.video:
            file_name = target_msg.video.file_name or f"video_{message_id}.mp4"
            mime_type = target_msg.video.mime_type
            file_size = target_msg.video.file_size
            file_id_ref = target_msg.video.file_id
        elif target_msg.document:
            file_name = target_msg.document.file_name or f"doc_{message_id}"
            mime_type = target_msg.document.mime_type
            file_size = target_msg.document.file_size
            file_id_ref = target_msg.document.file_id
        elif target_msg.photo:
            file_name = f"photo_{message_id}.jpg"
            mime_type = "image/jpeg"
            file_size = target_msg.photo.file_size
            file_id_ref = target_msg.photo.file_id
        elif target_msg.audio:
            file_name = target_msg.audio.file_name or f"audio_{message_id}.mp3"
            mime_type = target_msg.audio.mime_type
            file_size = target_msg.audio.file_size
            file_id_ref = target_msg.audio.file_id
        else:
            await status_msg.edit_text(f"⚠️ 暂不支持这种媒体类型: {media_type}")
            return

        # 4. Download (to memory or temp file)
        # For large files, Pyrogram automatically handles chunked download to disk
        start_time = time.time()
        await status_msg.edit_text(f"⬇️ 开始通过用户端接收文件...\n📄 {file_name}")
        
        # Download to a temporary path
        temp_dir = "downloads"
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)
            
        download_path = await user_client.download_media(
            target_msg,
            block=True,
            progress=progress,
            progress_args=(status_msg, "⬇️ 正在下载 (中转)", start_time)
        )
        
        if not download_path:
            await status_msg.edit_text("❌ 下载失败")
            return

        # ========== Telegram Stealth 存储 (防封+无限容量) ==========
        try:
            # 1. 修改文件 Hash (防秒传/防指纹封禁)
            # 在文件末尾追加 1-1024 个随机字节
            # 不会影响视频播放，但会彻底改变文件 Hash
            import random
            random_bytes = os.urandom(random.randint(1, 1024))
            with open(download_path, "ab") as f:
                f.write(random_bytes)
            
            # 2. 改名 (防文件名关键词封禁)
            # 使用随机字符重命名文件，但保持后缀以支持流媒体
            ext = os.path.splitext(file_name)[1]
            # 生成随机文件名 (16位)
            import secrets
            import string
            random_name = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))
            # 2.5 AES 全量加密 (核弹级防封)
            from services.crypto_utils import generate_key, encrypt_file
            import base64
            
            # 生成随机密钥
            aes_key = generate_key()
            aes_key_b64 = base64.b64encode(aes_key).decode('utf-8')
            
            # 加密文件名 (乱码.bin)
            import secrets
            import string
            random_name = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
            encrypted_filename = f"{random_name}.bin" # 使用 .bin 避免被识别为媒体
            encrypted_path = os.path.join(os.path.dirname(download_path), encrypted_filename)
            
            await status_msg.edit_text(f"🔒 正在进行 AES-256 全量加密...")
            
            # 执行加密 (CPU密集型，但在线程池运行以免阻塞)
            await asyncio.to_thread(encrypt_file, download_path, encrypted_path, aes_key)
            
            # 删除原文件节省空间
            if os.path.exists(download_path):
                os.remove(download_path)
            
            # 3. 上传到 私密存储频道
            start_time = time.time()
            await status_msg.edit_text(f"⬆️ 正在上传加密数据...")
            
            caption = target_msg.caption or target_msg.text or ""
            # 添加加密标识到 caption (可选，仅供管理员看)
            caption += "\n\n🔒 [AES-256 Encrypted]"
            
            # 始终使用 send_document 上传加密文件，防止 TG 尝试转码
            storage_msg = await client.send_document(
                config.STORAGE_CHANNEL_ID,
                encrypted_path,
                caption=caption,
                force_document=True,
                progress=progress,
                progress_args=(status_msg, "⬆️ 正在上传", start_time)
            )

            # 4. 存库
            if storage_msg:
                new_file_id = storage_msg.document.file_id
                new_file_unique_id = storage_msg.document.file_unique_id
                
                # 生成 16-32 位提取码
                key_length = secrets.randbelow(17) + 16
                access_key = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(key_length))
                
                db.add_file(
                    message_id=target_msg.id,
                    chat_id=target_msg.chat.id,
                    file_id=new_file_id,
                    file_unique_id=new_file_unique_id,
                    file_name=file_name,
                    caption=caption,
                    file_size=file_size,
                    mime_type="application/octet-stream",
                    storage_mode='telegram_stealth',
                    access_key=access_key,
                    is_encrypted=True,
                    encryption_key=aes_key_b64
                )
                
                # 清理加密文件
                if os.path.exists(encrypted_path):
                    os.remove(encrypted_path)
                
                response_text = (
                    f"✅ **加密存储成功！**\n\n"
                    f"📄 文件名: `{file_name}`\n"
                    f"🔐 状态: **AES-256 全加密** (防和谐)\n"
                    f"🔑 提取码: `{access_key}`\n\n"
                    f"使用方法:\n"
                    f"1. 发送 `{access_key}` 提取 (Bot 会自动解密播放)\n"
                    f"2. `/addto 合集名` 添加到合集"
                )
                await status_msg.edit_text(response_text)
            else:
                await status_msg.edit_text("❌ 上传失败，请重试")
            
            return
        
        except Exception as e:
            await status_msg.edit_text(f"❌ 存储失败: {e}")
            try:
                if 'download_path' in locals() and os.path.exists(download_path):
                    os.remove(download_path)
                if 'encrypted_path' in locals() and os.path.exists(encrypted_path):
                    os.remove(encrypted_path)
            except:
                pass
            return

    except Exception as e:
        await status_msg.edit_text(f"❌ 发生错误: {str(e)}")
        try:
            if 'download_path' in locals() and os.path.exists(download_path):
                os.remove(download_path)
        except:
            pass


# ========== 自动加入频道 ==========

@Client.on_message(filters.regex(r"https://t\.me/(\+|joinchat/)[\w\d_-]+") & filters.private)
async def join_channel_handler(client: Client, message: Message):
    """自动加入频道邀请链接 - 管理员专用"""
    user_id = message.from_user.id
    
    # 管理员专用
    if user_id != client.admin_id:
        await message.reply_text("⛔ 此机器人为私人使用，不对外开放。")
        return
    
    url = message.text.strip()
    user_client = client.user_client  # 用主账号
    
    status_msg = await message.reply_text("🔗 正在尝试加入频道...")
    
    try:
        # 提取邀请链接的 hash 部分
        if "t.me/+" in url:
            invite_hash = url.split("t.me/+")[1].split()[0]
            invite_link = f"https://t.me/+{invite_hash}"
        else:
            invite_hash = url.split("joinchat/")[1].split()[0]
            invite_link = f"https://t.me/joinchat/{invite_hash}"
        
        # 用 User Client 加入
        chat = await user_client.join_chat(invite_link)
        
        await status_msg.edit_text(
            f"✅ **成功加入频道！**\n\n"
            f"📢 频道名: **{chat.title}**\n"
            f"🆔 频道 ID: `{chat.id}`\n\n"
            f"现在你可以发送该频道的消息链接来下载了。"
        )
        
    except Exception as e:
        error_msg = str(e)
        if "USER_ALREADY_PARTICIPANT" in error_msg:
            await status_msg.edit_text("ℹ️ 已经是该频道的成员了！可以直接发送消息链接下载。")
        elif "INVITE_HASH_EXPIRED" in error_msg:
            await status_msg.edit_text("❌ 邀请链接已过期！")
        elif "INVITE_REQUEST_SENT" in error_msg:
            await status_msg.edit_text("⏳ 已发送加入请求，等待管理员批准...")
        else:
            await status_msg.edit_text(f"❌ 加入失败: `{e}`")
