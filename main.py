#!/usr/bin/env python3
# main.py — Calm Loop uploader
# Usage: python main.py --type shorts|long|very_long
# Requires: ffmpeg, ffprobe, python requests

import os
import sys
import subprocess
import requests
import json
import time
import random
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
    "to help you relax, sleep, meditate, and focus. This channel is dedicated to creating "
    "long, seamless loops of nature sounds that soothe the mind and body.\n\n"
    "🔔 Subscribe to Calm Loop for daily relaxing uploads: https://www.youtube.com/@CalmLoop\n\n"
    "✅ Use this video to relax, study, sleep, or meditate. If it helped you, please Like & Share.\n\n"
    "— Content: rain, ocean waves, forest ambience, waterfalls, night desert, winter calm, mountain breeze.\n\n"
    "Tags/Keywords: relaxing sounds, sleep sounds, meditation music, ambient nature, calm loop, deep sleep\n\n"
    "Channel: Calm Loop"
)

TAGS_BASE = [
    "relaxing", "nature", "sleep", "meditation", "ambient", "calm", "relax", "soothing", "ASMR", "english"
]

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

# Paths
WORKDIR = Path("work")
CLIPS_DIR = WORKDIR / "clips"
OUT_DIR = WORKDIR / "out"
FINAL_FILE = WORKDIR / "final_video.mp4"
UPLOAD_LOG = Path("uploads_log.csv")

# ---------------- utilities ----------------
def sh(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, shell=True).decode('utf-8', errors='ignore')
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

def has_audio(path):
    try:
        out = sh(f'ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 "{path}"', capture=True)
        return bool(out.strip())
    except Exception:
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

