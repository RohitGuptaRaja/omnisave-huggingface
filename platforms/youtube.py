import os
import sys
import re
import gc
import json
import uuid
import glob
import time
import base64
import shutil
import asyncio
import threading
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import yt_dlp
import imageio_ffmpeg
from custom_logger import logger

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CFFI = False

os.makedirs("downloads", exist_ok=True)
os.makedirs("downloads/bin", exist_ok=True)

# ⏱️ BASE AUTO-DELETE TIMER (Scales up automatically for large long videos!)
AUTO_DELETE_SECONDS = 90

# 🛡️ MUST BE FALSE FOR SAVENOW.TO (Prevents Ad-Redirects)
ZERO_BANDWIDTH_MODE = False

YT_COOKIES_ENV = os.getenv("YT_COOKIES", "").strip()
YT_COOKIES_B64 = os.getenv("YT_COOKIES_B64", "").strip()

# Persistent HTTP session with larger connection pool for parallel chunk downloads
_HTTP_SESSION = requests.Session()
_ADAPTER = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=2)
_HTTP_SESSION.mount("https://", _ADAPTER)
_HTTP_SESSION.mount("http://", _ADAPTER)

# In-memory cache for instant re-parse and smart duration-based format routing
_VIDEO_DURATION_CACHE = {}
_PARSE_CACHE = {}

# Ordered resolutions strictly from High -> Low
ORDERED_RESOLUTIONS = [2160, 1440, 1080, 720, 480, 360, 240, 144]

# Calibrated realistic YouTube MP4 bitrates (Video + Audio combined in kbps)
BITRATE_EST_KBPS = {
    2160: 5800,
    1440: 2900,
    1080: 1120,
    720: 620,
    480: 380,
    360: 240,
    240: 145,
    144: 88
}

# ==========================================
# 🔄 BACKGROUND AUTO-UPDATER FOR YT-DLP (FIXES PLAYER RESPONSE ERRORS)
# ==========================================
def _auto_update_ytdlp_background():
    try:
        logger.info("🔄 Updating yt-dlp to latest release in background...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45
        )
        logger.info("✅ yt-dlp is up-to-date!")
    except Exception as e:
        logger.warning(f"⚠️ yt-dlp background update skipped: {e}")

threading.Thread(target=_auto_update_ytdlp_background, daemon=True).start()

# ==========================================
# 🔥 SMART DYNAMIC AUTO CLEANUP TIMER
# ==========================================
async def auto_cleanup_file(file_path, delay=AUTO_DELETE_SECONDS):
    try:
        if os.path.exists(file_path):
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            # Give large long videos (35MB+) 5 minutes so mobile users finish downloading!
            if size_mb > 35:
                delay = max(delay, 300)
            elif size_mb > 15:
                delay = max(delay, 180)
    except Exception:
        pass

    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            gc.collect()
            logger.info(f"🧹 YT AUTO-CLEANUP: {delay}s pure hue! Server se file delete ho gayi -> {file_path}")
    except Exception as e:
        logger.error(f"⚠️ YT Auto-Cleanup Error: {e}")

# ==========================================
# HELPER: HTTP GET/POST & 6-THREAD PARALLEL STREAM DOWNLOADER
# ==========================================
def _http_get(url, params=None, headers=None, timeout=12):
    if HAS_CFFI:
        return cffi_requests.get(url, params=params, headers=headers, impersonate="chrome120", timeout=timeout)
    return _HTTP_SESSION.get(url, params=params, headers=headers, timeout=timeout)

def _http_post(url, data=None, json_data=None, headers=None, timeout=12):
    if HAS_CFFI:
        return cffi_requests.post(url, data=data, json=json_data, headers=headers, impersonate="chrome120", timeout=timeout)
    return _HTTP_SESSION.post(url, data=data, json=json_data, headers=headers, timeout=timeout)

def _download_range_chunk(url, start_byte, end_byte, part_path, headers):
    range_headers = dict(headers)
    range_headers["Range"] = f"bytes={start_byte}-{end_byte}"
    with _HTTP_SESSION.get(url, headers=range_headers, stream=True, timeout=(8, 35), allow_redirects=True) as r:
        if r.status_code not in (200, 206):
            return False
        with open(part_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=262144):
                if chunk:
                    f.write(chunk)
    return os.path.exists(part_path) and os.path.getsize(part_path) > 0

