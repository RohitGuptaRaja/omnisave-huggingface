import os
import re
import gc
import json
import html
import uuid
import glob
import asyncio
import subprocess
import urllib.parse
import requests
import instaloader
import yt_dlp
import imageio_ffmpeg
from custom_logger import logger

os.makedirs("downloads", exist_ok=True)
os.makedirs("downloads/bin", exist_ok=True)

# ⏱️ AUTO-DELETE TIMER (60 Seconds)
AUTO_DELETE_SECONDS = 60

# 🔥 FALSE = True 1080p (1080x1920) DASH Video + Audio FFmpeg Merge
ZERO_BANDWIDTH_MODE = False

# 🔑 Render Environment Variable se IG_SESSIONID uthayega
IG_SESSIONID = os.getenv("IG_SESSIONID", "").strip()

# ==========================================
# 🔥 60-SECOND AUTO CLEANUP TIMER
# ==========================================
async def auto_cleanup_file(file_path, delay=AUTO_DELETE_SECONDS):
    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            gc.collect()
            logger.info(f"🧹 AUTO-CLEANUP: {delay}s pure hue! Server se HD file delete ho gayi -> {file_path}")
    except Exception as e:
        logger.error(f"⚠️ Auto-Cleanup Error: {e}")

# ==========================================
# HELPER: CLEAN URL & EXTRACT SHORTCODE
# ==========================================
def _clean_instagram_url(raw_url):
    m = re.search(r'(https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+))', raw_url)
    if m:
        shortcode = m.group(2)
        return f"https://www.instagram.com/reel/{shortcode}/", shortcode
    clean = raw_url.split("?")[0].strip()
    shortcode = clean.rstrip("/").split("/")[-1]
    return clean, shortcode

# ==========================================
# HELPER: ENSURE STANDARD FFMPEG BINARY NAME
# ==========================================
def _get_ffmpeg_binary():
    real_exe = imageio_ffmpeg.get_ffmpeg_exe()
    standard_exe = os.path.abspath("downloads/bin/ffmpeg")
    if not os.path.exists(standard_exe):
        try:
            os.symlink(real_exe, standard_exe)
        except Exception:
            return real_exe
    return standard_exe

# ==========================================
# HELPER: BUILD COOKIEJAR FOR YT-DLP
# ==========================================
def _get_cookiefile_path():
    if os.path.isfile("cookies.txt") and os.path.getsize("cookies.txt") > 20:
        return "cookies.txt"

    if IG_SESSIONID:
        cookie_path = "downloads/ig_runtime_cookies.txt"
        clean_sid = IG_SESSIONID
        lines = [
            "# Netscape HTTP Cookie File",
            f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{clean_sid}",
            f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tds_user_id\t{urllib.parse.unquote(clean_sid).split(':')[0]}"
        ]
        try:
            with open(cookie_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            return cookie_path
        except Exception as e:
            logger.error(f"⚠️ Cookie file creation error: {e}")
    return None

# ==========================================
# HELPER: PROBE VIDEO RESOLUTION + AUDIO CHECK
# ==========================================
def _probe_media(file_path):
    try:
        if not file_path or not os.path.isfile(file_path):
            return 0, 0, False

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-i", file_path],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stderr + result.stdout
        width, height = 0, 0
        has_audio = False

        for line in output.splitlines():
            if "Video:" in line and width == 0:
                match = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
                if match:
                    width, height = int(match.group(1)), int(match.group(2))
            if "Audio:" in line:
                has_audio = True

        return width, height, has_audio
    except Exception as e:
        logger.error(f"❌ Media Probe Error: {e}")
    return 0, 0, False

# ==========================================
# HELPER: DOWNLOAD STREAM & FFMPEG MERGE
# ==========================================
def _download_file(url, dest_path, custom_headers=None):
    headers = custom_headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.instagram.com/"
    }
    with requests.get(url, headers=headers, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)
    return os.path.exists(dest_path) and os.path.getsize(dest_path) > 5000

def _merge_video_audio_files(video_file, audio_file, height_label="1080p"):
    uid = uuid.uuid4().hex[:8]
    final_mp4 = f"downloads/insta_merged_{uid}.mp4"
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    try:
        logger.info(f"🔧 FFmpeg Audio+Video Muxing ({height_label} HD)...")
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i", video_file,
            "-i", audio_file,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            "-movflags", "+faststart",
            final_mp4
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=40)

        if os.path.exists(final_mp4) and os.path.getsize(final_mp4) > 50000:
            w, h, has_aud = _probe_media(final_mp4)
            size_mb = round(os.path.getsize(final_mp4) / (1024 * 1024), 2)
            logger.info(f"✅ FFmpeg MUX SUCCESS: {w}x{h} | AUDIO={has_aud} ({size_mb} MB) -> {final_mp4}")
            return final_mp4
    except Exception as e:
        logger.error(f"❌ FFmpeg Mux Error: {e}")
        if os.path.exists(final_mp4):
            try: os.remove(final_mp4)
            except OSError: pass
    return None

