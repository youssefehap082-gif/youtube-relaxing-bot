#!/usr/bin/env python3
# main.py
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

# --- Config / Topics / Templates ---
CHANNEL_NAME = "Calm Loop"
TOPICS = [
    "relaxing", "rain", "ocean", "forest", "waterfall", "snow", "clouds",
    "desert night", "mountain", "river", "calm beach", "winter cozy", "campfire"
]
# Titles/templates (English)
TITLE_TEMPLATES = {
    "shorts": [
        "Relaxing Rain — {len}s Calm Moment",
        "Soothing Ocean Waves — {len}s",
        "Forest Ambience — {len}s Relax",
        "Gentle Waterfall — {len}s"
    ],
    "long": [
        "Relaxing Nature Sounds for Sleep & Focus — {len} minutes",
        "Calm Ocean Waves & Ambient Nature — {len} minutes",
        "Rain & Thunder for Deep Sleep — {len} minutes",
        "Peaceful Forest Ambience for Relaxation — {len} minutes"
    ],
    "very_long": [
        "Ultimate Relaxing Nature Mix — {len} minutes (Deep Sleep)",
        "Extended Rain & Ocean Sounds — {len} minutes",
        "Long Overnight Ambience — {len} minutes for Sleep & Meditation"
    ]
}

DESCRIPTION_TEMPLATE = (
    "Calm Loop presents high-quality relaxing ambient sounds and visuals to help you relax, "
    "sleep, meditate, and focus. Channel: Calm Loop. \n\n"
    "If you enjoy this video, please subscribe for more relaxing loops.\n\n"
    "Tags: #relaxing #nature #sleep #meditation #ambient #calm"
)

TAGS_BASE = ["relaxing","nature","sleep","meditation","ambient","calm","relax"]

# Mixkit fallback mp3 (public preview)
MIXKIT_FALLBACK = "https://assets.mixkit.co/music/preview/mixkit-relaxing-piano-628.mp3"

# API endpoints
PEXELS_SEARCH = "https://api.pexels.com/videos/search"
PIXABAY_SEARCH = "https://pixabay.com/api/videos/"
COVERR_SEARCH = "https://api.coverr.co/videos"  # optional

# env secrets
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY")
COVERR_API_KEY = os.environ.get("COVERR_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")

# utility
WORKDIR = Path("work")
CLIPS_DIR = WORKDIR / "clips"
OUT_DIR = WORKDIR / "out"
FINAL_FILE = WORKDIR / "final_video.mp4"

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
        out = sh(f"ffprobe -v error -show_entries format=duration -of csv=p=0 \"{path}\"", capture=True)
        return float(out.strip())
    except Exception:
        return 0.0

def has_audio(path):
    try:
        out = sh(f"ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 \"{path}\"", capture=True)
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

def search_pexels(query, per_page=15):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": query, "per_page": per_page}
    r = requests.get(PEXELS_SEARCH, headers=headers, params=params, timeout=20)
    if r.status_code != 200:
        return []
    data = r.json()
    videos = data.get("videos", [])
    results = []
    for v in videos:
        files = v.get("video_files", [])
        # prefer hd
        files_sorted = sorted(files, key=lambda x: (x.get('quality')!='hd', x.get('width',0)), reverse=False)
        if files_sorted:
            results.append({
                "source":"pexels",
                "id": v.get("id"),
                "files": files_sorted
            })
    return results

def search_pixabay(query, per_page=20):
    if not PIXABAY_API_KEY:
        return []
    params = {"key": PIXABAY_API_KEY, "q": query, "per_page": per_page}
    r = requests.get(PIXABAY_SEARCH, params=params, timeout=20)
    if r.status_code != 200:
        return []
    data = r.json()
    hits = data.get("hits", [])
    results = []
    for h in hits:
        # hits[].videos has tiny/medium/large urls
        results.append({
            "source":"pixabay",
            "id": h.get("id"),
            "videos": h.get("videos", {})
        })
    return results

