import os
import asyncio
import aiohttp
import urllib.parse
import uuid
import sys
import time
from urllib.parse import quote
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, BufferedInputFile
import yt_dlp

# 🔥 HUMARA CUSTOM LOGGER AUR MODULES IMPORT KAR RAHE HAIN 🔥
from custom_logger import logger
from platforms.youtube import get_youtube_media
from platforms.pinterest import get_pinterest_media
from platforms.instagram import get_instagram_media
from platforms.facebook import get_facebook_media
from platforms.twitter import get_twitter_media
from platforms.snapchat import get_snapchat_media
from platforms.tiktok import get_tiktok_media

# ==========================================
# 1. CONFIGURATION
# ==========================================
DOWNLOADER_BOT_TOKEN = "8974054448:AAGne5hzMm0k13w4d-mpm8sTsaOyWrbK22E"  
FEEDBACK_BOT_TOKEN = "8999937366:AAEl_EeczSIVN95zX7z2yk_8cFC_3xWnW5o"       
CHANNEL_USERNAME = "@thedailyvibes30m"
ADMIN_ID = 6273266301  

# 🔥 TERA CLOUDFLARE WORKER URL (0 MB Render Bandwidth Proxy for Social CDNs)
CF_WORKER_PROXY = "https://omnisave-cdn.rohitkumardajar.workers.dev/proxy"

bot = Bot(token=DOWNLOADER_BOT_TOKEN)
dp = Dispatcher()
feedback_bot = Bot(token=FEEDBACK_BOT_TOKEN)

ALLOWED_PLATFORMS = ["instagram.com", "facebook.com", "fb.watch", "twitter.com", "x.com", "snapchat.com", "pinterest.com", "pin.it", "tiktok.com", "youtube.com"]

logger.info("🤖 Main Server (Modular) start ho raha hai!")

def is_valid_platform(url):
    return any(p in url.lower() for p in ALLOWED_PLATFORMS)

def clean_social_url(url):
    if "instagram.com" in url and "?" in url:
        return url.split("?")[0]
    return url

# ==========================================
# 2. MAIN MEDIA ROUTER
# ==========================================
async def get_media_data(url):
    """Yeh router ab link ko uski specific file me bhej dega"""
    if "instagram.com" in url:
        return await get_instagram_media(url)
    elif "facebook.com" in url or "fb.watch" in url:
        return await get_facebook_media(url)
    elif "twitter.com" in url or "x.com" in url:
        return await get_twitter_media(url)
    elif "snapchat.com" in url:
        return await get_snapchat_media(url)
    elif "pinterest.com" in url or "pin.it" in url:
        return await get_pinterest_media(url)
    elif "tiktok.com" in url:
        return await get_tiktok_media(url)
    elif "youtube.com" in url:
        return await get_youtube_media(url)
    else:
        logger.error(f"❌ Unknown platform link: {url}")
        return None