# ==========================================
# 🔥 SMART AUDIO RECOVERY FOR SILENT 1080P VIDEOS
# ==========================================
def _recover_and_attach_audio(url, silent_video_path, current_formats, height_label="1080p"):
    logger.warning("🔊 AUDIO RECOVERY STARTED: 1080p video me audio nahi hai, audio stream dhoondh rahe hain...")
    candidate_audio_urls = []

    for f in (current_formats or []):
        f_id = str(f.get("format_id") or "")
        f_url = f.get("url")
        if not f_url:
            continue
        if f.get("acodec") not in (None, "none"):
            candidate_audio_urls.insert(0, (f_id, f_url))
        elif not f_id.startswith("dash-"):
            candidate_audio_urls.append((f_id, f_url))

    try:
        pub_opts = {
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.instagram.com/"
            }
        }
        with yt_dlp.YoutubeDL(pub_opts) as ydl_pub:
            pub_info = ydl_pub.extract_info(url, download=False)
            for pf in (pub_info.get("formats") or []):
                pf_id = str(pf.get("format_id") or "")
                pf_url = pf.get("url")
                if not pf_url:
                    continue
                if pf.get("acodec") not in (None, "none") or pf_id.endswith("a"):
                    candidate_audio_urls.insert(0, (f"pub_{pf_id}", pf_url))
                elif not pf_id.startswith("dash-"):
                    candidate_audio_urls.append((f"pub_{pf_id}", pf_url))
    except Exception as e:
        logger.warning(f"⚠️ Public Audio Lookup Warning: {e}")

    for src_id, audio_url in candidate_audio_urls:
        uid = uuid.uuid4().hex[:8]
        temp_audio = f"downloads/temp_aud_{uid}.mp4"
        try:
            logger.info(f"⬇️ Trying Audio Source '{src_id}'...")
            if _download_file(audio_url, temp_audio):
                _, _, src_has_audio = _probe_media(temp_audio)
                if src_has_audio:
                    logger.info(f"🎯 Valid Audio Stream found in '{src_id}'! Merging with 1080p video...")
                    merged_path = _merge_video_audio_files(silent_video_path, temp_audio, height_label=height_label)
                    try: os.remove(temp_audio)
                    except OSError: pass

                    if merged_path:
                        try: os.remove(silent_video_path)
                        except OSError: pass
                        return merged_path
                else:
                    logger.warning(f"⚠️ Source '{src_id}' me bhi audio track nahi mila, trying next...")
        except Exception as e:
            logger.warning(f"⚠️ Audio source '{src_id}' error: {e}")
        finally:
            if os.path.exists(temp_audio):
                try: os.remove(temp_audio)
                except OSError: pass

    logger.warning("⚠️ Koi alag audio track nahi mila (Shayad Reel originally muted hai).")
    return silent_video_path

# ==========================================
# ENGINE 1: YT-DLP TRUE 1080P + AUDIO VERIFIER & RECOVERY
# ==========================================
def get_hd_via_ytdlp_sync(url):
    uid = uuid.uuid4().hex[:8]
    prefix = os.path.join("downloads", f"instagram_{uid}")
    output_template = prefix + ".%(ext)s"

    logger.info("⚡ ENGINE 1: yt-dlp True 1080p video + audio downloader started")

    try:
        ffmpeg_exe = _get_ffmpeg_binary()
        cookie_file = _get_cookiefile_path()

        ydl_opts = {
            "format": "bv[height>=720]+ba/b[height>=720]/bv+ba/b",
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "ffmpeg_location": ffmpeg_exe,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 2,
            "fragment_retries": 2,
            "socket_timeout": 20,
            "source_address": "0.0.0.0",
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.instagram.com/"
            }
        }

        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
            logger.info("🍪 Authenticated Instagram Session Active in yt-dlp!")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        formats = info.get("formats") or []
        logger.info(f"📋 YT-DLP FORMAT COUNT: {len(formats)}")

        final_path = info.get("filepath") if isinstance(info, dict) else None
        if not final_path or not os.path.isfile(final_path):
            matches = [
                p for p in glob.glob(prefix + ".*")
                if not p.endswith((".part", ".ytdl")) and not re.search(r"\.f\d+\.", os.path.basename(p))
            ]
            if matches:
                final_path = max(matches, key=os.path.getsize)

        if final_path and os.path.isfile(final_path):
            w_actual, h_actual, has_audio = _probe_media(final_path)
            logger.info(f"📺 ACTUAL MP4 CHECK: {w_actual}x{h_actual} | AUDIO={has_audio}")

            if not has_audio:
                final_path = _recover_and_attach_audio(
                    url,
                    final_path,
                    formats,
                    height_label=f"{max(w_actual, h_actual)}p"
                )
                w_actual, h_actual, has_audio = _probe_media(final_path)
                logger.info(f"📺 POST-RECOVERY MP4 CHECK: {w_actual}x{h_actual} | AUDIO={has_audio}")

            file_size = os.path.getsize(final_path)
            if file_size > 50000:
                size_mb = round(file_size / (1024 * 1024), 2)
                logger.info(f"✅ ENGINE 1 SUCCESS: Instagram 1080p video ready ({size_mb} MB) -> {final_path}")
                return final_path

    except Exception as e:
        logger.error(f"❌ yt-dlp Download Error: {type(e).__name__}: {e}")

    for leftover in glob.glob(prefix + ".*"):
        try:
            if os.path.isfile(leftover):
                os.remove(leftover)
        except OSError:
            pass

    return None