def search_coverr():
    if not COVERR_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {COVERR_API_KEY}"}
    try:
        r = requests.get(COVERR_SEARCH, headers=headers, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        # structure varies; pick url from data[*].assets[0].url
        res = []
        for d in data.get("data", []):
            assets = d.get("assets", [])
            if assets:
                res.append({"source":"coverr", "id": d.get("id"), "assets": assets})
        return res
    except Exception:
        return []

def pick_and_download_for_type(video_type, target_min_s, target_max_s, try_count=12):
    """
    Try searches across topics and sources until we produce a final video within range.
    Returns path to final file or None.
    """
    ensure_dirs()
    attempts = 0
    while attempts < try_count:
        attempts += 1
        topic = random.choice(TOPICS)
        print(f"[search] Attempt {attempts} — topic: {topic}")
        candidates = []
        # Pexels
        p = search_pexels(topic, per_page=15)
        for item in p:
            for f in item.get("files", []):
                url = f.get("link")
                if url:
                    candidates.append(("pexels", url))
        # Pixabay
        pb = search_pixabay(topic, per_page=20)
        for item in pb:
            vid = item.get("videos", {})
            # choose medium or large
            for key in ("medium","large","tiny"):
                if key in vid:
                    url = vid[key].get("url")
                    if url:
                        candidates.append(("pixabay", url))
                        break
        # Coverr
        cv = search_coverr()
        for item in cv:
            assets = item.get("assets", [])
            if assets:
                url = assets[0].get("url")
                if url:
                    candidates.append(("coverr", url))

        random.shuffle(candidates)
        if not candidates:
            print("No candidate clips found for this topic. Retrying...")
            time.sleep(2)
            continue

        # download and test clips until we can assemble target length
        downloaded = []
        for idx,(src,url) in enumerate(candidates[:8]):
            clip_path = CLIPS_DIR / f"{src}_{attempts}_{idx}.mp4"
            try:
                download_url_to(clip_path, url)
            except Exception as e:
                print(f"Download failed: {e}")
                continue
            dur = ffprobe_duration_seconds(clip_path)
            audio = has_audio(clip_path)
            print(f"Downloaded {clip_path} dur={dur}s audio={audio}")
            if dur <= 0:
                clip_path.unlink(missing_ok=True)
                continue
            # For shorts: prefer clip <= 60s or can trim to <=60
            if video_type == "shorts":
                # if at least 8s duration
                if dur >= 6:
                    downloaded.append((clip_path,dur,audio))
            else:
                # for long/very_long accept any clip >5s
                if dur >= 5:
                    downloaded.append((clip_path,dur,audio))
            if len(downloaded) >= 6:
                break

        # Build final based on type
        if video_type == "shorts":
            # pick first clip with audio, trim to between 30-60s
            for clip,dur,audio in downloaded:
                if audio:
                    target = min(int(dur), 45)
                    if target < 10:
                        target = min(30, int(dur))
                    outp = OUT_DIR / f"short_{int(time.time())}.mp4"
                    cmd = f'ffmpeg -y -i "{clip}" -t {target} -c copy "{outp}"'
                    try:
                        print("Running:", cmd)
                        sh(cmd)
                        if has_audio(outp):
                            return outp
                    except Exception as e:
                        print("Trim error:", e)
                        continue
            print("No downloaded short clip with audio found — retrying search.")
            # cleanup and retry
            for p in CLIPS_DIR.glob("*"):
                try: p.unlink()
                except: pass
            time.sleep(1)
            continue

        else:
            # long or very_long: concat trimmed segments to reach target_min_s
            # create trimmed versions (max 180s each to provide variety)
            list_txt = OUT_DIR / "list.txt"
            if list_txt.exists():
                list_txt.unlink()
            total = 0
            idx = 0
            for clip,dur,audio in downloaded:
                if not audio:
                    print(f"Skipping clip {clip} because no audio")
                    continue
                trim_t = min(dur, 180)  # trim each to max 3 min for variety
                out_trim = OUT_DIR / f"trim_{idx}.mp4"
                cmd = f'ffmpeg -y -i "{clip}" -t {int(trim_t)} -c copy "{out_trim}"'
                try:
                    sh(cmd)
                except Exception as e:
                    print("Trim failed:", e)
                    continue
                with open(list_txt, "a") as f:
                    f.write(f"file '{out_trim.resolve()}'\n")
                total += int(trim_t)
                idx += 1
                if total >= target_min_s:
                    break

            if total >= target_min_s:
                combined = OUT_DIR / f"combined_{int(time.time())}.mp4"
                cmd = f'ffmpeg -y -f concat -safe 0 -i "{list_txt}" -c copy "{combined}"'
                try:
                    sh(cmd)
                except Exception as e:
                    print("Concat failed:", e)
                    continue
                # if resulting too long, trim to target_max
                dur_comb = ffprobe_duration_seconds(combined)
                if dur_comb > target_max_s:
                    trimmed = OUT_DIR / f"final_trim_{int(time.time())}.mp4"
                    sh(f'ffmpeg -y -i "{combined}" -t {target_max_s} -c copy "{trimmed}"')
                    combined = trimmed
                # ensure audio; if no audio, try to add fallback bg
                if not has_audio(combined):
                    print("Final combined has no audio. Attaching fallback music.")
                    bg = OUT_DIR / "bg.mp3"
                    try:
                        download_url_to(bg, MIXKIT_FALLBACK)
                        final_with_audio = FINAL_FILE
                        sh(f'ffmpeg -y -i "{combined}" -stream_loop -1 -i "{bg}" -shortest -c:v copy -c:a aac -b:a 128k "{final_with_audio}"')
                        return final_with_audio
                    except Exception as e:
                        print("Failed to attach fallback audio:", e)
                        continue
                else:
                    # move to final path
                    final_with_audio = FINAL_FILE
                    try:
                        if FINAL_FILE.exists(): FINAL_FILE.unlink()
                        combined.rename(final_with_audio)
                    except Exception:
                        # fallback to copy
                        sh(f'ffmpeg -y -i "{combined}" -c copy "{final_with_audio}"')
                    return final_with_audio
            else:
                print(f"Total collected duration {total}s < target {target_min_s}s — retrying.")
                # cleanup and retry
                for p in CLIPS_DIR.glob("*"):
                    try: p.unlink()
                    except: pass
                for p in OUT_DIR.glob("*"):
                    try: p.unlink()
                    except: pass
                time.sleep(1)
                continue

    return None

def get_access_token():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise Exception("Missing Google OAuth credentials in env.")
    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
    if r.status_code != 200:
        print("Token request failed:", r.status_code, r.text)
        raise Exception("Failed to get access token.")
    j = r.json()
    return j.get("access_token")

def upload_to_youtube(file_path, title, description, tags, privacy="public", categoryId="22"):
    print(f"[upload] Uploading {file_path} to YouTube as {title}")
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
    # Start resumable upload session
    resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers=headers, data=json.dumps(metadata), timeout=30
    )
    if resp.status_code not in (200,201,201):
        print("Failed to create upload session:", resp.status_code, resp.text)
        raise Exception("Upload session creation failed.")
    upload_url = resp.headers.get("Location")
    if not upload_url:
        # sometimes the response is 200 with body containing upload URL? check headers
        print("No Location header in session response. Response headers:", resp.headers)
        raise Exception("No upload URL returned.")
    # Upload the file
    with open(file_path, "rb") as f:
        upload_resp = requests.put(upload_url, data=f, headers={"Content-Type":"application/octet-stream"}, timeout=600)
    if upload_resp.status_code not in (200,201):
        print("Upload failed:", upload_resp.status_code, upload_resp.text)
        raise Exception("Upload failed.")
    # parse returned JSON for id
    try:
        resj = upload_resp.json()
        video_id = resj.get("id")
    except Exception:
        video_id = None
    if not video_id:
        # try to extract from body if plain text
        print("Upload response:", upload_resp.text[:400])
    print("Uploaded. Video ID:", video_id)
    return video_id

