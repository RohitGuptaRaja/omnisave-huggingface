import aiohttp
import yt_dlp
import asyncio
import re
from custom_logger import logger

async def fetch_vxtwitter(url):
    """🔥 TWITTER SECRET API: Images aur Videos dono ke liye 🔥"""
    logger.info("⚙️ TWITTER MODULE: VxTwitter Secret API start...")
    
    api_url = re.sub(r'(https?://)(www\.)?(twitter\.com|x\.com)', r'\1api.vxtwitter.com', url)
    api_url = api_url.split('?')[0]  
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    media_list = []
                    
                    if "media_extended" in data and len(data["media_extended"]) > 0:
                        for media in data["media_extended"]:
                            m_type = "video" if media.get("type") in ["video", "gif"] else "image"
                            media_list.append({"url": media.get("url"), "type": m_type})
                            
                        logger.info(f"✅ SUCCESS: VxTwitter API ne {len(media_list)} media nikal li!")
                        return media_list
                    else:
                        logger.warning("⚠️ TWITTER: Post sahi hai, par usme koi Photo/Video nahi hai!")
                        return "NO_MEDIA"
                else:
                    # Agar API dead link ya text post par 404 return kare
                    logger.warning(f"⚠️ TWITTER: VxTwitter API return status {response.status}")
                        
    except Exception as e:
        logger.error(f"❌ VxTwitter Error: {e}")
    return None

def _ytdlp_fallback(url):
    logger.info("⚙️ TWITTER MODULE: yt-dlp Native Strict MP4 Mode start...")
    ydl_opts = {
        'format': 'best[ext=mp4][protocol=https]/best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True
    }
    media_list = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info: return None
            
            entries = info.get('entries', [info])
            for entry in entries:
                if not entry: continue
                best_url = entry.get('url')
                if best_url:
                    ext = str(entry.get('ext', '')).lower()
                    m_type = "video" if ext in ['mp4', 'webm', 'mov'] else "image"
                    logger.info(f"✅ TWITTER SUCCESS: Perfect Playable URL found via yt-dlp!")
                    media_list.append({"url": best_url, "type": m_type})
        return media_list if media_list else None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ TWITTER yt-dlp Error: {error_msg}")
        
        # 🔥 YAHAN PAKDENGE TEXT-ONLY TWEET KO 🔥
        if "No video could be found in this tweet" in error_msg:
            logger.warning("⚠️ TWITTER: yt-dlp ko video nahi mila. Yeh Text-only post hai!")
            return "NO_MEDIA"
            
        return None

async def get_twitter_media(url):
    logger.info(f"📱 DETECTED: Twitter (X) -> {url}")
    
    vx_data = await fetch_vxtwitter(url)
    if vx_data == "NO_MEDIA":
        return "NO_MEDIA" 
    elif vx_data: 
        return vx_data
        
    headers = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    async with aiohttp.ClientSession(headers=headers) as session:
        instances = [
            "https://api.cobalt.tools/api/json", "https://cobalt.seasi.dev/api/json",
            "https://co.wuk.sh/api/json", "https://api.cobalt.lol/api/json"
        ]
        payload = {"url": url, "vQuality": "720", "isAudioOnly": False}
        for api_url in instances:
            try:
                post_headers = headers.copy()
                post_headers['Content-Type'] = 'application/json'
                logger.info(f"🔄 TWITTER Cobalt try: {api_url}")
                async with session.post(api_url, json=payload, headers=post_headers, timeout=3) as response:
                    if response.status == 200:
                        data = await response.json()
                        local_media = []
                        if data.get("status") == "picker":
                            for item in data.get("picker", []):
                                t = "image" if item.get("type") == "photo" else "video"
                                local_media.append({"url": item["url"], "type": t})
                            if local_media: 
                                logger.info(f"✅ SUCCESS: TWITTER Cobalt worked!")
                                return local_media
                        elif data.get("status") in ["stream", "redirect", "success"] or data.get("url"):
                            m_url = data.get("url")
                            if m_url: 
                                logger.info(f"✅ SUCCESS: TWITTER Cobalt worked!")
                                return [{"url": m_url, "type": "video"}]
            except Exception:
                pass 

    logger.info("⚠️ TWITTER Cobalt fails. Running Native Browser-Safe yt-dlp...")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ytdlp_fallback, url)