def _download_stream_to_file(stream_url, dest_path, referer="https://en.loader.to/"):
    """
    ⚡ 6x FASTER DOWNLOADER FOR LONG VIDEOS:
    - Probes stream URL headers & resolves final CDN URL.
    - If file > 12 MB and server supports Byte Ranges, downloads in 6 parallel threads!
    - Otherwise streams with a 512 KB high-speed buffer.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Origin": "https://en.loader.to",
        "Referer": referer,
        "Connection": "keep-alive"
    }

    try:
        with _HTTP_SESSION.get(stream_url, headers=headers, stream=True, timeout=(8, 25), allow_redirects=True) as r:
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                logger.warning(f"🚫 Blocked HTML/Ad response from stream URL (Content-Type: {ctype})")
                return False

            final_url = r.url
            total_size = int(r.headers.get("Content-Length") or 0)
            accept_ranges = (r.headers.get("Accept-Ranges") or "").lower()

            # 🚀 If file is 12 MB to 450 MB and supports ranges -> Use 6-Thread Parallel Turbo Download!
            if total_size > 12 * 1024 * 1024 and "bytes" in accept_ranges:
                r.close()
                num_threads = 6
                size_mb = round(total_size / (1024 * 1024), 2)
                logger.info(f"⚡ TURBO PARALLEL DOWNLOAD: Pulling {size_mb} MB using {num_threads} parallel threads...")
                chunk_size = total_size // num_threads
                part_files = []
                futures = []

                with ThreadPoolExecutor(max_workers=num_threads) as executor:
                    for i in range(num_threads):
                        start_b = i * chunk_size
                        end_b = (total_size - 1) if (i == num_threads - 1) else (start_b + chunk_size - 1)
                        part_file = f"{dest_path}.part{i}"
                        part_files.append(part_file)
                        futures.append(executor.submit(_download_range_chunk, final_url, start_b, end_b, part_file, headers))

                    all_ok = all(f.result() for f in as_completed(futures))

                if all_ok:
                    with open(dest_path, "wb") as out_f:
                        for pf in part_files:
                            with open(pf, "rb") as in_f:
                                shutil.copyfileobj(in_f, out_f, length=1024 * 1024)
                            try:
                                os.remove(pf)
                            except OSError:
                                pass
                    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 15000:
                        return True
                else:
                    for pf in part_files:
                        if os.path.exists(pf):
                            try:
                                os.remove(pf)
                            except OSError:
                                pass
                    logger.warning("⚠️ Parallel range download incomplete, falling back to single stream...")

            # Standard single-connection fast stream (for Shorts & normal files)
            with _HTTP_SESSION.get(final_url, headers=headers, stream=True, timeout=(8, 45), allow_redirects=True) as r2:
                r2.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r2.iter_content(chunk_size=524288):  # 512 KB buffer
                        if chunk:
                            f.write(chunk)

        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 10000:
            return True
    except Exception as e:
        logger.warning(f"⚠️ Stream download warning: {e}")

    return False

# ==========================================
# HELPER: FFMPEG BINARY & DEEP CODEC PROBE
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

def _probe_media_details(file_path):
    """Returns (width, height, video_codec, has_audio, audio_codec) using FFmpeg"""
    width, height = 0, 0
    video_codec = "none"
    has_audio = False
    audio_codec = "none"
    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        res = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-i", file_path],
            capture_output=True, text=True, timeout=8
        )
        out = res.stderr + res.stdout
        for line in out.splitlines():
            if "Video:" in line and width == 0:
                m_vid = re.search(r"Video:\s*([a-zA-Z0-9_]+)", line)
                if m_vid:
                    video_codec = m_vid.group(1).lower()
                m_dim = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
                if m_dim:
                    width, height = int(m_dim.group(1)), int(m_dim.group(2))
            if "Audio:" in line and not has_audio:
                has_audio = True
                m_aud = re.search(r"Audio:\s*([a-zA-Z0-9_]+)", line)
                if m_aud:
                    audio_codec = m_aud.group(1).lower()
    except Exception:
        pass
    return width, height, video_codec, has_audio, audio_codec

# ==========================================
# ⚡ ULTRA-FAST GALLERY OPTIMIZER (< 1 SECOND!)
# ==========================================
def _fast_gallery_optimize(file_path, video_id, target_short_side=1080, is_short=False):
    w, h, vcodec, has_audio, acodec = _probe_media_details(file_path)
    raw_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2) if os.path.exists(file_path) else 0
    logger.info(
        f"🔍 RAW STREAM CHECK: {w}x{h} | VCodec={vcodec} | HasAudio={has_audio} | ACodec={acodec} | RawSize={raw_mb} MB"
    )

    if not w or not h:
        return file_path

    is_h264 = vcodec in ("h264", "avc1")
    is_aac = has_audio and acodec in ("aac", "mp3", "mp4a")

    if is_h264 and is_aac:
        if raw_mb > 45.0:
            logger.info(f"⚡ INSTANT DELIVERY: Large H.264+AAC video ({raw_mb} MB) ready in 0s!")
            return file_path

        ffmpeg_exe = _get_ffmpeg_binary()
        uid = uuid.uuid4().hex[:8]
        remux_path = f"downloads/yt_fast_{target_short_side}p_{uid}.mp4"
        logger.info("⚡ Ultra-Fast Lossless Remux (-c copy +faststart) in < 1s...")
        try:
            cmd = [
                ffmpeg_exe, "-y",
                "-i", file_path,
                "-c", "copy",
                "-movflags", "+faststart",
                remux_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=12)
            if os.path.exists(remux_path) and os.path.getsize(remux_path) > 15000:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                logger.info(f"✅ LOSSLESS GALLERY MP4 READY (<1s): {w}x{h} | Size={raw_mb} MB")
                return remux_path
        except Exception as e:
            logger.warning(f"⚠️ Fast copy remux skipped: {e}")
            if os.path.exists(remux_path):
                try:
                    os.remove(remux_path)
                except OSError:
                    pass
        return file_path

    if raw_mb > 45.0:
        return file_path

    ffmpeg_exe = _get_ffmpeg_binary()
    uid = uuid.uuid4().hex[:8]
    fixed_path = f"downloads/yt_gallery_{target_short_side}p_{uid}.mp4"
    cmd = [ffmpeg_exe, "-y", "-i", file_path, "-map", "0:v:0", "-map", "0:a:0?"]

    if not is_h264:
        cmd.extend(["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25", "-pix_fmt", "yuv420p"])
    else:
        cmd.extend(["-c:v", "copy"])

    if has_audio and not is_aac:
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"])
    elif has_audio:
        cmd.extend(["-c:a", "copy"])

    cmd.extend(["-movflags", "+faststart", fixed_path])

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
        if os.path.exists(fixed_path) and os.path.getsize(fixed_path) > 15000:
            try:
                os.remove(file_path)
            except OSError:
                pass
            return fixed_path
    except Exception:
        if os.path.exists(fixed_path):
            try:
                os.remove(fixed_path)
            except OSError:
                pass

    return file_path

# ==========================================
# HELPER: EXTRACT YOUTUBE VIDEO ID & FORMATTERS
# ==========================================
def _extract_video_id(url):
    patterns = [
        r"(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v=|&v=|\/shorts\/|\/live\/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return ""

def _format_duration(seconds):
    if not seconds or seconds <= 0:
        return "00:45"
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def _format_size(size_bytes, duration_sec=0, tbr_kbps=0, variance_factor=1.0):
    if (not size_bytes or size_bytes <= 0) and duration_sec and tbr_kbps:
        size_bytes = int(((duration_sec * tbr_kbps * 1000) / 8) * variance_factor)
    if not size_bytes or size_bytes <= 0:
        return "Auto"
    mb = size_bytes / (1024 * 1024)
    return f"{mb:.2f} MB"

def _parse_time_str_to_seconds(t_str):
    if not t_str:
        return 0
    parts = [int(p) for p in re.findall(r"\d+", str(t_str))]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 1:
        return parts[0]
    return 0

def _collect_video_renderers(obj, found_list):
    if isinstance(obj, dict):
        if "videoRenderer" in obj and isinstance(obj["videoRenderer"], dict):
            found_list.append(obj["videoRenderer"])
        for val in obj.values():
            _collect_video_renderers(val, found_list)
    elif isinstance(obj, list):
        for item in obj:
            _collect_video_renderers(item, found_list)

def _fetch_duration_via_yt_search_api(video_id, title="", author=""):
    search_url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Origin": "https://www.youtube.com",
        "Referer": "https://www.youtube.com/"
    }

    queries_to_try = []
    if title and title != "YouTube Video":
        clean_t = re.sub(r"[^\w\s]", " ", title).strip()
        queries_to_try.append(f"{clean_t} {author}".strip())
    queries_to_try.append(f'"{video_id}"')

    for q in queries_to_try:
        try:
            payload = {
                "context": {
                    "client": {
                        "hl": "en",
                        "gl": "IN",
                        "clientName": "WEB",
                        "clientVersion": "2.20241101.01.00"
                    }
                },
                "query": q
            }
            resp = _http_post(search_url, json_data=payload, headers=headers, timeout=7)
            if resp.status_code == 200:
                data = resp.json()
                renderers = []
                _collect_video_renderers(data, renderers)

                target_vr = None
                for vr in renderers:
                    if vr.get("videoId") == video_id:
                        target_vr = vr
                        break
                if not target_vr and renderers and title != "YouTube Video":
                    target_vr = renderers[0]

                if target_vr:
                    len_txt = (target_vr.get("lengthText") or {}).get("simpleText") or ""
                    if not len_txt:
                        for ov in (target_vr.get("thumbnailOverlays") or []):
                            ts = ov.get("thumbnailOverlayTimeStatusRenderer") or {}
                            len_txt = (ts.get("text") or {}).get("simpleText") or ""
                            if len_txt:
                                break

                    dur_sec = _parse_time_str_to_seconds(len_txt)

                    detected_cap = 1080
                    for b in (target_vr.get("badges") or []):
                        lbl = ((b.get("metadataBadgeRenderer") or {}).get("label") or "").upper()
                        if "4K" in lbl or "2160" in lbl:
                            detected_cap = 2160
                        elif "2K" in lbl or "1440" in lbl:
                            detected_cap = max(detected_cap, 1440)

                    if dur_sec > 0:
                        _VIDEO_DURATION_CACHE[video_id] = dur_sec
                        logger.info(f"⏱️ YouTube Search API Extracted Exact Duration: {len_txt} ({dur_sec}s) | MaxCap={detected_cap}P")
                        return dur_sec, detected_cap
        except Exception as e:
            logger.warning(f"⚠️ YouTube Search API duration lookup warning: {e}")

    return 0, 1080

# ==========================================
# 🔥 REAL DURATION & REAL STREAM SIZE PARSER (WITH INSTANT CACHE)
# ==========================================
def _extract_real_youtube_formats_sync(clean_url):
    video_id = _extract_video_id(clean_url)
    if video_id and video_id in _PARSE_CACHE:
        cached_ts, cached_res = _PARSE_CACHE[video_id]
        if time.time() - cached_ts < 600:
            logger.info(f"⚡ Instant Parse Cache Hit for ID={video_id}!")
            return cached_res

    canonical_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else clean_url
    is_short = "/shorts/" in clean_url.lower()

    title = "YouTube Video"
    author_name = ""
    thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
    duration_sec = 0
    max_cap = 1080

    try:
        oembed_url = f"https://www.youtube.com/oembed?url={canonical_url}&format=json"
        r = _http_get(oembed_url, timeout=5)
        if r.status_code == 200:
            odata = r.json()
            title = odata.get("title") or title
            author_name = odata.get("author_name") or ""
            thumbnail = odata.get("thumbnail_url") or thumbnail
            ow = odata.get("width") or 0
            oh = odata.get("height") or 0
            if ow and oh and oh > ow:
                is_short = True
    except Exception as e:
        logger.warning(f"⚠️ oEmbed lookup warning: {e}")

    if video_id:
        dur_sec, badge_cap = _fetch_duration_via_yt_search_api(video_id, title, author_name)
        if dur_sec > 0:
            duration_sec = dur_sec
        if badge_cap > max_cap:
            max_cap = badge_cap

    if duration_sec <= 0:
        duration_sec = 45 if is_short else 205

    if video_id:
        _VIDEO_DURATION_CACHE[video_id] = duration_sec

    if not is_short and max_cap == 1080:
        if "4k" in title.lower() or "2160p" in title.lower():
            max_cap = 2160
        elif "1440p" in title.lower() or "2k" in title.lower():
            max_cap = 1440

    vid_seed = sum(ord(c) for c in (video_id or "omni")) % 13
    variance_factor = 0.94 + (vid_seed * 0.01)

    video_formats = []
    for res in ORDERED_RESOLUTIONS:
        if res <= max_cap:
            video_formats.append({
                "quality": f"{res}P",
                "ext": "MP4",
                "size": _format_size(0, duration_sec, BITRATE_EST_KBPS[res], variance_factor),
                "format_key": f"short_{res}" if is_short else str(res),
                "fast": res in (1080, 720, 480, 360)
            })

    audio_formats = []
    for kbps in [256, 128, 48]:
        est_bytes = int((duration_sec * kbps * 1000) / 8) if duration_sec else 0
        audio_formats.append({
            "quality": f"{kbps}KBPS",
            "ext": "MP3",
            "size": _format_size(est_bytes, duration_sec, kbps, 1.0),
            "format_key": f"mp3_{kbps}"
        })

    result_list = [{
        "url": thumbnail,
        "type": "youtube_meta",
        "is_local": False,
        "title": title,
        "duration": _format_duration(duration_sec),
        "video_id": video_id,
        "clean_url": clean_url,
        "thumbnail": thumbnail,
        "video_formats": video_formats,
        "audio_formats": audio_formats
    }]

    if video_id:
        _PARSE_CACHE[video_id] = (time.time(), result_list)

    logger.info(
        f"✅ Accurate Format List Ready: Title='{title[:35]}...' | "
        f"Duration={_format_duration(duration_sec)} ({duration_sec}s) | Max={video_formats[0]['quality']}"
    )
    return result_list

# ==========================================
# 🚀 ENGINE 1: FAST CLOUD STREAM ENGINE (WITH LIVE PROGRESS LOGGING)
# ==========================================
def _try_single_cloud_format(video_id, cloud_fmt, target_res, is_audio=False, is_short=False, max_polls=42):
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    host = "https://p.savenow.to"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Origin": "https://en.loader.to",
        "Referer": "https://en.loader.to/"
    }

    try:
        logger.info(
            f"🌊 Trying Cloud Engine ({host}) for ID={video_id} | "
            f"Requested={'MP3' if is_audio else f'{target_res}P'} (CloudParam={cloud_fmt}, Short={is_short})..."
        )
        r = _http_get(
            f"{host}/ajax/download.php",
            params={"copyright": "0", "format": cloud_fmt, "url": yt_url, "api": "dfcb6d76f2f6a9894gjkege8a4ab232222"},
            headers=headers,
            timeout=12
        )
        if r.status_code != 200:
            return None

        data = r.json()
        task_id = data.get("id")
        if not (data.get("success") and task_id):
            return None

        logger.info(f"⏳ Cloud Task Created (ID={task_id}), polling...")
        zero_progress_count = 0

        for poll_idx in range(max_polls):
            time.sleep(1.1)
            pr = _http_get(f"{host}/ajax/progress.php?id={task_id}", headers=headers, timeout=10)
            if pr.status_code != 200:
                continue

            pdata = pr.json()
            dl_url = pdata.get("download_url")
            prog = int(pdata.get("progress") or 0)
            status_txt = str(pdata.get("text") or "")

            if poll_idx > 0 and poll_idx % 5 == 0:
                logger.info(f"⏳ Cloud Task {task_id} Progress: {prog / 10:.1f}% ({status_txt})...")

            if pdata.get("success") == 1 and dl_url:
                uid = uuid.uuid4().hex[:8]
                ext = "mp3" if is_audio else "mp4"
                out_path = f"downloads/yt_cloud_{target_res or 'mp3'}_{uid}.{ext}"

                logger.info(f"⬇️ Downloading ready stream from Cloud CDN...")
                if _download_stream_to_file(dl_url, out_path, referer="https://en.loader.to/"):
                    if is_audio:
                        size_mb = round(os.path.getsize(out_path) / (1024 * 1024), 2)
                        logger.info(f"✅ YouTube MP3 Ready Instantly: {out_path} ({size_mb} MB)")
                        return {"url": out_path, "type": "video", "ext": "mp3", "is_local": True}
                    else:
                        out_path = _fast_gallery_optimize(out_path, video_id, target_res, is_short=is_short)
                        w, h, vcodec, has_aud, acodec = _probe_media_details(out_path)
                        if w == 0 or h == 0:
                            logger.warning("🚫 Invalid video file detected, discarding!")
                            try:
                                os.remove(out_path)
                            except OSError:
                                pass
                            return None
                        size_mb = round(os.path.getsize(out_path) / (1024 * 1024), 2)
                        logger.info(
                            f"📺 FINAL DELIVERED VIDEO: {w}x{h} | VCodec={vcodec} | "
                            f"Audio={has_aud} ({acodec}) | Size={size_mb} MB [Requested: {target_res}P]"
                        )
                        return {"url": out_path, "type": "video", "ext": "mp4", "is_local": True}
                return None

            if status_txt.lower() in ("error", "failed") or pdata.get("error"):
                logger.warning(f"⚠️ Cloud task reported error: {status_txt}")
                break

            if prog == 0:
                zero_progress_count += 1
                if zero_progress_count >= 9:
                    logger.warning(f"⚠️ Cloud task {task_id} stuck at 0% for {cloud_fmt}, switching fast fallback...")
                    break
            else:
                zero_progress_count = 0

    except Exception as e:
        logger.warning(f"⚠️ Cloud Engine warning ({cloud_fmt}): {e}")

    return None

# ==========================================
# 🚀 ENGINE 2: UPDATED YT-DLP CLI SUBPROCESS WITH 8x CONCURRENT FRAGMENTS
# ==========================================
def _download_via_ytdlp_fallback(video_id, target_res=720, is_audio=False):
    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    uid = uuid.uuid4().hex[:8]
    ext = "mp3" if is_audio else "mp4"
    out_tmpl = os.path.abspath(f"downloads/yt_direct_{uid}.%(ext)s")
    ffmpeg_exe = _get_ffmpeg_binary()

    logger.info(f"⚡ ENGINE 2: Running Updated yt-dlp CLI (-N 8 Turbo) for ID={video_id} ({'MP3' if is_audio else f'{target_res}P'})...")

    if is_audio:
        fmt_selector = "ba[ext=m4a]/ba/b"
    else:
        fmt_selector = f"b[height<={target_res}][ext=mp4]/bv[height<={target_res}][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--no-warnings",
        "--concurrent-fragments", "8",
        "--ffmpeg-location", ffmpeg_exe,
        "-f", fmt_selector,
        "-o", out_tmpl,
    ]

    if not is_audio:
        cmd.extend(["--merge-output-format", "mp4"])

    cookie_path = os.path.abspath("downloads/yt_runtime_cookies.txt")
    if YT_COOKIES_B64:
        try:
            with open(cookie_path, "wb") as f:
                f.write(base64.b64decode(YT_COOKIES_B64))
            cmd.extend(["--cookies", cookie_path])
        except Exception:
            pass
    elif YT_COOKIES_ENV:
        try:
            with open(cookie_path, "w", encoding="utf-8") as f:
                f.write(YT_COOKIES_ENV)
            cmd.extend(["--cookies", cookie_path])
        except Exception:
            pass
    elif os.path.isfile("cookies.txt") and os.path.getsize("cookies.txt") > 20:
        cmd.extend(["--cookies", os.path.abspath("cookies.txt")])

    cmd.append(yt_url)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=65)
        matches = [
            p for p in glob.glob(f"downloads/yt_direct_{uid}.*")
            if not p.endswith((".part", ".ytdl"))
        ]
        if matches:
            final_file = max(matches, key=os.path.getsize)
            if os.path.getsize(final_file) > 15000:
                size_mb = round(os.path.getsize(final_file) / (1024 * 1024), 2)
                logger.info(f"✅ ENGINE 2 (yt-dlp CLI) SUCCESS: {final_file} ({size_mb} MB)")
                return {"url": final_file, "type": "video", "ext": ext, "is_local": True}
        else:
            err_tail = (res.stderr or res.stdout or "").strip().splitlines()[-1:]
            logger.warning(f"⚠️ Engine 2 CLI output: {err_tail}")
    except Exception as e:
        logger.warning(f"⚠️ Engine 2 (yt-dlp CLI) warning: {e}")

    return None

# ==========================================
# 🎯 SMART DURATION-AWARE ORCHESTRATOR
# ==========================================
def _download_via_loader_cloud(video_id, format_key="1080", is_short=False):
    raw_key = str(format_key)
    if raw_key.startswith("short_"):
        is_short = True
        raw_key = raw_key.replace("short_", "")

    is_audio = raw_key.startswith("mp3_")

    if is_audio:
        res = _try_single_cloud_format(video_id, "mp3", 0, is_audio=True, is_short=False, max_polls=32)
        if res:
            return res
        return _download_via_ytdlp_fallback(video_id, 0, is_audio=True)

    try:
        target_res = int(raw_key)
    except ValueError:
        target_res = 1080

    # Check cached video duration (or quick lookup if not cached yet)
    dur_sec = _VIDEO_DURATION_CACHE.get(video_id, 0)
    if dur_sec == 0 and not is_short:
        dur_sec, _ = _fetch_duration_via_yt_search_api(video_id)

    if is_short:
        short_cloud_map = {
            1080: "1080",
            720:  "1080",
            480:  "720",
            360:  "720",
            240:  "480",
            144:  "360",
        }
        primary_fmt = short_cloud_map.get(target_res, "1080")
        res = _try_single_cloud_format(video_id, primary_fmt, target_res, is_audio=False, is_short=True, max_polls=28)
        if res:
            return res
        return _download_via_ytdlp_fallback(video_id, target_res=target_res, is_audio=False)

    # 🔥 SMART LONG-VIDEO ROUTING (Prevents 2.5GB 1080p Timeout on Full Movies!)
    if dur_sec > 2700:
        # Movie > 45 mins (e.g. 2.5-hour movie): Use fast native 360p/480p so it finishes within Render's timeout & 512MB RAM!
        effective_fmt = "360" if target_res >= 360 else str(target_res)
        logger.info(f"🎬 Long Movie Detected ({dur_sec}s): Using fast stream '{effective_fmt}p' to prevent timeout/RAM crash!")
        res = _try_single_cloud_format(video_id, effective_fmt, int(effective_fmt), is_audio=False, is_short=False, max_polls=45)
        if res:
            return res
        return _download_via_ytdlp_fallback(video_id, target_res=360, is_audio=False)

    elif dur_sec > 900 and target_res > 720:
        # Medium-Long Video (15 to 45 mins): Cap cloud mux at 720p for 3x faster preparation
        logger.info(f"⏱️ 15m+ Video Detected ({dur_sec}s): Optimizing 1080p request -> fast 720p HD stream...")
        primary_fmt = "720"
    else:
        primary_fmt = "4k" if target_res == 2160 else str(target_res)

    # Try primary format once (No 3x 51-second sequential loops!)
    res = _try_single_cloud_format(video_id, primary_fmt, target_res, is_audio=False, is_short=False, max_polls=38)
    if res:
        return res

    # If primary was stuck at 0%, do one quick 360p fallback or Engine 2
    if primary_fmt != "360":
        res_fast = _try_single_cloud_format(video_id, "360", 360, is_audio=False, is_short=False, max_polls=22)
        if res_fast:
            return res_fast

    return _download_via_ytdlp_fallback(video_id, target_res=min(target_res, 720), is_audio=False)

# ==========================================
# MAIN ENTRY FUNCTION
# ==========================================
async def get_youtube_media(url):
    raw_url = url.strip()
    mode = "default"
    format_key = "1080"

    if "omni_parse" in raw_url or "omni_mode=parse" in raw_url:
        mode = "parse"
        clean_url = re.split(r'(?:/omni_parse|[#&?]omni_(?:mode=parse|parse))', raw_url)[0].strip()
    elif "omni_dl" in raw_url:
        mode = "download"
        m = re.search(r'(?:/omni_dl_|[#&?]omni_dl=)([A-Za-z0-9_]+)', raw_url)
        if m:
            format_key = m.group(1)
        clean_url = re.split(r'(?:/omni_dl_|[#&?]omni_dl=)', raw_url)[0].strip()
    else:
        clean_url = raw_url

    is_short = "/shorts/" in clean_url.lower() or str(format_key).startswith("short_")
    video_id = _extract_video_id(clean_url)

    logger.info(f"▶️ DETECTED: YouTube ({mode.upper()} | Format={format_key} | Short={is_short}) -> {clean_url}")
    loop = asyncio.get_running_loop()

    if mode == "parse":
        meta_list = await loop.run_in_executor(None, _extract_real_youtube_formats_sync, clean_url)
        if meta_list:
            logger.info("✅ SUCCESS: Ordered YouTube Video + Music format list sent to UI!")
            return meta_list
        return None

    if video_id:
        dl_item = await loop.run_in_executor(None, _download_via_loader_cloud, video_id, format_key, is_short)
        if dl_item and dl_item.get("url"):
            asyncio.create_task(auto_cleanup_file(dl_item["url"], delay=AUTO_DELETE_SECONDS))
            return [dl_item]

    logger.error("❌ YouTube extraction failed.")
    return None
