#!/usr/bin/env python3
# main.py — Calm Loop uploader (fixed env parsing, upload retries, no channel handle in title)
# Usage: python main.py --type shorts|long|very_long
# Requirements on runner: ffmpeg, ffprobe, python3, pip install requests

import os, sys, time, random, re, subprocess, requests, json
from pathlib import Path
import math

# ---------- Config ----------
CHANNEL_NAME = "Calm Loop"
CHANNEL_HANDLE = "CalmLoop-l6p"
TOPICS = [
    "relaxing", "rain", "ocean", "forest", "waterfall", "snow", "clouds",
    "desert night", "mountain", "river", "calm beach", "winter cozy", "campfire",
    "underwater diving", "birds", "sunset", "sunrise", "drone aerial", "night stars"
]

TITLE_TEMPLATES = {
    "shorts": ["Instant Calm — {}", "Quick Relaxation: {}", "{} Mini Escape", "{} Moment to Breathe"],
    "long": ["{} Ambience for Relaxation & Focus", "Soothing {} Sounds — Relax & Sleep", "Peaceful {} Ambience — Calm Your Mind", "Gentle {} Flow — Meditation & Sleep"],
    "very_long": ["Extended {} Mix — Overnight Relaxation", "{} Soundscape — Sleep & Deep Rest", "Long {} Ambience for Full Night Sleep"]
}

DESCRIPTION_TEMPLATE = (
    "Calm Loop brings you high-quality relaxing ambient sounds and peaceful nature visuals "
    "to help you relax, sleep, meditate, and focus."
)

TAGS_BASE = ["relaxing","nature","sleep","meditation","ambient","calm","relax","soothing","ASMR","english"]

MIXKIT_FALLBACK = "https://assets.mixkit.co/music/preview/mixkit-relaxing-piano-628.mp3"
MIXKIT_BIRDS = os.environ.get("MIXKIT_BIRDS_URL", "")

# API endpoints
PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
COVERR_SEARCH = "https://api.coverr.co/videos"
VIDEVO_SEARCH = "https://www.videvo.net/search/videos/"
LIFE_OF_VIDS_SEARCH = "https://www.lifeofvids.com/?s="
IA_ADVANCED_SEARCH = "https://archive.org/advancedsearch.php"

# ---------- Secrets (may be empty strings; handle safely) ----------
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY") or None
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY") or None
COVERR_API_KEY = os.environ.get("COVERR_API_KEY") or None
VIDEVO_API_KEY = os.environ.get("VIDEVO_API_KEY") or None
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID") or None
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET") or None
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN") or None

# ---------- Safe env->int parser ----------
def getenv_int(name, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return int(default)
    try:
        return int(v)
    except Exception:
        return int(default)

# durations
SHORT_MAX_S = getenv_int("SHORT_MAX_S", 600)            # max seconds allowed for 'shorts' job
SHORT_THRESHOLD_SECONDS = getenv_int("SHORT_THRESHOLD_SECONDS", 120)  # <= this considered a Short (vertical)
LONG_MIN_S  = getenv_int("LONG_MIN_S", 120)
LONG_MAX_S  = getenv_int("LONG_MAX_S", 1800)
VERY_LONG_MIN_S = getenv_int("VERY_LONG_MIN_S", 3600)

# audio threshold
def getenv_float(name, default):
    v = os.environ.get(name)
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)

AUDIO_MIN_DB = getenv_float("AUDIO_MIN_DB", -60.0)

MAX_DOWNLOAD_CANDIDATES = getenv_int("MAX_CANDIDATES", 30)
TRY_COUNT = getenv_int("TRY_COUNT", 30)

# ---------- Paths ----------
WORKDIR = Path("work")
CLIPS_DIR = WORKDIR / "clips"
OUT_DIR = WORKDIR / "out"
FINAL_FILE = WORKDIR / "final_video.mp4"
UPLOAD_LOG = Path("uploads_log.csv")