def download_yt_dlp(url, msg_id):
    logger.info(f"⬇️ FILE DOWNLOAD: Direct file download call (ID: {msg_id})")
    file_name = f"video_{msg_id}.mp4"
    ydl_opts = {'format': 'best', 'outtmpl': file_name, 'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return file_name
    except: return None

# ==========================================
# 3. TELEGRAM BOT LOGIC
# ==========================================
async def check_membership(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    logger.info(f"🤖 [TG BOT] User {message.from_user.id} ne /start bheja.")
    text = (
        "🤍 <b>Hi! I am the Premium Media Downloader.</b>\n\n"
        "🔗 <b>Supported Platforms:</b>\n"
        "• Instagram\n• Facebook\n• Twitter\n• Snapchat\n• Pinterest\n• TikTok\n• Youtube\n\n"
        f"⚠️ <i>Note: Kripya pehle {CHANNEL_USERNAME} join karein!</i>"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message()
async def process_link(message: types.Message):
    url = message.text
    logger.info(f"🤖 [TG BOT] Bot ko naya link mila: {url}")
    if not url.startswith("http"):
        return await message.reply("Kripya ek valid link bhejein.")
        
    url = clean_social_url(url)
    if not is_valid_platform(url):
        return await message.reply("🚫 Error: Main sirf Youtube, Instagram, Facebook, Twitter, Snapchat, Pinterest, aur TikTok support karta hoon.")

    if not await check_membership(message.from_user.id):
        return await message.reply(f"🚫 Kripya pehle hamara channel {CHANNEL_USERNAME} join karein.")

    status_msg = await message.reply("⏳ Fetching media...")
    caption = f"🎬 <b>Media Downloaded</b>\n🔗 <a href='{url}'>Original Link</a>"

    media_data = await get_media_data(url)
    
    if media_data == "NO_MEDIA":
        await status_msg.edit_text("❌ Is post par koi Photo ya Video available nahi hai (Text-only post).")
        return
    
    if media_data:
        local_files = []
        try:
            if len(media_data) == 1:
                m = media_data[0]
                media_source = FSInputFile(m["url"]) if m.get("is_local") else m["url"]
                if m.get("is_local"): local_files.append(m["url"])
                
                if m["type"] == "video":
                    await bot.send_video(chat_id=message.chat.id, video=media_source, caption=caption, parse_mode="HTML")
                else:
                    await bot.send_photo(chat_id=message.chat.id, photo=media_source, caption=caption, parse_mode="HTML")
            else:
                media_group = []
                for i, m in enumerate(media_data[:10]):
                    cap = caption if i == 0 else None
                    media_source = FSInputFile(m["url"]) if m.get("is_local") else m["url"]
                    if m.get("is_local"): local_files.append(m["url"])
                    
                    if m["type"] == "video":
                        media_group.append(InputMediaVideo(media=media_source, caption=cap, parse_mode="HTML"))
                    else:
                        media_group.append(InputMediaPhoto(media=media_source, caption=cap, parse_mode="HTML"))
                await bot.send_media_group(chat_id=message.chat.id, media=media_group)
            return await status_msg.delete()
        except Exception as e:
            logger.error(f"❌ [TG BOT] Telegram Upload Error: {e}")
        finally:
            for file_path in local_files:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"🗑️ TG BOT Storage saaf: {file_path} auto-deleted!")

    loop = asyncio.get_running_loop()
    file_path = await loop.run_in_executor(None, download_yt_dlp, url, message.message_id)

    if file_path and os.path.exists(file_path):
        try:
            await status_msg.edit_text("📤 Uploading video...")
            video = FSInputFile(file_path)
            await bot.send_video(chat_id=message.chat.id, video=video, caption=caption, parse_mode="HTML")
            await status_msg.delete()
        except:
            await status_msg.edit_text("❌ Telegram Error: File size badi hai.")
        finally:
            if os.path.exists(file_path): os.remove(file_path)
    else:
        await status_msg.edit_text("❌ Download failed. Kripya check karein ki link valid hai.")

# ==========================================
# 4. WEB SERVER (AIOHTTP) & AUTO CLEANER
# ==========================================
async def auto_clean_downloads():
    while True:
        await asyncio.sleep(60)
        folder = "downloads"
        if os.path.exists(folder):
            curr_time = time.time()
            for filename in os.listdir(folder):
                filepath = os.path.join(folder, filename)
                if os.path.getmtime(filepath) < curr_time - 300:
                    try:
                        os.remove(filepath)
                        logger.info(f"🧹 Web Server Storage saaf: {filepath} auto-deleted (Expired)!")
                    except Exception:
                        pass

def serve_html_page(filename):
    try:
        with open(f'templates/{filename}', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: templates/{filename} file nahi mili!"

async def handle_view_logs(request):
    try:
        with open("omni_logs.txt", "r", encoding="utf-8") as f:
            logs = f.read()
        if not logs: logs = "Logs clear hain."
    except FileNotFoundError:
        logs = "Logs file nahi bani."
    
    html_content = f"<html><body style='background:#121212; color:#00FF00; font-family:monospace; padding: 20px;'><pre>{logs}</pre><script>setInterval(() => window.location.reload(), 3000);</script></body></html>"
    return web.Response(text=html_content, content_type='text/html')

async def handle_clear_logs(request):
    with open("omni_logs.txt", "w", encoding="utf-8") as f:
        f.write("🔥 GOD MODE LOGS CLEARED 🔥\n==============================\n")
    return web.Response(text="Logs Cleared! Go back to /logs")

async def handle_home(request): return web.Response(text=serve_html_page('index.html'), content_type='text/html')
async def handle_instagram(request): return web.Response(text=serve_html_page('instagram.html'), content_type='text/html')
async def handle_facebook(request): return web.Response(text=serve_html_page('facebook.html'), content_type='text/html')
async def handle_twitter(request): return web.Response(text=serve_html_page('twitter.html'), content_type='text/html')
async def handle_snapchat(request): return web.Response(text=serve_html_page('snapchat.html'), content_type='text/html')
async def handle_pinterest(request): return web.Response(text=serve_html_page('pinterest.html'), content_type='text/html')
async def handle_tiktok(request): return web.Response(text=serve_html_page('tiktok.html'), content_type='text/html')
async def handle_youtube(request): return web.Response(text=serve_html_page('youtube.html'), content_type='text/html')

async def handle_api_download(request):
    try:
        user_agent = request.headers.get('User-Agent', '')
        if not user_agent or "python" in user_agent.lower() or "curl" in user_agent.lower() or "postman" in user_agent.lower():
            return web.json_response({"success": False, "message": "Access Denied: Bot detected."})

        client_token = request.headers.get('X-Omni-Token')
        if not client_token or client_token != "omni_secret_key_2026":
            return web.json_response({"success": False, "message": "Unauthorized Request."})

        data = await request.json()
        url = clean_social_url(data.get('url', ''))
        if not url: return web.json_response({"success": False, "message": "No URL provided."})
        
        if not is_valid_platform(url): return web.json_response({"success": False, "message": "Invalid platform."})
            
        media_data = await get_media_data(url)

        if media_data == "NO_MEDIA":
            return web.json_response({"success": False, "message": "Photo/Video not available on this post."})
            
        if media_data: return web.json_response({"success": True, "link": media_data[0]["url"], "media": media_data})
        else: return web.json_response({"success": False, "message": "Failed to fetch media."})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)})

