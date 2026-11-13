import os
import re
import uuid
import base64
import asyncio
import aiohttp
from yarl import URL
from bs4 import BeautifulSoup
import yt_dlp
from custom_logger import logger

os.makedirs("downloads", exist_ok=True)

# ==========================================
# HELPER: SNAPTIK JS DECODER (Unpacker)
# ==========================================
def _decode_snaptik_js(h, u, n, t, e, r):
    def _base_convert(d, e_base, f_base):
        chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
        g = chars[:e_base]
        h_chars = chars[:f_base]
        acc = 0
        for idx, c in enumerate(reversed(d)):
            if c in g:
                acc += g.index(c) * (e_base ** idx)
        k = ""
        while acc > 0:
            k = h_chars[acc % f_base] + k
            acc //= f_base
        return k or "0"

    r_out = ""
    i = 0
    while i < len(h):
        s = ""
        while i < len(h) and h[i] != n[e]:
            s += h[i]
            i += 1
        for j in range(len(n)):
            s = s.replace(n[j], str(j))
        if s:
            r_out += chr(int(_base_convert(s, e, 10)) - t)
        i += 1
    return r_out

# ==========================================
# HELPER: SAVE HD VIDEO TO DOWNLOADS FOLDER (Fallback)
# ==========================================
async def _save_stream_to_mp4(session, video_url, headers=None):
    req_headers = headers or {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    file_path = f"downloads/tiktok_{uuid.uuid4().hex[:8]}.mp4"
    try:
        async with session.get(video_url, headers=req_headers, allow_redirects=True, timeout=25) as resp:
            if resp.status == 200:
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(16384):
                        f.write(chunk)
                if os.path.exists(file_path) and os.path.getsize(file_path) > 50000:
                    size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
                    logger.info(f"✅ HD Video Saved: {file_path} ({size_mb} MB)")
                    return file_path
                elif os.path.exists(file_path):
                    os.remove(file_path)
    except Exception as e:
        logger.error(f"⚠️ Stream save error: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
    return None

# ==========================================
# ENGINE 1: SSSTIK 2-STEP TRUE HD API (Hx-Redirect -> Direct 0 MB Download)
# ==========================================
async def get_from_ssstik_hd(url):
    logger.info("🎥 ENGINE 1: SSSTik 2-Step True HD API try kar rahe hain...")
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8"
    }

    try:
        async with aiohttp.ClientSession() as session:
            tt_token = "S3E1UE4_"
            async with session.get("https://ssstik.io/", headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    tt_match = re.search(r"s_tt\s*=\s*['\"]([^'\"]+)['\"]", html_text)
                    if tt_match:
                        tt_token = tt_match.group(1)

            post_headers = {
                "User-Agent": ua,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://ssstik.io",
                "Referer": "https://ssstik.io/",
                "HX-Request": "true",
                "HX-Target": "target",
                "HX-Trigger": "_gcaptcha_pt",
                "HX-Current-URL": "https://ssstik.io/"
            }
            payload = {
                "id": url,
                "locale": "en",
                "tt": tt_token,
                "debug": "ab=0&loc=IN&ip=223.237.128.146"
            }

            async with session.post("https://ssstik.io/abc?url=dl", data=payload, headers=post_headers, timeout=12) as resp1:
                if resp1.status != 200:
                    return None
                res_html = await resp1.text()

            clean_res = res_html.replace('\\"', '"').replace("\\'", "'")
            hd_path_match = re.search(r'(/abc\?url=([A-Za-z0-9+/=_=-]+==[A-Za-z0-9+/=_=-]+))', clean_res)
            if hd_path_match:
                hd_path = hd_path_match.group(1)
                hd_url_param = hd_path_match.group(2)
                hd_tt = hd_url_param.split("==")[0]

                hd_headers = {
                    "User-Agent": ua,
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "https://ssstik.io",
                    "Referer": "https://ssstik.io/",
                    "HX-Request": "true",
                    "HX-Target": "hd_download",
                    "HX-Trigger": "hd_download",
                    "HX-Current-URL": "https://ssstik.io/#goog_fullscreen_ad"
                }

                target_hd_url = URL(f"https://ssstik.io{hd_path}", encoded=True)
                async with session.post(target_hd_url, data={"tt": hd_tt}, headers=hd_headers, timeout=12) as resp2:
                    hx_redirect = resp2.headers.get("Hx-Redirect") or resp2.headers.get("hx-redirect")
                    if hx_redirect and hx_redirect.startswith("http"):
                        # 🔥 Return exact hx_redirect (tikcdn.io) — it works in <video> preview AND has built-in Content-Disposition: attachment!
                        logger.info(f"⚡ [0 MB RENDER DATA] SSSTik True HD Link Ready -> {hx_redirect[:60]}...")
                        return [{"url": hx_redirect, "type": "video", "is_local": False}]

    except Exception as e:
        logger.error(f"⚠️ SSSTik HD Engine Error: {e}")
    return None

# ==========================================
# ENGINE 2: SNAPTIK TRUE HD API (/api/hd?token=...)
# ==========================================
async def get_from_snaptik_hd(url):
    logger.info("🎥 ENGINE 2: SnapTik True HD API (/api/hd) try kar rahe hain...")
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://snaptik.app/en3"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://snaptik.app/en3", headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return None
                home_html = await resp.text()

            token_match = re.search(r'name=["\']token["\']\s+value=["\']([^"\']+)["\']', home_html)
            if not token_match:
                return None
            form_token = token_match.group(1)

            data = aiohttp.FormData()
            data.add_field("url", url)
            data.add_field("lang", "en3")
            data.add_field("token", form_token)

            post_headers = {
                "User-Agent": ua,
                "Origin": "https://snaptik.app",
                "Referer": "https://snaptik.app/en3",
                "X-Requested-With": "XMLHttpRequest"
            }

            async with session.post("https://snaptik.app/abc2.php", data=data, headers=post_headers, timeout=12) as post_resp:
                if post_resp.status != 200:
                    return None
                js_text = await post_resp.text()

            decoded_html = js_text
            pack_match = re.search(r'\("([^"]+)",\d+,"([^"]+)",(\d+),(\d+),\d+\)', js_text)
            if pack_match:
                h_arg, n_arg, t_arg, e_arg = pack_match.groups()
                decoded_html = _decode_snaptik_js(h_arg, 0, n_arg, int(t_arg), int(e_arg), 0)

            unescaped_html = decoded_html.replace('\\"', '"').replace("\\'", "'").replace("\\/", "/")

            hd_token_match = re.search(r'data-token="([^"]+)"', unescaped_html) or re.search(r'/api/hd\?token=([^"&\'\\]+)', unescaped_html)
            if hd_token_match:
                hd_token = hd_token_match.group(1)
                hd_api_url = URL(f"https://snaptik.app/api/hd?token={hd_token}", encoded=True)
                hd_headers = {
                    "User-Agent": ua,
                    "Accept": "*/*",
                    "Referer": "https://snaptik.app/en3",
                    "X-Requested-With": "XMLHttpRequest"
                }
                async with session.get(hd_api_url, headers=hd_headers, timeout=10) as hd_resp:
                    if hd_resp.status == 200:
                        hd_json = await hd_resp.json(content_type=None)
                        if hd_json and not hd_json.get("error") and hd_json.get("url"):
                            rapid_url = hd_json["url"].strip()
                            if rapid_url.startswith("http"):
                                logger.info(f"⚡ [0 MB RENDER DATA] SnapTik True HD Link Ready -> {rapid_url[:60]}...")
                                return [{"url": rapid_url, "type": "video", "is_local": False}]

    except Exception as e:
        logger.error(f"⚠️ SnapTik HD Engine Error: {e}")
    return None

# ==========================================
# ENGINE 3: YT-DLP 1080P HEVC/HD SELECTOR (Local Fallback)
# ==========================================
def _ytdlp_local_hd(url):
    logger.info("⚙️ ENGINE 3: yt-dlp True 1080p HD Selector start...")
    file_path = f"downloads/tiktok_{uuid.uuid4().hex[:8]}.mp4"
    base_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.tiktok.com/"
    }

    try:
        extract_opts = {"quiet": True, "no_warnings": True, "http_headers": base_headers}
        best_format_id = "best"
        with yt_dlp.YoutubeDL(extract_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get("formats"):
                clean_formats = []
                for f in info["formats"]:
                    f_id = str(f.get("format_id", "")).lower()
                    f_note = str(f.get("format_note", "")).lower()
                    vcodec = str(f.get("vcodec", "none")).lower()
                    if "watermark" in f_note or f_id.startswith("download"):
                        continue
                    if vcodec != "none":
                        size = f.get("filesize") or f.get("filesize_approx") or 0
                        height = f.get("height") or 0
                        tbr = f.get("tbr") or 0
                        clean_formats.append((size, height, tbr, f.get("format_id")))

                if clean_formats:
                    clean_formats.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
                    best_format_id = clean_formats[0][3]
                    logger.info(f"🎯 Selected Highest HD Format: {best_format_id} (Size: {round(clean_formats[0][0]/(1024*1024), 2)} MB, Height: {clean_formats[0][1]}p)")

        dl_opts = {
            "format": best_format_id,
            "outtmpl": file_path,
            "quiet": True,
            "no_warnings": True,
            "http_headers": base_headers
        }
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([url])

        if os.path.exists(file_path) and os.path.getsize(file_path) > 50000:
            size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
            logger.info(f"✅ yt-dlp True HD Video Saved: {file_path} ({size_mb} MB)")
            return [{"url": file_path, "type": "video", "is_local": True}]
    except Exception as e:
        logger.error(f"❌ yt-dlp HD Error: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
    return None

# ==========================================
# MAIN FUNCTION
# ==========================================
async def get_tiktok_media(url):
    clean_url = url.split("?")[0] if ("?" in url and "tiktok.com/@" in url) else url.strip()
    logger.info(f"📱 DETECTED: TikTok -> {clean_url}")

    # 1. Try SSSTik 2-Step True HD API (0 MB Render Bandwidth)
    media = await get_from_ssstik_hd(clean_url)
    if media:
        return media

    # 2. Try SnapTik True HD API (0 MB Render Bandwidth)
    media = await get_from_snaptik_hd(clean_url)
    if media:
        return media

    # 3. Try yt-dlp Highest Resolution/Filesize Selector (Fallback)
    loop = asyncio.get_running_loop()
    media = await loop.run_in_executor(None, _ytdlp_local_hd, clean_url)
    if media:
        return media

    logger.error("❌ TikTok extraction fail ho gayi.")
    return None