def choose_title_desc(video_type, duration_seconds):
    minutes = max(1, int(duration_seconds // 60))
    if video_type == "shorts":
        tplt = random.choice(TITLE_TEMPLATES["shorts"])
        title = tplt.format(len=int(duration_seconds))
    elif video_type == "long":
        tplt = random.choice(TITLE_TEMPLATES["long"])
        title = tplt.format(len=minutes)
    else:
        tplt = random.choice(TITLE_TEMPLATES["very_long"])
        title = tplt.format(len=minutes)
    desc = DESCRIPTION_TEMPLATE + f"\n\nUploaded by {CHANNEL_NAME}.\nDuration: {minutes} minutes."
    tags = TAGS_BASE + [video_type, "relaxing", "ambient"]
    return title, desc, tags

def clean_workdir():
    for p in WORKDIR.glob("**/*"):
        # keep logs maybe
        pass

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
        min_d, max_d = 20, 60
    elif vtype == "long":
        min_d, max_d = 10*60, 50*60
    else:
        min_d, max_d = 60*60, 3*60*60  # 1h - 3h

    print(f"Start building type={vtype} target {min_d}s - {max_d}s")
    ensure_dirs()

    final = pick_and_download_for_type(vtype, min_d, max_d, try_count=20)
    if not final:
        print("Failed to produce final video — aborting.")
        sys.exit(1)

    dur = ffprobe_duration_seconds(final)
    print(f"Final file: {final} duration {dur}s audio={has_audio(final)}")

    # confirm audio again strictly
    if not has_audio(final):
        print("Final video has no audio — abort.")
        sys.exit(1)

    # build metadata
    title, desc, tags = choose_title_desc(vtype, dur)
    print("Title:", title)
    print("Uploading...")
    video_id = upload_to_youtube(final, title, desc, tags, privacy="public")
    if not video_id:
        print("Upload did not return id. Exiting.")
        sys.exit(1)

    youtube_url = f"https://youtu.be/{video_id}"
    print("Uploaded successfully:", youtube_url)
    # Optionally: write log to uploads.csv (commit back to repo if needed)
    logline = f"{time.strftime('%Y-%m-%d %H:%M:%S')},{video_id},{title}\n"
    with open("uploads_log.csv","a") as f:
        f.write(logline)

if __name__ == "__main__":
    main()
