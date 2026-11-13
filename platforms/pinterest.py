import re
import html
import aiohttp
import asyncio
import yt_dlp
from custom_logger import logger

def _decode_pinterest_text(raw_text):
    """Saare \u002f, \u002F, \u0026 aur \/ escapes ko clean karega"""
    text = re.sub(r'\\+u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), raw_text)
    text = re.sub(r'\\+/', '/', text)
    return html.unescape(text)

def _pick_best_mp4(mp4_list):
    """Milne wale saare MP4 links me se sabse HD (720p/1080p/expmp4) select karega"""
    if not mp4_list:
        return None
    for priority in ["1080p", "720p", "expmp4", "expMp4", "480p"]:
        for url in mp4_list:
            if priority in url:
                return url
    return mp4_list[0]

def _ytdlp_pinterest_video(clean_url):
    """Clean Pin URL se yt-dlp ke zariye direct HTTP MP4 link nikalega"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            if not info:
                return None
            if 'entries' in info and info['entries']:
                info = info['entries'][0]

            # Formats me se direct HTTP .mp4 dhoondho (m3u8 stream nahi)
            formats = info.get('formats') or []
            mp4_candidates = []
            for f in formats:
                f_url = f.get('url', '')
                protocol = f.get('protocol', '')
                if '.mp4' in f_url and 'm3u8' not in f_url and protocol in ('http', 'https', ''):
                    height = f.get('height') or 0
                    mp4_candidates.append((height, f_url))

            if mp4_candidates:
                mp4_candidates.sort(key=lambda x: x[0], reverse=True)
                best_url = mp4_candidates[0][1]
                logger.info(f"✅ SUCCESS: Pinterest Video (via yt-dlp) mil gaya! -> {best_url[:50]}...")
                return [{"url": best_url, "type": "video", "is_local": False}]

            # Fallback to direct url if it's an mp4
            direct_url = info.get('url', '')
            if direct_url and '.mp4' in direct_url:
                logger.info(f"✅ SUCCESS: Pinterest Video (via yt-dlp) mil gaya!")
                return [{"url": direct_url, "type": "video", "is_local": False}]
    except Exception as e:
        logger.error(f"⚠️ Pinterest yt-dlp warning: {e}")
    return None

async def _convert_m3u8_to_mp4(session, m3u8_urls):
    """Pinterest ke .m3u8 HLS link ko direct CDN .mp4 link me convert karke check karega"""
    candidates = []
    for m_url in m3u8_urls:
        # Pattern: https://v1.pinimg.com/videos/iht/hls/ab/cd/ef/file.m3u8
        m = re.search(r'https?://([^/]+)/videos/(?:iht|mc)/hls/([a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]{2}/[^.\s"\'<>\\]+)\.m3u8', m_url)
        if m:
            host, path_id = m.group(1), m.group(2)
            candidates.extend([
                f"https://{host}/videos/mc/720p/{path_id}.mp4",
                f"https://{host}/videos/iht/expmp4/{path_id}.mp4",
                f"https://{host}/videos/iht/expmp4/{path_id}_720w.mp4",
                f"https://{host}/videos/mc/expMp4/{path_id}_t1.mp4",
                f"https://{host}/videos/mc/480p/{path_id}.mp4"
            ])

    seen = set()
    for c_url in candidates:
        if c_url in seen:
            continue
        seen.add(c_url)
        try:
            async with session.get(c_url, timeout=5) as resp:
                if resp.status == 200 and "video" in resp.headers.get("Content-Type", "").lower():
                    return c_url
        except Exception:
            continue
    return None

async def get_pinterest_media(url):
    logger.info(f"📱 DETECTED: Pinterest -> {url}")
    logger.info("⚙️ PINTEREST MODULE: Native Scraper start hua...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9'
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # STEP 1: Resolve Shortlink (pin.it) & Extract Clean Pin ID
            async with session.get(url, allow_redirects=True, timeout=15) as resp:
                final_url = str(resp.url)
                raw_html = await resp.text()

            js_match = re.search(r'window\.location\.replace\([\'"]([^\'"]+)[\'"]\)', raw_html)
            meta_match = re.search(r'http-equiv="refresh"\s+content="\d+;\s*url=([^"]+)"', raw_html, re.IGNORECASE)
            redirect_url = js_match.group(1) if js_match else (meta_match.group(1) if meta_match else None)
                
            if redirect_url:
                async with session.get(redirect_url, allow_redirects=True, timeout=15) as resp:
                    final_url = str(resp.url)
                    raw_html = await resp.text()

            clean_html = _decode_pinterest_text(raw_html)

            # Pin ID nikalna (taaki /sent/?invite_code=... kachra hat jaye)
            pin_id_match = (
                re.search(r'/pin/(?:[\w-]+--)?(\d+)', final_url) or
                re.search(r'pinterest\.com/pin/(\d+)', clean_html) or
                re.search(r'"id"\s*:\s*"(\d{15,22})"', clean_html)
            )
            pin_id = pin_id_match.group(1) if pin_id_match else None
            clean_pin_url = f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else final_url

            # Agar redirect URL me /sent/ ya invite_code tha, toh clean Pin URL fetch karo
            if pin_id and ("/sent" in final_url or "invite_code" in final_url):
                try:
                    async with session.get(clean_pin_url, allow_redirects=True, timeout=12) as resp:
                        clean_html += "\n" + _decode_pinterest_text(await resp.text())
                except Exception:
                    pass

            # STEP 2: Direct MP4 Regex Scan on HTML
            video_matches = re.findall(r'https?://[^"\'\s<>\\]*pinimg\.com/[^"\'\s<>\\]+\.mp4', clean_html)
            if video_matches:
                best_video = _pick_best_mp4(video_matches)
                logger.info(f"✅ SUCCESS: Pinterest HD Video (Direct HTML) mil gaya! -> {best_video[:50]}...")
                return [{"url": best_video, "type": "video", "is_local": False}]

            # STEP 3: Official Pinterest Pidgets V3 API (Sabse reliable MP4 source!)
            if pin_id:
                pidget_url = f"https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids={pin_id}"
                try:
                    async with session.get(pidget_url, timeout=10) as p_resp:
                        if p_resp.status == 200:
                            pidget_text = _decode_pinterest_text(await p_resp.text())
                            pidget_mp4s = re.findall(r'https?://[^"\'\s<>\\]*pinimg\.com/[^"\'\s<>\\]+\.mp4', pidget_text)
                            if pidget_mp4s:
                                best_video = _pick_best_mp4(pidget_mp4s)
                                logger.info(f"✅ SUCCESS: Pinterest HD Video (via Pidgets API) mil gaya! -> {best_video[:50]}...")
                                return [{"url": best_video, "type": "video", "is_local": False}]
                            clean_html += "\n" + pidget_text
                except Exception as e:
                    logger.error(f"⚠️ Pidgets API warning: {e}")

            # STEP 4: HLS (.m3u8) to Direct CDN (.mp4) Converter + yt-dlp Fallback
            m3u8_matches = re.findall(r'https?://[^"\'\s<>\\]*pinimg\.com/[^"\'\s<>\\]+\.m3u8', clean_html)
            has_video_indicator = bool(m3u8_matches) or any(
                k in clean_html for k in ['"video_list"', 'v1.pinimg.com/videos', 'og:video', '"is_video":true']
            )

            if has_video_indicator:
                logger.info("🎬 Video Pin detected! Converting stream / running yt-dlp...")
                if m3u8_matches:
                    converted_mp4 = await _convert_m3u8_to_mp4(session, m3u8_matches)
                    if converted_mp4:
                        logger.info(f"✅ SUCCESS: Pinterest HD Video (CDN Converted) mil gaya! -> {converted_mp4[:50]}...")
                        return [{"url": converted_mp4, "type": "video", "is_local": False}]

                loop = asyncio.get_running_loop()
                yt_res = await loop.run_in_executor(None, _ytdlp_pinterest_video, clean_pin_url)
                if yt_res:
                    return yt_res

            # STEP 5: Image Extraction (Sirf Photo Pins ke liye)
            meta_img = (
                re.search(r'property="og:image"[^>]+content="([^"]+)"', clean_html, re.IGNORECASE) or
                re.search(r'content="([^"]+)"[^>]+property="og:image"', clean_html, re.IGNORECASE) or
                re.search(r'name="og:image"[^>]+content="([^"]+)"', clean_html, re.IGNORECASE)
            )
            
            if meta_img:
                img_url = meta_img.group(1).replace("&amp;", "&")
                if "pinimg.com" in img_url:
                    img_url = re.sub(r'/(?:236x|474x|564x|736x|url)/', '/originals/', img_url)
                    logger.info("✅ SUCCESS: Pinterest HD Image mil gayi!")
                    return [{"url": img_url, "type": "image", "is_local": False}]
                    
            img_matches = re.findall(r'https?://i\.pinimg\.com/originals/[a-zA-Z0-9/_\-]+\.(?:jpg|jpeg|png|webp)', clean_html)
            if img_matches:
                logger.info("✅ SUCCESS: Pinterest HD Image mil gayi!")
                return [{"url": img_matches[0], "type": "image", "is_local": False}]
            
    except Exception as e:
        logger.error(f"❌ Pinterest Scraper Error: {e}")
        
    return None
