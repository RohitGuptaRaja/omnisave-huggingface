import aiohttp
import yt_dlp
import asyncio
import re
import html
import urllib.parse
from custom_logger import logger

def _ytdlp_fallback(url):
    logger.info("⚙️ FACEBOOK MODULE: yt-dlp extractor start...")
    ydl_opts = {'format': 'best', 'quiet': True, 'no_warnings': True}
    media_list = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
            if 'entries' in info:
                for entry in info['entries']:
                    if entry and entry.get('url'):
                        ext = str(entry.get('ext', '')).lower()
                        i_type = "video" if ext in ['mp4', 'webm', 'mov'] else "image"
                        media_list.append({"url": entry['url'], "type": i_type})
            elif info.get('url'):
                ext = str(info.get('ext', '')).lower()
                i_type = "video" if ext in ['mp4', 'webm', 'mov'] else "image"
                media_list.append({"url": info['url'], "type": i_type})
                
        if media_list:
            logger.info(f"✅ SUCCESS: Facebook Video/Reel mil gayi! ({len(media_list)} item)")
            return media_list
        return None
    except Exception as e:
        logger.error(f"❌ FACEBOOK yt-dlp Error: {e}")
        return None

def _get_resolution_score(img_url):
    """Agar URL me p526x296 jaisa resize tag nahi hai, toh wo Original Full HD (10000) hai"""
    size_match = re.search(r'[ps](\d+)x(\d+)', img_url)
    if not size_match:
        return 10000
    return int(size_match.group(1))

def _parse_and_convert_fb_url(raw_url):
    """
    1. /login/?next=... ke andar se asli link nikalega
    2. permalink.php?story_fbid=X&id=Y ko modern https://www.facebook.com/Y/posts/X me badlega
    """
    try:
        parsed = urllib.parse.urlparse(raw_url)
        qs = urllib.parse.parse_qs(parsed.query)
        
        if "/login" in parsed.path and "next" in qs:
            raw_url = urllib.parse.unquote(qs["next"][0])
            parsed = urllib.parse.urlparse(raw_url)
            qs = urllib.parse.parse_qs(parsed.query)

        if ("permalink.php" in parsed.path or "story.php" in parsed.path) and "story_fbid" in qs and "id" in qs:
            story_fbid = qs["story_fbid"][0]
            owner_id = qs["id"][0]
            posts_url = f"https://www.facebook.com/{owner_id}/posts/{story_fbid}"
            permalink_url = f"https://www.facebook.com/permalink.php?story_fbid={story_fbid}&id={owner_id}"
            return posts_url, permalink_url, story_fbid

        post_id = ""
        if "story_fbid" in qs:
            post_id = qs["story_fbid"][0]
        elif "fbid" in qs:
            post_id = qs["fbid"][0]
        else:
            pfbid_match = re.search(r'(pfbid[A-Za-z0-9]+)', parsed.path)
            if pfbid_match:
                post_id = pfbid_match.group(1)

        keep_keys = {"story_fbid", "fbid", "id", "set", "pcb", "v"}
        clean_qs = {k: v[0] for k, v in qs.items() if k in keep_keys}
        new_query = urllib.parse.urlencode(clean_qs)
        clean_url = urllib.parse.urlunparse(("https", "www.facebook.com", parsed.path, "", new_query, ""))
        return clean_url, clean_url, post_id
    except Exception:
        return raw_url, raw_url, ""