# ==========================================
# ENGINE 2: PUBLIC INSTAGRAM API FALLBACK (Bypasses JSONDecodeError)
# ==========================================
def get_hd_via_public_api_sync(url):
    logger.info("⚙️ ENGINE 2: Public Instagram API Fallback started...")
    try:
        clean_url, shortcode = _clean_instagram_url(url)
        api_url = f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "Accept": "application/json,text/html",
            "Referer": "https://www.instagram.com/"
        }
        resp = requests.get(api_url, headers=headers, timeout=10)
        if resp.status_code == 200 and resp.text.strip().startswith("{"):
            data = resp.json()
            items = data.get("items") or data.get("graphql", {}).get("shortcode_media", {})
            vid_url = items.get("video_url") if isinstance(items, dict) else None
            if vid_url:
                uid = uuid.uuid4().hex[:8]
                out_path = f"downloads/ig_pub_{uid}.mp4"
                if _download_file(vid_url, out_path):
                    logger.info(f"✅ ENGINE 2 PUBLIC API SUCCESS -> {out_path}")
                    return out_path
    except Exception as e:
        logger.warning(f"⚠️ Engine 2 Public API warning: {e}")
    return None

# ==========================================
# STEP 1: INSTALOADER (Photos & Video Fallback)
# ==========================================
def get_instaloader_media_sync(shortcode):
    logger.info("🕵️ Instaloader fallback check kar rahe hain...")
    try:
        L = instaloader.Instaloader()
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        if not post.is_video:
            media_list = []
            if post.typename == "GraphSidecar":
                for node in post.get_sidecar_nodes():
                    if not node.is_video:
                        media_list.append({"url": node.display_url, "type": "image", "is_local": False})
            else:
                media_list.append({"url": post.url, "type": "image", "is_local": False})
            return media_list
        else:
            # Video fallback via Instaloader
            v_url = post.video_url
            if v_url:
                uid = uuid.uuid4().hex[:8]
                out_path = f"downloads/ig_insta_{uid}.mp4"
                if _download_file(v_url, out_path):
                    logger.info(f"✅ INSTALOADER VIDEO SUCCESS -> {out_path}")
                    return out_path
    except Exception as e:
        logger.error(f"⚠️ Instaloader Error: {e}")
    return None

# ==========================================
# MAIN FUNCTION: MULTI-ENGINE INSTAGRAM SELECTOR
# ==========================================
async def get_instagram_media(url):
    clean_url, shortcode = _clean_instagram_url(url)

    logger.info(f"📱 DETECTED: Instagram -> {clean_url} (Shortcode: {shortcode})")
    loop = asyncio.get_running_loop()

    # ENGINE 1: YT-DLP TRUE 1080P + AUDIO RECOVERY
    engine1_path = await loop.run_in_executor(None, get_hd_via_ytdlp_sync, clean_url)
    if engine1_path and os.path.exists(engine1_path):
        w1, h1, has_aud1 = _probe_media(engine1_path)
        size1 = round(os.path.getsize(engine1_path) / (1024 * 1024), 2)
        logger.info(f"📊 FINAL VIDEO QUALITY: {w1}x{h1} | AUDIO={has_aud1} ({size1} MB)")

        asyncio.create_task(auto_cleanup_file(engine1_path))
        return [{"url": engine1_path, "type": "video", "is_local": True}]

    # ENGINE 2: PUBLIC INSTAGRAM API FALLBACK (Bypasses JSONDecodeError)
    engine2_path = await loop.run_in_executor(None, get_hd_via_public_api_sync, clean_url)
    if engine2_path and os.path.exists(engine2_path):
        asyncio.create_task(auto_cleanup_file(engine2_path))
        return [{"url": engine2_path, "type": "video", "is_local": True}]

    # ENGINE 3: INSTALOADER (Photos / Carousel / Video Fallback)
    insta_res = await loop.run_in_executor(None, get_instaloader_media_sync, shortcode)
    if insta_res:
        if isinstance(insta_res, list):
            return insta_res
        elif isinstance(insta_res, str) and os.path.exists(insta_res):
            asyncio.create_task(auto_cleanup_file(insta_res))
            return [{"url": insta_res, "type": "video", "is_local": True}]

    logger.error("❌ Instagram extraction failed across all engines.")
    return None