# ---------- Helpers ----------
def sh(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
    else:
        return subprocess.check_call(cmd, shell=True)

def ensure_dirs():
    WORKDIR.mkdir(exist_ok=True)
    CLIPS_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)

def ffprobe_duration_seconds(path):
    try:
        out = sh(f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"', capture=True)
        return float(out.strip())
    except Exception:
        return 0.0

def has_audio_stream(path):
    try:
        out = sh(f'ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 "{path}"', capture=True)
        return bool(out.strip())
    except Exception:
        return False

def audio_mean_volume_db(path):
    try:
        out = sh(f'ffmpeg -hide_banner -nostats -i "{path}" -af "volumedetect" -f null /dev/null', capture=True)
        m = re.search(r'mean_volume:\s*([-0-9\.]+)\s*dB', out)
        if m:
            return float(m.group(1))
    except Exception:
        return None
    return None

def audio_ok(path, min_db=AUDIO_MIN_DB):
    if not has_audio_stream(path):
        return False
    mv = audio_mean_volume_db(path)
    if mv is None:
        return False
    return mv > min_db

def has_long_silence(path, silence_db=-50, max_silence_seconds=1.5):
    try:
        out = sh(f'ffmpeg -hide_banner -nostats -i "{path}" -af "silencedetect=noise={silence_db}dB:d={max_silence_seconds}" -f null -', capture=True)
        if "silence_start" in out or "silence_end" in out:
            return True
    except Exception:
        return True
    return False

def download_url_to(path, url, timeout=60):
    print(f"Downloading {url} → {path}")
    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path

# ---------- Search functions ----------
def search_pexels(query, per_page=20):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": per_page}
    try:
        r = requests.get(PEXELS_SEARCH, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        results = []
        for v in data.get("videos", []):
            files = v.get("video_files", [])
            if files:
                best = sorted(files, key=lambda x: int(x.get("width",0)), reverse=True)[0]
                link = best.get("link")
                if link:
                    results.append(link)
        return results
    except Exception as e:
        print("pexels error", e); return []

def search_pixabay(query, per_page=25):
    if not PIXABAY_API_KEY:
        return []
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": per_page}
    try:
        r = requests.get(PIXABAY_SEARCH, params=params, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        results = []
        for h in data.get("hits", []):
            vids = h.get("videos", {})
            for key in ("large","medium","tiny"):
                if key in vids and vids[key].get("url"):
                    results.append(vids[key]["url"])
                    break
        return results
    except Exception as e:
        print("pixabay error", e); return []

def search_coverr():
    if not COVERR_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {COVERR_API_KEY}"}
    try:
        r = requests.get(COVERR_SEARCH, headers=headers, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        urls = []
        for d in data.get("data", []):
            assets = d.get("assets", [])
            if assets:
                urls.append(assets[0].get("url"))
        return urls
    except Exception as e:
        print("coverr error", e); return []

def search_videvo(query, per_page=20):
    try:
        search_url = f"https://www.videvo.net/search/videos/{requests.utils.quote(query)}/"
        headers = {"User-Agent":"Mozilla/5.0"}
        r = requests.get(search_url, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        html = r.text
        urls = re.findall(r'https?://[^\s"\']+\.mp4', html)
        return list(dict.fromkeys(urls))[:per_page]
    except Exception as e:
        print("videvo scrape error", e); return []

def search_lifeofvids(query):
    try:
        url = f"https://www.lifeofvids.com/?s={requests.utils.quote(query)}"
        headers = {"User-Agent":"Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        html = r.text
        urls = re.findall(r'https?://[^\s"\']+\.mp4', html)
        return list(dict.fromkeys(urls))[:20]
    except Exception as e:
        print("lifeofvids error", e); return []

def search_internet_archive(query, rows=20):
    try:
        q = requests.utils.quote(f'("video" OR mediatype:movies) AND ({query})')
        url = f"{IA_ADVANCED_SEARCH}?q={q}&fl[]=identifier&fl[]=title&rows={rows}&output=json"
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        ids = [d.get("identifier") for d in data.get("response", {}).get("docs", []) if d.get("identifier")]
        results = []
        for identifier in ids:
            meta_url = f"https://archive.org/metadata/{identifier}"
            m = requests.get(meta_url, timeout=15)
            if m.status_code != 200:
                continue
            meta = m.json()
            files = meta.get("files", [])
            for f in files:
                name = f.get("name","")
                fmt = f.get("format","")
                if name.endswith(".mp4") or "MPEG-4" in fmt or "MP4" in fmt:
                    results.append(f"https://archive.org/download/{identifier}/{name}")
            if len(results) >= rows:
                break
        return results[:rows]
    except Exception as e:
        print("internet archive error", e); return []

# ---------- processing helpers ----------
def normalize_audio(input_path, output_path):
    try:
        cmd = f'ffmpeg -y -i "{input_path}" -af "loudnorm=I=-16:LRA=11:TP=-1.5" -c:v copy -c:a aac -b:a 192k "{output_path}"'
        sh(cmd)
        return True
    except Exception as e:
        print("normalize error", e); return False

def make_vertical_1080x1920(input_path, output_path):
    try:
        cmd = f'ffmpeg -y -i "{input_path}" -vf "scale=1080:-2, pad=1080:1920:(ow-iw)/2:(oh-ih)/2" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k "{output_path}"'
        sh(cmd)
        return True
    except Exception as e:
        print("vertical error", e); return False

def overlay_birds_if_needed(video_in, video_out):
    birds = MIXKIT_BIRDS or MIXKIT_FALLBACK
    tmp_audio = OUT_DIR / "birds_tmp.mp3"
    try:
        download_url_to(tmp_audio, birds)
        cmd = f'ffmpeg -y -i "{video_in}" -stream_loop -1 -i "{tmp_audio}" -filter_complex "[1:a]volume=0.12[a1];[0:a][a1]amerge=inputs=2[aout]" -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k "{video_out}"'
        sh(cmd)
        return True
    except Exception as e:
        print("overlay birds error", e); return False

def concat_list_and_final(list_txt, combined_path):
    try:
        sh(f'ffmpeg -y -f concat -safe 0 -i "{list_txt}" -c copy "{combined_path}"')
        return True
    except Exception as e:
        print("concat error", e); return False

def loop_to_target(src_path, target_seconds, out_path):
    try:
        sh(f'ffmpeg -y -stream_loop -1 -i "{src_path}" -t {int(target_seconds)} -af "loudnorm=I=-16:LRA=11:TP=-1.5" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k "{out_path}"')
        return True
    except Exception as e:
        print("loop error", e); return False

# ---------- hot hashtags ----------
HOT_HASHTAGS_MAP = {
    "rain": ["#rain","#rainysounds","#rainambience","#relaxingrain"],
    "ocean": ["#ocean","#oceanwaves","#seasounds","#beachambience"],
    "forest": ["#forest","#forestambience","#birds","#nature"],
    "waterfall": ["#waterfall","#waterfalls","#nature"],
    "snow": ["#snow","#winter","#cozy"],
    "clouds": ["#clouds","#sky","#relax"],
    "desertnight": ["#desert","#night","#stars"],
    "underwaterdiving": ["#underwater","#diving","#sealife"],
    "birds": ["#birds","#birdsong","#nature"],
    "sunset": ["#sunset","#goldenhour","#calm"],
    "droneaerial": ["#drone","#aerial","#cinematic"],
    "default": ["#relaxing","#nature","#sleep","#meditation"]
}
def get_hot_hashtags_for(topic):
    key = re.sub(r'\s+','',topic.lower())
    return HOT_HASHTAGS_MAP.get(key, HOT_HASHTAGS_MAP["default"])

# ---------- pick & download ----------
def pick_and_download_for_type(video_type, target_min_s, target_max_s, try_count=TRY_COUNT):
    ensure_dirs()
    attempts = 0
    while attempts < try_count:
        attempts += 1
        topic = random.choice(TOPICS)
        print(f"[search] Attempt {attempts} — topic: {topic}")

        candidates = []
        candidates += search_pexels(topic, per_page=MAX_DOWNLOAD_CANDIDATES//3)
        candidates += search_pixabay(topic, per_page=MAX_DOWNLOAD_CANDIDATES//3)
        candidates += search_coverr()
        candidates += search_videvo(topic, per_page=MAX_DOWNLOAD_CANDIDATES//6)
        candidates += search_lifeofvids(topic)
        candidates += search_internet_archive(topic, rows=6)
        random.shuffle(candidates)
        if not candidates:
            print("No candidates, retrying..."); time.sleep(2); continue

        downloaded = []
        for i, url in enumerate(candidates[:MAX_DOWNLOAD_CANDIDATES]):
            clip_path = CLIPS_DIR / f"clip_{attempts}_{i}.mp4"
            try:
                download_url_to(clip_path, url)
            except Exception as e:
                print("download failed:", e); continue
            dur = ffprobe_duration_seconds(clip_path)
            aud = has_audio_stream(clip_path)
            mv = audio_mean_volume_db(clip_path) if aud else None
            print(f"Downloaded {clip_path} dur={dur}s audio={aud} mean_v={mv}")
            if dur <= 0:
                clip_path.unlink(missing_ok=True); continue
            downloaded.append((clip_path, dur, aud, mv))
            if len(downloaded) >= 12: break

        if not downloaded:
            print("No downloaded clips, retrying."); time.sleep(1); continue

        if video_type == "shorts":
            for clip, dur, aud, mv in sorted(downloaded, key=lambda x: -x[1]):
                if not aud: continue
                if mv is None or mv <= AUDIO_MIN_DB: continue
                target = min(int(dur), SHORT_MAX_S)
                if target < 6: continue
                tmp = OUT_DIR / f"short_trim_{int(time.time())}.mp4"
                try:
                    sh(f'ffmpeg -y -i "{clip}" -t {target} -c copy "{tmp}"')
                except Exception:
                    try: sh(f'ffmpeg -y -i "{clip}" -ss 0 -t {target} -c copy "{tmp}"')
                    except Exception as e: print("Trim short error:", e); continue
                if not audio_ok(tmp, min_db=AUDIO_MIN_DB):
                    tmp.unlink(missing_ok=True); continue
                if ffprobe_duration_seconds(tmp) <= SHORT_THRESHOLD_SECONDS:
                    vert = OUT_DIR / f"short_vert_{int(time.time())}.mp4"
                    if make_vertical_1080x1920(tmp, vert):
                        return vert, topic
                    else:
                        return tmp, topic
                else:
                    return tmp, topic
            print("No suitable short found this attempt — retrying.")
            for p in CLIPS_DIR.glob("*"): p.unlink(missing_ok=True)
            for p in OUT_DIR.glob("*"):
                if p.is_file(): p.unlink(missing_ok=True)
            time.sleep(1); continue

        if video_type == "long":
            singles = [t for t in downloaded if t[1] >= target_min_s and t[2] and (t[3] is None or t[3] > AUDIO_MIN_DB)]
            singles = sorted(singles, key=lambda x: (-x[1], -(x[3] if x[3] is not None else -999)))
            for clip, dur, aud, mv in singles:
                print(f"Trying single candidate {clip} dur={dur} mv={mv}")
                if has_long_silence(clip, silence_db=-50, max_silence_seconds=1.5):
                    print("Rejected due to long silence.")
                    continue
                tmp_norm = OUT_DIR / f"long_single_norm_{int(time.time())}.mp4"
                if normalize_audio(clip, tmp_norm):
                    if audio_ok(tmp_norm, min_db=AUDIO_MIN_DB) and not has_long_silence(tmp_norm):
                        dur2 = ffprobe_duration_seconds(tmp_norm)
                        if dur2 > target_max_s:
                            trimmed = OUT_DIR / f"long_single_trim_{int(time.time())}.mp4"
                            sh(f'ffmpeg -y -i "{tmp_norm}" -t {target_max_s} -c copy "{trimmed}"')
                            tmp_norm = trimmed
                        return tmp_norm, topic
                    else:
                        tmp_norm.unlink(missing_ok=True)
                        continue
            print("No suitable single long clip found — will try concat/loop fallback.")

        acceptable = [t for t in downloaded if t[2] and (t[3] is None or t[3] > AUDIO_MIN_DB)]
        if not acceptable:
            print("No acceptable audio clips found this attempt, retrying.")
            for p in CLIPS_DIR.glob("*"): p.unlink(missing_ok=True)
            time.sleep(1); continue

        list_txt = OUT_DIR / "list.txt"
        if list_txt.exists(): list_txt.unlink()
        total = 0; idx = 0
        for clip, dur, aud, mv in acceptable:
            trim_t = min(dur, 180)
            out_trim = OUT_DIR / f"trim_{attempts}_{idx}.mp4"
            try:
                sh(f'ffmpeg -y -i "{clip}" -t {int(trim_t)} -c copy "{out_trim}"')
            except Exception as e:
                print("Trim error:", e); continue
            with open(list_txt, "a") as f:
                f.write(f"file '{out_trim.resolve()}'\n")
            total += int(trim_t); idx += 1
            if total >= target_min_s: break

        print(f"Total collected duration: {total}s (target {target_min_s}s)")
        if total >= target_min_s:
            combined = OUT_DIR / f"combined_{int(time.time())}.mp4"
            if not concat_list_and_final(list_txt, combined):
                print("Concat failed; retrying."); continue
            norm_comb = OUT_DIR / f"combined_norm_{int(time.time())}.mp4"
            if normalize_audio(combined, norm_comb):
                if ffprobe_duration_seconds(norm_comb) > target_max_s:
                    sh(f'ffmpeg -y -i "{norm_comb}" -t {target_max_s} -c copy "{norm_comb}"')
                if not has_long_silence(norm_comb):
                    return norm_comb, topic
                else:
                    birds_out = OUT_DIR / f"birds_{int(time.time())}.mp4"
                    if overlay_birds_if_needed(norm_comb, birds_out):
                        if audio_ok(birds_out, min_db=AUDIO_MIN_DB):
                            return birds_out, topic
            if not audio_ok(combined, min_db=AUDIO_MIN_DB):
                bg = OUT_DIR / "bg.mp3"
                download_url_to(bg, MIXKIT_FALLBACK)
                sh(f'ffmpeg -y -stream_loop -1 -i "{bg}" -i "{combined}" -shortest -c:v copy -c:a aac -b:a 192k "{FINAL_FILE}"')
                if ffprobe_duration_seconds(FINAL_FILE) < target_min_s:
                    tmp_loop = OUT_DIR / f"looped_{int(time.time())}.mp4"
                    if loop_to_target(FINAL_FILE, target_min_s, tmp_loop):
                        return tmp_loop, topic
                return FINAL_FILE, topic
            else:
                dur_comb = ffprobe_duration_seconds(combined)
                if dur_comb < target_min_s:
                    loop_out = OUT_DIR / f"looped_{int(time.time())}.mp4"
                    if loop_to_target(combined, target_min_s, loop_out):
                        return loop_out, topic
                else:
                    return combined, topic

        if len(acceptable) > 0:
            best = sorted(acceptable, key=lambda x: (-x[1], x[3] if x[3] is not None else 0))[0]
            best_clip = best[0]
            print(f"Not enough duration — looping best clip {best_clip}")
            loop_out = OUT_DIR / f"looped_{int(time.time())}.mp4"
            if loop_to_target(best_clip, target_min_s, loop_out):
                if audio_ok(loop_out, min_db=AUDIO_MIN_DB):
                    return loop_out, topic
            print("Loop fallback failed; retrying.")
            for p in CLIPS_DIR.glob("*"): p.unlink(missing_ok=True)
            for p in OUT_DIR.glob("*"): 
                if p.is_file(): p.unlink(missing_ok=True)
            time.sleep(1)
            continue

        for p in CLIPS_DIR.glob("*"): p.unlink(missing_ok=True)
        for p in OUT_DIR.glob("*"): 
            if p.is_file(): p.unlink(missing_ok=True)
        time.sleep(1)

    return None, None

# ---------- upload with retries ----------
def get_access_token():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise Exception("Missing Google OAuth env vars.")
    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=30)
    if r.status_code != 200:
        print("Token request failed:", r.status_code, r.text)
        raise Exception("Failed to get access token.")
    j = r.json()
    return j.get("access_token")

def upload_to_youtube(file_path, title, description, tags, privacy="public", max_attempts=3):
    last_exc = None
    for attempt in range(1, max_attempts+1):
        try:
            print(f"[upload] Attempt {attempt} to upload {file_path}")
            token = get_access_token()
            metadata = {"snippet":{"title":title,"description":description,"tags":tags,"categoryId":"22"},"status":{"privacyStatus":privacy}}
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
            resp = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", headers=headers, data=json.dumps(metadata), timeout=30)
            upload_url = resp.headers.get("Location") or resp.headers.get("location")
            if not upload_url:
                print("Create session failed (status):", resp.status_code, resp.text)
                raise Exception("No upload URL from YouTube.")
            # stream upload (may be large) — use streaming put
            with open(file_path, "rb") as f:
                upload_resp = requests.put(upload_url, data=f, headers={"Content-Type":"application/octet-stream"}, timeout=3600)
            if upload_resp.status_code not in (200,201):
                print("Upload returned non-OK:", upload_resp.status_code, upload_resp.text[:800])
                raise Exception(f"Upload failed status {upload_resp.status_code}")
            try:
                resj = upload_resp.json(); video_id = resj.get("id")
            except Exception:
                video_id = None
            return video_id
        except Exception as e:
            print(f"[upload] attempt {attempt} error:", e)
            last_exc = e
            time.sleep(3 * attempt)
            continue
    raise last_exc

# ---------- Titles/Description/Tags ----------
HOT_HASHTAGS_MAP = {
    "rain": ["#rain","#rainysounds","#rainambience","#relaxingrain"],
    "ocean": ["#ocean","#oceanwaves","#seasounds","#beachambience"],
    "forest": ["#forest","#forestambience","#birds","#nature"],
    "waterfall": ["#waterfall","#waterfalls","#nature"],
    "default": ["#relaxing","#nature","#sleep","#meditation"]
}
def get_hot_hashtags_for(topic):
    key = re.sub(r'\s+','',topic.lower())
    return HOT_HASHTAGS_MAP.get(key, HOT_HASHTAGS_MAP["default"])

def choose_title_desc(video_type, duration_seconds, topic):
    topic_clean = topic.title()
    emoji_map = {
        "Rain":"🌧️","Ocean":"🌊","Forest":"🌿","Waterfall":"💧","Snow":"❄️","Clouds":"☁️",
        "Desert Night":"🏜️","Mountain":"🏔️","River":"🏞️","Calm Beach":"🏝️","Winter Cozy":"🔥",
        "Campfire":"🔥","Underwater Diving":"🤿","Birds":"🐦","Sunset":"🌇","Sunrise":"🌅",
        "Drone Aerial":"🚁","Night Stars":"✨","Relaxing":"🧘"
    }
    emoji = emoji_map.get(topic_clean, "🌿")

    if video_type == "shorts":
        template = random.choice(TITLE_TEMPLATES["shorts"])
        base_title = template.format(topic_clean)
        title = f"{emoji} {base_title} {random.choice(['✨','💤','🌿'])}"
    elif video_type == "long":
        template = random.choice(TITLE_TEMPLATES["long"])
        minutes = max(1, int(math.ceil(duration_seconds/60.0)))
        title = f"{emoji} {template.format(topic_clean)}"
    else:
        template = random.choice(TITLE_TEMPLATES["very_long"])
        title = f"{emoji} {template.format(topic_clean)}"

    # NOTE: **do not** add channel handle to title per user's request

    hot = get_hot_hashtags_for(topic)
    general = ["#relaxing","#nature","#sleep","#meditation","#calm","#ambient","#relax","#soothing","#ASMR","#sleepmusic"]
    hashtags = list(dict.fromkeys(hot + general))[:12]
    hashtags_line = " ".join(hashtags)

    minutes = max(1, int(math.ceil(duration_seconds / 60.0)))
    use_line = ("Perfect for a quick calm break." if video_type=="shorts"
                else "Great for studying, working, deep relaxation, and sleep." if video_type=="long"
                else "Designed for long sleep cycles, overnight use, and deep meditation.")
    description_lines = [
        DESCRIPTION_TEMPLATE,
        "",
        f"🔔 Subscribe to @{CHANNEL_HANDLE} for daily relaxing uploads: https://www.youtube.com/@{CHANNEL_HANDLE}",
        "👍 Like the video if it helped you relax and share it with someone who needs calm.",
        "🛎️ Turn on the notification bell to get new uploads.",
        "",
        "🎧 Best with headphones for full immersion.",
        f"⏱️ Approx. duration: {minutes} minute(s). {use_line}",
        "",
        "—",
        f"Tags: {', '.join([t.strip('#') for t in hashtags[:8]])}",
        "",
        hashtags_line
    ]
    description = "\n".join(description_lines)

    tags = TAGS_BASE.copy()
    for h in hot:
        tags.append(h.lstrip("#"))
    tags.append(topic_clean.lower())
    if video_type == "shorts":
        if "shorts" not in tags:
            tags.append("shorts")
    if video_type == "very_long":
        tags.extend(["overnight","deep sleep"])
    tags = list(dict.fromkeys(tags))[:20]
    return title, description, tags

# ---------- Main ----------
def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--type":
        print("Usage: python main.py --type shorts|long|very_long"); sys.exit(1)
    vtype = sys.argv[2]
    if vtype not in ("shorts","long","very_long"):
        print("Invalid type"); sys.exit(1)
    if vtype == "shorts":
        min_d, max_d = 5, SHORT_MAX_S
    elif vtype == "long":
        min_d, max_d = LONG_MIN_S, LONG_MAX_S
    else:
        min_d, max_d = VERY_LONG_MIN_S, 3*60*60

    print(f"[start] type={vtype} target {min_d}-{max_d}s (AUDIO_MIN_DB={AUDIO_MIN_DB})")
    ensure_dirs()

    attempts = 0
    while attempts < TRY_COUNT:
        attempts += 1
        print(f"[main] build attempt {attempts}/{TRY_COUNT}")
        final, topic = pick_and_download_for_type(vtype, min_d, max_d, try_count=6)
        if not final:
            print("[main] producer failed, trying again")
            continue
        dur = ffprobe_duration_seconds(final)
        print(f"[main] produced file={final} dur={dur}s audio_ok={audio_ok(final)}")
        if not audio_ok(final):
            print("[main] audio check failed, removing file and retrying")
            try: final.unlink(missing_ok=True)
            except: pass
            continue
        title, desc, tags = choose_title_desc(vtype, dur, topic or "relaxing")
        print("[main] Title:", title)
        try:
            vid = upload_to_youtube(final, title, desc, tags, privacy="public", max_attempts=3)
            if not vid:
                print("[main] upload returned no id, retrying full cycle")
                continue
            youtube_url = f"https://youtu.be/{vid}"
            print("[done] Uploaded:", youtube_url)
            with open(UPLOAD_LOG, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{vid},{title}\n")
            return
        except Exception as e:
            print("[main] upload failed:", e)
            # try again: produce a new video and upload
            continue

    print("Reached TRY_COUNT without success — aborting.")
    sys.exit(1)

if __name__ == "__main__":
    main()