# ---------------- Search functions ----------------
def search_pexels(query, per_page=15):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": per_page}
    try:
        r = requests.get(PEXELS_SEARCH, headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            print("Pexels returned", r.status_code)
            return []
        data = r.json()
        videos = data.get("videos", [])
        results = []
        for v in videos:
            files = v.get("video_files", [])
            files_sorted = sorted(files, key=lambda x: (0 if x.get('quality')=='hd' else 1, -int(x.get('width',0))))
            if files_sorted:
                # take highest quality link
                results.append(files_sorted[0].get('link'))
        return results
    except Exception as e:
        print("Pexels search error:", e)
        return []

def search_pixabay(query, per_page=20):
    if not PIXABAY_API_KEY:
        return []
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": per_page}
    try:
        r = requests.get(PIXABAY_SEARCH, params=params, timeout=20)
        if r.status_code != 200:
            print("Pixabay returned", r.status_code)
            return []
        data = r.json()
        hits = data.get("hits", [])
        results = []
        for h in hits:
            vids = h.get("videos", {})
            # prefer medium -> large -> tiny
            for key in ("medium","large","tiny"):
                if key in vids and vids[key].get("url"):
                    results.append(vids[key]["url"])
                    break
        return results
    except Exception as e:
        print("Pixabay search error:", e)
        return []

def search_coverr():
    if not COVERR_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {COVERR_API_KEY}"}
    try:
        r = requests.get(COVERR_SEARCH, headers=headers, timeout=20)
        if r.status_code != 200:
            print("Coverr returned", r.status_code)
            return []
        data = r.json()
        urls = []
        for d in data.get("data", []):
            assets = d.get("assets", [])
            if assets:
                urls.append(assets[0].get("url"))
        return urls
    except Exception as e:
        print("Coverr search error:", e)
        return []

# ---------------- Build & selection ----------------
def pick_and_download_for_type(video_type, target_min_s, target_max_s, try_count=20):
    ensure_dirs()
    attempts = 0
    while attempts < try_count:
        attempts += 1
        topic = random.choice(TOPICS)
        print(f"[search] Attempt {attempts} — topic: {topic}")

        candidates = []
        candidates += search_pexels(topic, per_page=12)
        candidates += search_pixabay(topic, per_page=20)
        candidates += search_coverr()

        random.shuffle(candidates)
        if not candidates:
            print("No candidates found, retrying...")
            time.sleep(2)
            continue

        # download a few candidates
        downloaded = []
        for i, url in enumerate(candidates[:12]):
            clip_path = CLIPS_DIR / f"clip_{attempts}_{i}.mp4"
            try:
                download_url_to(clip_path, url)
            except Exception as e:
                print("Download failed:", e)
                continue
            dur = ffprobe_duration_seconds(clip_path)
            audio = has_audio(clip_path)
            print(f"Downloaded {clip_path} dur={dur}s audio={audio}")
            if dur <= 0:
                clip_path.unlink(missing_ok=True)
                continue
            downloaded.append((clip_path, dur, audio))
            # if enough small clips -> break
            if len(downloaded) >= 8:
                break

        if not downloaded:
            print("No downloaded clips, retrying...")
            time.sleep(1)
            continue

        # SHORTS: pick single clip with audio and trim to <=60s
        if video_type == "shorts":
            candidates_with_audio = [t for t in downloaded if t[2]]
            # prefer those with reasonable length >=15s
            candidates_with_audio = sorted(candidates_with_audio, key=lambda x: -x[1])
            for clip, dur, _ in candidates_with_audio:
                if dur < 4:
                    continue
                target = min(int(dur), 60)
                if target < 10:
                    target = min(10, int(dur))
                outp = OUT_DIR / f"short_{int(time.time())}.mp4"
                try:
                    sh(f'ffmpeg -y -i "{clip}" -t {target} -c copy "{outp}"')
                except Exception:
                    try:
                        sh(f'ffmpeg -y -i "{clip}" -ss 0 -t {target} -c copy "{outp}"')
                    except Exception as e:
                        print("ffmpeg short trim error:", e)
                        continue
                if has_audio(outp):
                    return outp
                else:
                    print("Trimmed short has no audio, trying next clip.")
            print("No suitable short found, retrying topic.")
            # cleanup
            for p in CLIPS_DIR.glob("*"):
                try: p.unlink()
                except: pass
            time.sleep(1)
            continue

        # LONG and VERY_LONG: assemble by concatenating several audio+video clips
        # keep only clips with audio
        with_audio = [t for t in downloaded if t[2]]
        if not with_audio:
            print("No downloaded clips with audio, retrying.")
            for p in CLIPS_DIR.glob("*"):
                try: p.unlink()
                except: pass
            time.sleep(1)
            continue

        # create trimmed segments (max 180s each) and concat until reach target_min_s
        list_txt = OUT_DIR / "list.txt"
        if list_txt.exists():
            list_txt.unlink()
        total = 0
        idx = 0
        for clip, dur, _ in with_audio:
            trim_t = min(dur, 180)
            out_trim = OUT_DIR / f"trim_{attempts}_{idx}.mp4"
            try:
                sh(f'ffmpeg -y -i "{clip}" -t {int(trim_t)} -c copy "{out_trim}"')
            except Exception as e:
                print("Trim error:", e)
                continue
            with open(list_txt, "a") as f:
                f.write(f"file '{out_trim.resolve()}'\n")
            total += int(trim_t)
            idx += 1
            if total >= target_min_s:
                break

        if total < target_min_s:
            print(f"Total {total}s < target {target_min_s}s, retrying.")
            for p in CLIPS_DIR.glob("*"):
                try: p.unlink()
                except: pass
            for p in OUT_DIR.glob("*"):
                try: p.unlink()
                except: pass
            time.sleep(1)
            continue

        # concat
        combined = OUT_DIR / f"combined_{int(time.time())}.mp4"
        try:
            sh(f'ffmpeg -y -f concat -safe 0 -i "{list_txt}" -c copy "{combined}"')
        except Exception as e:
            print("Concat error:", e)
            continue

        # trim to max if needed
        dur_comb = ffprobe_duration_seconds(combined)
        if dur_comb > target_max_s:
            trimmed = OUT_DIR / f"final_trim_{int(time.time())}.mp4"
            sh(f'ffmpeg -y -i "{combined}" -t {target_max_s} -c copy "{trimmed}"')
            combined = trimmed

        # ensure audio present
        if not has_audio(combined):
            print("Combined has no audio — will attach fallback music.")
            bg = OUT_DIR / "bg.mp3"
            try:
                download_url_to(bg, MIXKIT_FALLBACK)
                sh(f'ffmpeg -y -stream_loop -1 -i "{bg}" -i "{combined}" -shortest -c:v copy -c:a aac -b:a 128k "{FINAL_FILE}"')
                return FINAL_FILE
            except Exception as e:
                print("Failed attaching fallback:", e)
                continue
        else:
            # move/copy to final
            try:
                if FINAL_FILE.exists():
                    FINAL_FILE.unlink()
                combined.rename(FINAL_FILE)
            except Exception:
                sh(f'ffmpeg -y -i "{combined}" -c copy "{FINAL_FILE}"')
            return FINAL_FILE

    return None

# ---------------- Google OAuth & Upload ----------------
def get_access_token():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise Exception("Missing Google OAuth env vars.")
    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=30)
    except Exception as e:
        print("Token request error:", e)
        raise
    if r.status_code != 200:
        print("Token request failed:", r.status_code, r.text)
        raise Exception("Failed to get access token.")
    j = r.json()
    return j.get("access_token")

