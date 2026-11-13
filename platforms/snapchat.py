import re
import json
import requests
import yt_dlp
import asyncio
from custom_logger import logger

def clean_snapchat_url(url):
    """Tracking parameters hatana"""
    if "?" in url:
        return url.split("?")[0]
    return url

def _direct_snapchat_scraper(url):
    """Direct Snapchat __NEXT_DATA__ & JSON-LD Scraper (Bypasses yt-dlp hyphen bug & junk images)"""
    logger.info("⚙️ SNAPCHAT MODULE: Direct HTML/JSON Scraper start...")
    
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    ]
    
    html_text = ""
    final_url = url
    
    for ua in user_agents:
        try:
            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            final_url = res.url
            logger.info(f"🌐 Snapchat HTTP Status: {res.status_code} | URL: {final_url}")
            
            if res.status_code == 200 and res.text:
                html_text = res.text
                break
            elif res.text and ('"contentUrl"' in res.text or '"mediaUrl"' in res.text):
                html_text = res.text
                break
        except Exception as e:
            logger.error(f"⚠️ Direct request error: {e}")

    if not html_text:
        logger.error("❌ Snapchat returned 404 (Ye video Snapchat se delete/unavailable ho chuka hai).")
        return None

    # 1. JSON-LD "contentUrl" Check (Spotlight ke liye 100% exact single video)
    content_urls = re.findall(r'"contentUrl"\s*:\s*"(https://[^"]+)"', html_text)
    for c_url in content_urls:
        c_url = c_url.replace(r'\u002F', '/').replace('\\/', '/')
        if 'sc-cdn.net' in c_url or '.mp4' in c_url:
            logger.info("✅ Video found via Snapchat contentUrl!")
            return [{"url": c_url, "type": "video"}]

    # 2. __NEXT_DATA__ JSON Parse (Spotlight + Public Stories + /t/ links ke liye)
    next_data_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.DOTALL)
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1))
            page_props = data.get('props', {}).get('pageProps', {})
            
            # Check A: Direct videoMetadata
            vid_meta = page_props.get('videoMetadata', {})
            if vid_meta and vid_meta.get('contentUrl'):
                logger.info("✅ Video found via __NEXT_DATA__ videoMetadata!")
                return [{"url": vid_meta['contentUrl'], "type": "video"}]

            # Check B: spotlightFeed
            spotlight_feed = page_props.get('spotlightFeed', {}).get('spotlightStories', [])
            if spotlight_feed:
                for story in spotlight_feed:
                    for snap in story.get('snapList', []):
                        m_url = snap.get('snapUrls', {}).get('mediaUrl')
                        if m_url:
                            logger.info("✅ Video found via __NEXT_DATA__ spotlightFeed!")
                            return [{"url": m_url, "type": "video"}]

            # Check C: Public Story /t/ links
            story_snaps = page_props.get('story', {}).get('snapList', [])
            if story_snaps:
                media_list = []
                for snap in story_snaps:
                    m_url = snap.get('snapUrls', {}).get('mediaUrl')
                    m_type = snap.get('snapMediaType', 1) # 1 = video, 0 = image
                    if m_url:
                        media_list.append({
                            "url": m_url,
                            "type": "video" if m_type == 1 else "image"
                        })
                if media_list:
                    # Agar video hai toh sirf video bhejo (faltu broken image hatao)
                    videos_only = [m for m in media_list if m['type'] == 'video']
                    logger.info("✅ Media found via __NEXT_DATA__ story!")
                    return videos_only if videos_only else media_list
        except Exception as e:
            logger.error(f"⚠️ __NEXT_DATA__ parse error: {e}")

    # 3. Direct "mediaUrl" Regex Check
    media_urls = re.findall(r'"mediaUrl"\s*:\s*"(https://[^"]+)"', html_text)
    for m_url in media_urls:
        m_url = m_url.replace(r'\u002F', '/').replace('\\/', '/')
        if 'sc-cdn.net' in m_url:
            logger.info("✅ Video found via Snapchat mediaUrl!")
            return [{"url": m_url, "type": "video"}]

    # 4. OpenGraph og:video Check
    og_video = re.search(r'<meta[^>]+property="og:video(?::secure_url)?"[^>]+content="(https://[^"]+)"', html_text)
    if og_video:
        logger.info("✅ Video found via og:video meta tag!")
        return [{"url": og_video.group(1), "type": "video"}]

    return None

def _ytdlp_fallback(url):
    logger.info("⚙️ SNAPCHAT MODULE: yt-dlp Fallback start...")
    ydl_opts = {'format': 'best', 'quiet': True, 'no_warnings': True}
    videos = []
    images = []
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            
            entries = info.get('entries') if 'entries' in info else [info]
            for entry in entries:
                if not entry or not entry.get('url'):
                    continue
                ext = str(entry.get('ext', '')).lower()
                vcodec = str(entry.get('vcodec', 'none')).lower()
                
                # Sirf asli video ko video maano, har unknown cheez ko image mat banao!
                if ext in ['mp4', 'webm', 'mov'] or (vcodec != 'none' and vcodec != ''):
                    videos.append({"url": entry['url'], "type": "video"})
                elif ext in ['jpg', 'jpeg', 'png', 'webp']:
                    images.append({"url": entry['url'], "type": "image"})
                    
        # Agar ek bhi video mila hai, toh sirf video return karo (broken photo gayab!)
        if videos:
            return videos
        return images if images else None
    except Exception as e:
        logger.error(f"❌ SNAPCHAT yt-dlp Error: {e}")
        return None

def _process_snapchat_sync(url):
    # Step 1: Direct Scraper (Fastest, fixes '-' hyphen bug & /t/ junk photo)
    media = _direct_snapchat_scraper(url)
    if media:
        return media
        
    # Step 2: yt-dlp fallback
    return _ytdlp_fallback(url)

async def get_snapchat_media(url):
    clean_url = clean_snapchat_url(url)
    logger.info(f"📱 DETECTED: Snapchat -> {clean_url}")
    
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _process_snapchat_sync, clean_url)