async def handle_file_download(request):
    target_url = request.query.get("url")
    if not target_url: return web.Response(status=400, text="No URL")
    
    # 🚀 1. TIKTOK DIRECT HD SERVERS (tikcdn.io / rapidcdn.app):
    # In servers me pehle se 'Content-Disposition: attachment' hota hai.
    # Inhe sidha redirect karne se Cloudflare Error 525 nahi aata aur Render ka 0 MB lagta hai!
    if any(d in target_url.lower() for d in ("tikcdn.io", "ssstik.io", "rapidcdn.app", "snaptik")):
        logger.info(f"⚡ [0 MB RENDER DATA] Direct TikTok HD CDN Redirect -> {target_url[:55]}...")
        return web.HTTPFound(target_url)

    # 🚀 2. OTHER SOCIAL CDNs (FB, Twitter, Pinterest, Snapchat, Insta Photos) -> Cloudflare Worker:
    if target_url.startswith("http://") or target_url.startswith("https://"):
        logger.info(f"⚡ [0 MB RENDER DATA] Redirecting to Cloudflare Worker CDN -> {target_url[:55]}...")
        cf_redirect_url = f"{CF_WORKER_PROXY}?url={quote(target_url, safe='')}"
        return web.HTTPFound(cf_redirect_url)

    # 3. LOCAL MERGED HD FILES (Instagram 1080p & YouTube):
    if target_url.startswith("downloads/") and os.path.exists(target_url):
        size_mb = round(os.path.getsize(target_url) / (1024 * 1024), 2)
        logger.warning(f"⚠️ [RENDER BANDWIDTH USED: {size_mb} MB] Serving 1080p HD local file -> {target_url}")
        ext = "mp4" if "mp4" in target_url.lower() else "jpg"
        filename = f"OmniSave_{uuid.uuid4().hex[:6]}.{ext}"
        content_type = "video/mp4" if ext == "mp4" else "image/jpeg"
        
        response = web.FileResponse(target_url, headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': content_type
        })
        return response

    async with aiohttp.ClientSession(headers={'User-Agent': 'Mozilla/5.0'}) as session:
        try:
            async with session.get(target_url) as resp:
                ext = "mp4" if "video" in resp.headers.get("Content-Type", "") else "jpg"
                filename = f"OmniSave_{uuid.uuid4().hex[:6]}.{ext}"
                response = web.StreamResponse(status=200, headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Content-Type': resp.headers.get("Content-Type", "application/octet-stream")})
                await response.prepare(request)
                async for chunk in resp.content.iter_chunked(8192): await response.write(chunk)
                return response
        except: return web.Response(status=500, text="Failed.")

# ==========================================
# 5. RESTORED: FEEDBACK API & PWA ENDPOINTS
# ==========================================
async def handle_feedback(request):
    try:
        reader = await request.multipart()
        rating, message_text, screenshot, filename = "0", "No message", None, None
        while True:
            field = await reader.next()
            if field is None: break
            if field.name == 'rating': rating = (await field.read(decode=True)).decode('utf-8')
            elif field.name == 'message': message_text = (await field.read(decode=True)).decode('utf-8')
            elif field.name == 'screenshot': 
                filename = field.filename
                if filename: screenshot = await field.read(decode=False)
        caption_text = f"🌟 <b>New Website Feedback!</b>\n\n⭐ <b>Rating:</b> {rating}/5\n📝 <b>Message:</b> {message_text}"
        if screenshot:
            file_input = BufferedInputFile(screenshot, filename=filename)
            await feedback_bot.send_photo(chat_id=ADMIN_ID, photo=file_input, caption=caption_text, parse_mode="HTML")
        else:
            await feedback_bot.send_message(chat_id=ADMIN_ID, text=caption_text, parse_mode="HTML")
        return web.json_response({"success": True, "message": "Feedback submitted!"})
    except Exception as e:
        logger.error(f"❌ Feedback Error: {e}")
        return web.json_response({"success": False, "message": "Server error."})

async def handle_manifest(request):
    manifest = {
        "name": "OmniSave - Video Downloader",
        "short_name": "OmniSave",
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#FAFAFA",
        "theme_color": "#7C3AED",
        "description": "Free All-in-One Social Media Video & Photo Downloader",
        "icons": [
            {
                "src": "https://i.postimg.cc/RVtkMpXK/file-00000000606482118d38a9c54bb54a98.png", 
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": "https://i.postimg.cc/RVtkMpXK/file-00000000606482118d38a9c54bb54a98.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ]
    }
    return web.json_response(manifest)

async def handle_sw(request):
    sw_code = """
    const CACHE_NAME = 'omnisave-cache-v1';
    self.addEventListener('install', (event) => {
        self.skipWaiting();
    });
    self.addEventListener('activate', (event) => {
        event.waitUntil(self.clients.claim());
    });
    self.addEventListener('fetch', (event) => {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request))
        );
    });
    """
    return web.Response(text=sw_code, content_type='application/javascript')

# ==========================================
# 6. SERVER RUNNER
# ==========================================
async def main():
    asyncio.create_task(dp.start_polling(bot))
    
    asyncio.create_task(auto_clean_downloads())
    
    app = web.Application()
    
    os.makedirs("downloads", exist_ok=True)
    app.router.add_static('/downloads/', path='downloads', name='downloads')
    
    app.router.add_get('/', handle_home)
    app.router.add_get('/youtube', handle_youtube)
    app.router.add_get('/instagram', handle_instagram)
    app.router.add_get('/facebook', handle_facebook)
    app.router.add_get('/twitter', handle_twitter)
    app.router.add_get('/snapchat', handle_snapchat)
    app.router.add_get('/pinterest', handle_pinterest) 
    app.router.add_get('/tiktok', handle_tiktok)
    app.router.add_get('/logs', handle_view_logs)
    app.router.add_get('/clear_logs', handle_clear_logs)
    app.router.add_post('/api/download', handle_api_download)
    app.router.add_get('/download_file', handle_file_download)
    
    app.router.add_post('/api/feedback', handle_feedback)
    app.router.add_get('/manifest.json', handle_manifest)
    app.router.add_get('/sw.js', handle_sw)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 7860))
    site = web.TCPSite(runner, '0.0.0.0', port)
    logger.info(f"Server starting on port {port}...")
    await site.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
