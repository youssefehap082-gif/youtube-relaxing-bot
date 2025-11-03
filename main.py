#!/usr/bin/env python3
# main.py — Calm Loop uploader (fixed for very_long loop fallback + audio threshold)
# Usage: python main.py --type shorts|long|very_long
# Requires: ffmpeg, ffprobe, python requests

import os, sys, subprocess, requests, json, time, random, re
from pathlib import Path

# ---------------- Config ----------------
CHANNEL_NAME = "Calm Loop"
TOPICS = [
    "relaxing", "rain", "ocean", "forest", "waterfall", "snow", "clouds",
    "desert night", "mountain", "river", "calm beach", "winter cozy", "campfire", "night stars"
]

TITLE_TEMPLATES = {
    "shorts": [
        "🌧️ Instant Calm — Calming Rain Sounds",
        "🌊 Ocean Breeze — Quick Relaxation",
        "🍃 Forest Breeze — A Moment to Breathe",
        "💧 Gentle Waterfall — Mini Calm Escape",
        "✨ Soothing Nature Clip — Reset Your Mind"
    ],
    "long": [
        "Relaxing Nature Sounds for Deep Relaxation ✨",
        "Soothing Ocean Waves to Help You Unwind 🌊",
        "Rain Ambience for Sleep & Focus 🌧️",
        "Peaceful Forest Ambience — Calm Your Mind 🌿",
        "Gentle River Flow — Meditation & Sleep"
    ],
    "very_long": [
        "Ultimate Relaxing Nature Mix for Deep Sleep 🌙",
        "Extended Rain & Ocean Ambience — Sleep Through the Night 🌧️",
        "Long Forest & River Sounds for Meditation & Rest 🍃",
        "Overnight Calm — Deep Relaxation & Continuous Nature Sounds"
    ]
}

DESCRIPTION_TEMPLATE = (
    "Calm Loop brings you high-quality relaxing ambient sounds and peaceful nature visuals "
    "to help you relax, sleep, meditate, and focus.\n\n"
    "🔔 Subscribe to Calm Loop for daily relaxing uploads: https://www.youtube.com/@CalmLoop\n\n"
    "✅ Use this video to relax, study, sleep, or meditate. If it helped you, please Like & Share.\n\n"
)

TAGS_BASE = ["relaxing","nature","sleep","meditation","ambient","calm","relax","soothing","ASMR","english"]

MIXKIT_FALLBACK = "https://assets.mixkit.co/music/preview/mixkit-relaxing-piano-628.mp3"

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
COVERR_SEARCH = "https://api.coverr.co/videos"

# Env / secrets
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")
COVERR_API_KEY = os.environ.get("COVERR_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")

# thresholds & behavior
AUDIO_MIN_DB = float(os.environ.get("AUDIO_MIN_DB", "-60.0"))  # default -60 dB (more lenient)
MAX_DOWNLOAD_CANDIDATES = int(os.environ.get("MAX_CANDIDATES", "20"))
TRY_COUNT = int(os.environ.get("TRY_COUNT", "30"))

# Paths
WORKDIR = Path("work")
CLIPS_DIR = WORKDIR / "clips"
OUT_DIR = WORKDIR / "out"
FINAL_FILE = WORKDIR / "final_video.mp4"
UPLOAD_LOG = Path("uploads_log.csv")