def _decode_fb_html(raw_html):
    """Saare \u0025, \u0026, \u003d aur \/ escapes ko 100% clean karega"""
    text = re.sub(r'\\+u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), raw_html)
    text = re.sub(r'\\+/', '/', text)
    text = text.replace('\\"', '"')
    return html.unescape(text)

def _get_json_blocks(text, key, open_char='[', close_char=']'):
    """HTML ke andar se exact JSON array/object blocks (jaise "attachments":[...]) nikalega"""
    blocks = []
    start_idx = 0
    pattern = re.compile(re.escape(key) + r'\s*:\s*' + re.escape(open_char))
    while True:
        match = pattern.search(text, start_idx)
        if not match:
            break
        brace_start = match.end() - 1
        depth = 0
        in_str = False
        escape = False
        limit = min(brace_start + 250000, len(text))
        for i in range(brace_start, limit):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if not in_str:
                if ch == open_char:
                    depth += 1
                elif ch == close_char:
                    depth -= 1
                    if depth == 0:
                        blocks.append((brace_start, text[brace_start:i+1]))
                        break
        start_idx = match.end()
    return blocks

def _extract_urls_from_fragment(fragment, photo_candidates, photo_order, allow_new_ids=True):
    """Sirf diye gaye fragment me se valid scontent photos nikalega"""
    scontent_matches = re.findall(r'https://scontent[^"\'\s<>\\)\},;]+', fragment)
    
    noise_markers = [
        "lookaside.fbsbx.com", "/rsrc.php/", "/t39.30808-1/", "/t1.30497-1/", "/t39.1997-6/",
        "p16x16", "s16x16", "p24x24", "s24x24", "p32x32", "s32x32", "p40x40", "s40x40",
        "p48x48", "s48x48", "p50x50", "s50x50", "p60x60", "s60x60", "p75x75", "s75x75",
        "p86x86", "s86x86", "p100x100", "s100x100", "cp0", ".png"
    ]

    for img_url in scontent_matches:
        img_url = html.unescape(img_url.rstrip(').,;\\'))
        if "scontent" not in img_url or "oe=" not in img_url:
            continue
        if any(m in img_url for m in noise_markers):
            continue

        base_path = img_url.split('?')[0].lower()
        if not (base_path.endswith('.jpg') or base_path.endswith('.webp') or base_path.endswith('.jpeg')):
            continue

        photo_id = img_url.split('?')[0].split('/')[-1]
        if not photo_id:
            continue

        if photo_id not in photo_candidates:
            if not allow_new_ids:
                continue
            photo_candidates[photo_id] = []
            photo_order.append(photo_id)

        score = _get_resolution_score(img_url)
        photo_candidates[photo_id].append((score, img_url))

def _extract_target_post_photos(raw_html, target_post_id, photo_candidates, photo_order):
    """
    🔥 SURGICAL LOCK: Poore profile ki timeline photos uthane ke bajaye 
    SIRF target post ke "attachments" block se photos nikalega!
    """
    clean_text = _decode_fb_html(raw_html)

    og_matches = re.findall(r'"og:image"\s+content="([^"]+)"', clean_text, re.IGNORECASE)
    og_matches += re.findall(r'content="([^"]+)"\s+property="og:image"', clean_text, re.IGNORECASE)
    
    og_pids = []
    valid_og_urls = []
    for og_u in og_matches:
        if "scontent" in og_u and "/t39.30808-1/" not in og_u and "/t1.30497-1/" not in og_u:
            pid = og_u.split('?')[0].split('/')[-1]
            if pid:
                og_pids.append(pid)
                valid_og_urls.append(og_u)

    attachment_blocks = _get_json_blocks(clean_text, '"attachments"', '[', ']')
    valid_blocks = []
    for pos, blk in attachment_blocks:
        temp_c, temp_o = {}, []
        _extract_urls_from_fragment(blk, temp_c, temp_o, allow_new_ids=True)
        if temp_o:
            valid_blocks.append((pos, blk))

    chosen_block = None
    if valid_blocks:
        if og_pids:
            for pos, blk in valid_blocks:
                if any(pid in blk for pid in og_pids):
                    chosen_block = blk
                    break

        if not chosen_block and target_post_id:
            id_positions = [m.start() for m in re.finditer(re.escape(target_post_id), clean_text) if m.start() > 15000]
            if id_positions:
                best_dist = float('inf')
                for pos, blk in valid_blocks:
                    for id_pos in id_positions:
                        dist = abs(pos - id_pos)
                        if dist < best_dist:
                            best_dist = dist
                            chosen_block = blk

        if not chosen_block:
            chosen_block = valid_blocks[0][1]

    if chosen_block:
        sub_blocks = _get_json_blocks(chosen_block, '"all_subattachments"', '{', '}')
        if not sub_blocks:
            sub_blocks = _get_json_blocks(chosen_block, '"subattachments"', '{', '}')

        if sub_blocks:
            for _, s_blk in sub_blocks:
                _extract_urls_from_fragment(s_blk, photo_candidates, photo_order, allow_new_ids=True)
            logger.info(f"🎯 Target Post Locked (Multi-Photo Album): Found {len(photo_order)} photos")
        else:
            temp_c, temp_o = {}, []
            _extract_urls_from_fragment(chosen_block, temp_c, temp_o, allow_new_ids=True)
            if temp_o:
                first_pid = temp_o[0]
                if first_pid not in photo_candidates:
                    photo_candidates[first_pid] = []
                    photo_order.append(first_pid)
                photo_candidates[first_pid].extend(temp_c[first_pid])
            logger.info(f"🎯 Target Post Locked (Single-Photo Post): Found {len(photo_order)} photo")

        _extract_urls_from_fragment(clean_text, photo_candidates, photo_order, allow_new_ids=False)
        return True

    curr_media_blocks = _get_json_blocks(clean_text, '"currMedia"', '{', '}')
    if curr_media_blocks:
        _extract_urls_from_fragment(curr_media_blocks[0][1], photo_candidates, photo_order, allow_new_ids=True)
        _extract_urls_from_fragment(clean_text, photo_candidates, photo_order, allow_new_ids=False)
        return True

    if valid_og_urls:
        _extract_urls_from_fragment(" ".join(valid_og_urls), photo_candidates, photo_order, allow_new_ids=True)
        _extract_urls_from_fragment(clean_text, photo_candidates, photo_order, allow_new_ids=False)
        return True

    return False

def _extract_embed_photos(raw_html, photo_candidates, photo_order):
    """Facebook Embed Plugin (plugins/post.php) se sirf post ki photos (data-ploi) nikalega"""
    clean_text = _decode_fb_html(raw_html)
    clean_text = re.sub(r'<a[^>]+class="[^"]*_38vo[^"]*"[^>]*>.*?</a>', '', clean_text, flags=re.DOTALL)
    clean_text = re.sub(r'<img[^>]+class="[^"]*_1glk[^"]*"[^>]*>', '', clean_text)

    ploi_matches = re.findall(r'data-ploi="([^"]+)"', clean_text)
    if ploi_matches:
        _extract_urls_from_fragment(" ".join(ploi_matches), photo_candidates, photo_order, allow_new_ids=True)
        _extract_urls_from_fragment(clean_text, photo_candidates, photo_order, allow_new_ids=False)
    else:
        _extract_urls_from_fragment(clean_text, photo_candidates, photo_order, allow_new_ids=True)

async def _verify_single_photo(session, candidate_list):
    """Har photo ID ke sabse HD link ko verify karega ki wo 100% working hai"""
    sorted_candidates = sorted(candidate_list, key=lambda x: x[0], reverse=True)
    seen = set()
    
    for _, url in sorted_candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            async with session.get(url, timeout=6) as resp:
                if resp.status == 200 and "image" in resp.headers.get("Content-Type", "").lower():
                    content_len = int(resp.headers.get("Content-Length", "99999"))
                    if content_len > 1500:
                        return url
        except Exception:
            continue
    return None

# 🔥 UNIVERSAL SURGICAL MULTI-PHOTO SCRAPER
async def _scrape_fb_photo(url):
    logger.info("📸 FACEBOOK MODULE: Surgical Post Photo Scraper start...")
    photo_candidates = {}
    photo_order = []
    posts_url = url
    permalink_url = url
    target_post_id = ""
    locked_post = False

    webkit_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9'
    }

    try:
        async with aiohttp.ClientSession(headers=webkit_headers) as session:
            # STAGE 1: Direct Request with WebKit UA
            try:
                async with session.get(url, allow_redirects=True, timeout=10) as resp:
                    step1_html = await resp.text()
                    posts_url, permalink_url, target_post_id = _parse_and_convert_fb_url(str(resp.url))
                    
                    og_url_match = re.search(r'property="og:url"\s+content="([^"]+)"', step1_html, re.IGNORECASE)
                    if og_url_match:
                        posts_url, permalink_url, target_post_id = _parse_and_convert_fb_url(html.unescape(og_url_match.group(1)))

                    locked_post = _extract_target_post_photos(step1_html, target_post_id, photo_candidates, photo_order)
                    logger.info(f"🔗 [Stage 1] URL: {posts_url[:70]} | Photos: {len(photo_order)}")
            except Exception as e:
                logger.error(f"⚠️ Stage 1 warning: {e}")

            # STAGE 2: Agar Stage 1 login/permalink pe gaya tha, toh converted /{id}/posts/{pfbid} hit karo
            if not locked_post and posts_url != url:
                try:
                    async with session.get(posts_url, allow_redirects=True, timeout=10) as resp:
                        step2_html = await resp.text()
                        locked_post = _extract_target_post_photos(step2_html, target_post_id, photo_candidates, photo_order)
                        logger.info(f"📊 [Stage 2 - /posts/] Photos: {len(photo_order)}")
                except Exception as e:
                    logger.error(f"⚠️ Stage 2 warning: {e}")

            # STAGE 3: Facebook Embed Plugin Fallback
            if not locked_post and len(photo_order) <= 1:
                embed_targets = [posts_url]
                if permalink_url != posts_url:
                    embed_targets.append(permalink_url)
                    
                for target in embed_targets:
                    try:
                        embed_url = f"https://www.facebook.com/plugins/post.php?href={urllib.parse.quote(target, safe='')}&show_text=true&width=750"
                        async with session.get(embed_url, allow_redirects=True, timeout=10) as embed_resp:
                            embed_html = await embed_resp.text()
                            _extract_embed_photos(embed_html, photo_candidates, photo_order)
                        if len(photo_order) > 0:
                            break
                    except Exception as e:
                        logger.error(f"⚠️ Stage 3 warning: {e}")

            # STAGE 4: Live Verification
            if photo_order:
                tasks = [
                    _verify_single_photo(session, photo_candidates[pid])
                    for pid in photo_order[:10]
                ]
                verified_results = await asyncio.gather(*tasks)
                media_list = [u for u in verified_results if u is not None]

                if media_list:
                    logger.info(f"✅ SUCCESS: Post se EXACT {len(media_list)} working HD photos mil gayin!")
                    return [{"url": u, "type": "image", "is_local": False} for u in media_list[:10]]

        logger.error("❌ FACEBOOK Error: Koi valid working photo nahi mili.")
    except Exception as e:
        logger.error(f"❌ FACEBOOK Photo Scrape Exception: {e}")

    return None

async def get_facebook_media(url):
    logger.info(f"📱 DETECTED: Facebook -> {url}")
    loop = asyncio.get_running_loop()

    # 1. Agar Reel ya Video link hai (/share/r/, /reel/, /watch, /videos/), toh seedha yt-dlp chalao
    is_video_link = any(k in url for k in ["/share/r/", "/share/v/", "/reel/", "/videos/", "/watch"])
    if is_video_link:
        yt_result = await loop.run_in_executor(None, _ytdlp_fallback, url)
        if yt_result:
            return yt_result
        return await _scrape_fb_photo(url)

    # 2. Agar Post ya Photo link hai (/share/p/, /posts/, photo, permalink), toh seedha Surgical Photo Scraper chalao
    photo_result = await _scrape_fb_photo(url)
    if photo_result:
        return photo_result

    # 3. Agar Post ke andar Video nikli, toh fallback me yt-dlp chalao
    logger.info("⚠️ Post me photo nahi mili, checking for Video via yt-dlp...")
    return await loop.run_in_executor(None, _ytdlp_fallback, url)