def upload_to_youtube(file_path, title, description, tags, privacy="public", categoryId="22"):
    print(f"[upload] Starting upload: {file_path}")
    token = get_access_token()
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": categoryId
        },
        "status": {
            "privacyStatus": privacy
        }
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    try:
        resp = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers=headers, data=json.dumps(metadata), timeout=30
        )
    except Exception as e:
        print("Create upload session error:", e)
        raise

    # Accept 200 or 201 and require Location header
    upload_url = resp.headers.get("Location") or resp.headers.get("location")
    if not upload_url:
        print("Create session failed, status:", resp.status_code, "body:", resp.text, "headers:", resp.headers)
        raise Exception("No upload URL returned by YouTube.")
    print("Upload URL obtained, uploading file (may take long)...")
    # perform upload PUT with chunked streaming if needed
    try:
        with open(file_path, "rb") as f:
            upload_resp = requests.put(upload_url, data=f, headers={"Content-Type":"application/octet-stream"}, timeout=1200)
    except Exception as e:
        print("Upload error:", e)
        raise
    if upload_resp.status_code not in (200,201):
        print("Upload failed:", upload_resp.status_code, upload_resp.text[:800])
        raise Exception("Upload failed.")
    # try parse response json
    try:
        resj = upload_resp.json()
        video_id = resj.get("id")
    except Exception:
        video_id = None
    print("Upload completed, video id:", video_id)
    return video_id

# ---------------- Titles / Description ----------------
def choose_title_desc(video_type, duration_seconds):
    templates = TITLE_TEMPLATES.get(video_type, TITLE_TEMPLATES["long"])
    title = random.choice(templates)
    # 40% chance to add emoji suffix
    suffixes = ["✨", "🌿", "🌊", "🌙", "💤"]
    if random.random() < 0.4:
        title = f"{title} {random.choice(suffixes)}"

    minutes = max(1, int(duration_seconds // 60))
    if video_type == "shorts":
        use_line = "Perfect for a quick calm break."
    elif video_type == "long":
        use_line = "Great for studying, working, deep relaxation, and sleep."
    else:
        use_line = "Designed for long sleep cycles, overnight use, and deep meditation."

    description = DESCRIPTION_TEMPLATE + f"\n\nDuration: approx {minutes} minutes. {use_line}\n\nHashtags: #relaxing #nature #sleep #meditation #calm"
    tags = TAGS_BASE.copy()
    # add some context tags
    for kw in ["rain","ocean","forest","waterfall","snow","clouds","desert","mountain","river","campfire","night","winter"]:
        if kw in title.lower() and kw not in tags:
            tags.append(kw)
    return title, description, tags[:20]

# ---------------- Main ----------------
def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--type":
        print("Usage: python main.py --type shorts|long|very_long")
        sys.exit(1)
    vtype = sys.argv[2]
    if vtype not in ("shorts","long","very_long"):
        print("Invalid type")
        sys.exit(1)

    # targets in seconds
    if vtype == "shorts":
        min_d, max_d = 15, 60
    elif vtype == "long":
        min_d, max_d = 10*60, 50*60
    else:
        min_d, max_d = 60*60, 3*60*60  # 1h - 3h

    print(f"[start] Building type={vtype} target {min_d}s - {max_d}s")
    ensure_dirs()

    final = pick_and_download_for_type(vtype, min_d, max_d, try_count=25)
    if not final:
        print("Failed to produce final video — aborting.")
        sys.exit(1)

    dur = ffprobe_duration_seconds(final)
    print(f"[final] file={final} duration={dur}s audio={has_audio(final)}")

    # strict audio check
    if not has_audio(final):
        print("Final video has no audio — aborting upload.")
        sys.exit(1)

    title, desc, tags = choose_title_desc(vtype, dur)
    print("Title:", title)
    try:
        video_id = upload_to_youtube(final, title, desc, tags, privacy="public")
    except Exception as e:
        print("Upload error:", e)
        sys.exit(1)

    if not video_id:
        print("Upload completed but no video id returned.")
        sys.exit(1)

    youtube_url = f"https://youtu.be/{video_id}"
    print("[done] Uploaded successfully:", youtube_url)
    # log upload
    with open(UPLOAD_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{video_id},{title}\n")

if __name__ == "__main__":
    main()