# ---------------- helpers ----------------
def sh(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
    else:
        subprocess.check_call(cmd, shell=True)

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

def download_url_to(path, url, timeout=60):
    print(f"Downloading {url} → {path}")
    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path

# ---------------- Search functions ----------------
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
        vids = []
        for v in data.get("videos", []):
            files = v.get("video_files", [])
            if files:
                # choose highest width
                best = sorted(files, key=lambda x: int(x.get("width",0)), reverse=True)[0]
                link = best.get("link")
                if link:
                    vids.append(link)
        return vids
    except Exception:
        return []

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
    except Exception:
        return []

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
    except Exception:
        return []

# ---------------- Video assembly ----------------
def concat_list_and_final(list_txt, combined_path):
    try:
        sh(f'ffmpeg -y -f concat -safe 0 -i "{list_txt}" -c copy "{combined_path}"')
        return True
    except Exception as e:
        print("Concat error:", e)
        return False

def loop_to_target(src_path, target_seconds, out_path):
    """
    Create a file of length target_seconds by looping src_path.
    We'll re-encode to ensure compatibility.
    """
    try:
        sh(f'ffmpeg -y -stream_loop -1 -i "{src_path}" -t {int(target_seconds)} -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k "{out_path}"')
        return True
    except Exception as e:
        print("Loop/encode error:", e)
        return False

def attach_fallback_audio_if_needed(src_video, bg_url, out_path):
    """
    If src_video has no (acceptable) audio, download bg_url and mix it.
    """
    bg = OUT_DIR / "bg_fallback.mp3"
    try:
        download_url_to(bg, bg_url, timeout=60)
        sh(f'ffmpeg -y -i "{src_video}" -stream_loop -1 -i "{bg}" -shortest -c:v copy -c:a aac -b:a 128k "{out_path}"')
        return True
    except Exception as e:
        print("Attach fallback audio error:", e)
        return False

# ---------------- Core: pick & assemble with fallback loop ----------------
def pick_and_download_for_type(video_type, target_min_s, target_max_s, try_count=TRY_COUNT):
    ensure_dirs()
    attempts = 0
    while attempts < try_count:
        attempts += 1
        topic = random.choice(TOPICS)
        print(f"[search] Attempt {attempts} — topic: {topic}")

        candidates = []
        candidates += search_pexels(topic, per_page=MAX_DOWNLOAD_CANDIDATES//2)
        candidates += search_pixabay(topic, per_page=MAX_DOWNLOAD_CANDIDATES//2)
        candidates += search_coverr()
        random.shuffle(candidates)

        if not candidates:
            print("No candidates found, retrying...")
            time.sleep(2); continue

        downloaded = []
        for i, url in enumerate(candidates[:MAX_DOWNLOAD_CANDIDATES]):
            clip_path = CLIPS_DIR / f"clip_{attempts}_{i}.mp4"
            try:
                download_url_to(clip_path, url)
            except Exception as e:
                print("Download failed:", e); continue
            dur = ffprobe_duration_seconds(clip_path)
            aud = has_audio_stream(clip_path)
            mv = audio_mean_volume_db(clip_path) if aud else None
            print(f"Downloaded {clip_path} dur={dur}s audio_stream={aud} mean_v={mv}")
            if dur <= 0:
                clip_path.unlink(missing_ok=True); continue
            downloaded.append((clip_path, dur, aud, mv))
            if len(downloaded) >= 12: break

        if not downloaded:
            print("No downloaded clips, retrying.")
            time.sleep(1); continue

        # SHORTS processing (single clip)
        if video_type == "shorts":
            # pick clip with audio and acceptable loudness
            for clip, dur, aud, mv in sorted(downloaded, key=lambda x: -x[1]):
                if not aud: continue
                if mv is None or mv <= AUDIO_MIN_DB:
                    print(f"Clip {clip} mean_v={mv} dB too low, skip.")
                    continue
                target = min(int(dur), 60)
                if target < 6: continue
                tmp = OUT_DIR / f"short_trim_{int(time.time())}.mp4"
                try:
                    sh(f'ffmpeg -y -i "{clip}" -t {target} -c copy "{tmp}"')
                except Exception:
                    try: sh(f'ffmpeg -y -i "{clip}" -ss 0 -t {target} -c copy "{tmp}"')
                    except Exception as e: print("Trim short error:", e); continue
                # ensure audio ok
                if not audio_ok(tmp, min_db=AUDIO_MIN_DB):
                    print("Trimmed short audio fail, skipping."); tmp.unlink(missing_ok=True); continue
                # convert to vertical (optional): pad to 1080x1920
                vert = OUT_DIR / f"short_vert_{int(time.time())}.mp4"
                try:
                    sh(f'ffmpeg -y -i "{tmp}" -vf "scale=1080:-2, pad=1080:1920:(ow-iw)/2:(oh-ih)/2" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 128k "{vert}"')
                    return vert
                except Exception:
                    print("Vertical conversion failed, using trimmed file."); return tmp

        # LONG/VERY_LONG assembly: keep only decent audio clips
        with_audio = [t for t in downloaded if t[2] and (t[3] is None or t[3] > AUDIO_MIN_DB)]
        if not with_audio:
            print("No good audio clips this attempt, retrying.")
            time.sleep(1); continue

        # create trimmed segments and concat
        list_txt = OUT_DIR / "list.txt"
        if list_txt.exists(): list_txt.unlink()
        total = 0; idx = 0
        for clip, dur, aud, mv in with_audio:
            trim_t = min(dur, 180)  # max 3 min each
            out_trim = OUT_DIR / f"trim_{attempts}_{idx}.mp4"
            try:
                sh(f'ffmpeg -y -i "{clip}" -t {int(trim_t)} -c copy "{out_trim}"')
            except Exception as e:
                print("Trim error:", e); continue
            with open(list_txt, "a") as f: f.write(f"file '{out_trim.resolve()}'\n")
            total += int(trim_t); idx += 1
            if total >= target_min_s: break

        print(f"Collected total {total}s (target {target_min_s}s).")
        if total >= target_min_s:
            combined = OUT_DIR / f"combined_{int(time.time())}.mp4"
            if not concat_list_and_final(list_txt, combined):
                print("Concat failed; retrying.") ; continue
            # trim to max if needed
            dur_comb = ffprobe_duration_seconds(combined)
            if dur_comb > target_max_s:
                sh(f'ffmpeg -y -i "{combined}" -t {target_max_s} -c copy "{combined}"')
            # ensure audio ok, else attach fallback
            if not audio_ok(combined, min_db=AUDIO_MIN_DB):
                print("Combined audio insufficient — attaching fallback music.")
                if attach_fallback_audio_if_needed(combined, MIXKIT_FALLBACK, FINAL_FILE):
                    return FINAL_FILE
                else:
                    print("Fallback attach failed; retrying.")
                    continue
            else:
                # move to final path
                try:
                    if FINAL_FILE.exists(): FINAL_FILE.unlink()
                    combined.rename(FINAL_FILE)
                except Exception:
                    sh(f'ffmpeg -y -i "{combined}" -c copy "{FINAL_FILE}"')
                return FINAL_FILE

        # NOT ENOUGH total: handle fallback (loop) for long/very_long
        if total > 0 and video_type in ("long","very_long"):
            print("Not enough unique duration. Will build combined and loop to reach target (fallback).")
            combined = OUT_DIR / f"combined_partial_{int(time.time())}.mp4"
            if not concat_list_and_final(list_txt, combined):
                print("Concat of partial failed; retrying.") ; continue
            # if combined has no audio, attach fallback first
            if not audio_ok(combined, min_db=AUDIO_MIN_DB):
                print("Partial combined has insufficient audio; attaching fallback background music.")
                partial_with_audio = OUT_DIR / f"combined_partial_with_audio_{int(time.time())}.mp4"
                if not attach_fallback_audio_if_needed(combined, MIXKIT_FALLBACK, partial_with_audio):
                    print("Failed to attach fallback audio; retrying.")
                    continue
                combined = partial_with_audio
            # now loop to target_min_s
            loop_out = FINAL_FILE
            if loop_to_target(combined, target_min_s, loop_out):
                print(f"Created looped final video {loop_out} of length {target_min_s}s")
                return loop_out
            else:
                print("Looping failed; retrying.")
                continue

        # else retry next attempt (not enough total and no partial)
        print("Total insufficient and no fallback possible in this attempt — retrying.")
        # cleanup small files to free space
        for p in CLIPS_DIR.glob("*"):
            try: p.unlink()
            except: pass
        for p in OUT_DIR.glob("*"):
            try: p.unlink()
            except: pass
        time.sleep(1)
        continue

    # exhausted attempts -> final fallback: if any partial combined exists, try loop it
    print("Exhausted attempts. Trying final fallback from any available trims.")
    # attempt to build from whatever trims exist
    trims = list(OUT_DIR.glob("trim_*.mp4"))
    if trims:
        list_txt = OUT_DIR / "list_final.txt"
        with open(list_txt, "w") as f:
            for t in trims:
                f.write(f"file '{t.resolve()}'\n")
        combined = OUT_DIR / f"combined_final_{int(time.time())}.mp4"
        if concat_list_and_final(list_txt, combined):
            # attach fallback audio if needed
            if not audio_ok(combined, min_db=AUDIO_MIN_DB):
                attach_fallback_audio_if_needed(combined, MIXKIT_FALLBACK, FINAL_FILE)
            else:
                sh(f'ffmpeg -y -i "{combined}" -c copy "{FINAL_FILE}"')
            # try to loop to minimal target for very_long (if needed)
            # choose a safe minimal fallback loop length: use max(target_min_s, 600)
            try_target = max(600, target_min_s)
            if loop_to_target(FINAL_FILE, try_target, FINAL_FILE):
                return FINAL_FILE
    return None

# ---------------- Google OAuth & Upload (unchanged) ----------------
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

def upload_to_youtube(file_path, title, description, tags, privacy="public", categoryId="22", is_short=False):
    print(f"[upload] Uploading {file_path} (short={is_short}) as {title}")
    token = get_access_token()
    metadata = {"snippet":{"title":title,"description":description,"tags":tags,"categoryId":categoryId},"status":{"privacyStatus":privacy}}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    resp = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", headers=headers, data=json.dumps(metadata), timeout=30)
    upload_url = resp.headers.get("Location") or resp.headers.get("location")
    if not upload_url:
        print("Create session failed, status:", resp.status_code, "body:", resp.text)
        raise Exception("No upload URL returned by YouTube.")
    with open(file_path, "rb") as f:
        upload_resp = requests.put(upload_url, data=f, headers={"Content-Type":"application/octet-stream"}, timeout=1800)
    if upload_resp.status_code not in (200,201):
        print("Upload failed:", upload_resp.status_code, upload_resp.text[:800])
        raise Exception("Upload failed.")
    try:
        resj = upload_resp.json(); video_id = resj.get("id")
    except Exception:
        video_id = None
    return video_id

# ---------------- Titles / Description ----------------
def choose_title_desc(video_type, duration_seconds):
    templates = TITLE_TEMPLATES.get(video_type, TITLE_TEMPLATES["long"])
    title = random.choice(templates)
    if video_type == "shorts" and "#shorts" not in title.lower():
        title = f"{title} #shorts"
    elif random.random() < 0.4:
        title = f"{title} {random.choice(['✨','🌿','🌊','🌙','💤'])}"
    minutes = max(1, int(duration_seconds // 60))
    if video_type == "shorts":
        use_line = "Perfect for a quick calm break."
    elif video_type == "long":
        use_line = "Great for studying, working, deep relaxation, and sleep."
    else:
        use_line = "Designed for long sleep cycles, overnight use, and deep meditation."
    description = DESCRIPTION_TEMPLATE + f"\n\nDuration: approx {minutes} minutes. {use_line}\n\nHashtags: #relaxing #nature #sleep #meditation #calm"
    tags = TAGS_BASE.copy()
    if video_type == "shorts" and "shorts" not in tags: tags.append("shorts")
    return title, description, tags[:20]

# ---------------- Main ----------------
def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--type":
        print("Usage: python main.py --type shorts|long|very_long"); sys.exit(1)
    vtype = sys.argv[2]
    if vtype not in ("shorts","long","very_long"): print("Invalid type"); sys.exit(1)
    if vtype == "shorts": min_d, max_d = 5, 60
    elif vtype == "long": min_d, max_d = 10*60, 50*60
    else: min_d, max_d = 60*60, 3*60*60
    print(f"[start] Building type={vtype} target {min_d}s - {max_d}s (AUDIO_MIN_DB={AUDIO_MIN_DB})")
    ensure_dirs()
    final = pick_and_download_for_type(vtype, min_d, max_d, try_count=TRY_COUNT)
    if not final:
        print("Failed to produce final video — aborting."); sys.exit(1)
    dur = ffprobe_duration_seconds(final)
    print(f"[final] file={final} duration={dur}s audio_ok={audio_ok(final)}")
    if not audio_ok(final):
        print("Final audio check failed — abort"); sys.exit(1)
    title, desc, tags = choose_title_desc(vtype, dur)
    print("Title:", title)
    try:
        vid = upload_to_youtube(final, title, desc, tags, privacy="public", is_short=(vtype=="shorts"))
    except Exception as e:
        print("Upload error:", e); sys.exit(1)
    if not vid:
        print("Upload completed but no video id returned."); sys.exit(1)
    youtube_url = f"https://youtu.be/{vid}"
    print("[done] Uploaded:", youtube_url)
    with open(UPLOAD_LOG, "a") as f: f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{vid},{title}\n")

if __name__ == "__main__":
    main()
