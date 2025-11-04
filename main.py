#!/usr/bin/env python3
# main.py - Calm Loop uploader (fixed overlay + robust ffmpeg handling)
# Usage: python3 main.py --type shorts|long|very_long

import os, sys, time, random, re, subprocess, requests, json, math
from pathlib import Path

# ------------- CONFIG -------------
CHANNEL_HANDLE = "CalmLoop-l6p"
TOPICS = [
    "relaxing","rain","ocean","forest","waterfall","snow","clouds","desert night",
    "mountain","river","calm beach","winter cozy","campfire","underwater diving","birds",
    "sunset","sunrise","drone aerial","night stars","beach","misty forest"
]
TITLE_TEMPLATES = {
    "shorts": ["Instant Calm — {}", "{} Mini Escape", "{} Moment to Breathe", "Relax in seconds: {}"],
    "long": ["{} Ambience for Relaxation & Focus", "Soothing {} Sounds — Relax & Sleep", "Peaceful {} Ambience — Calm Your Mind"],
    "very_long": ["Extended {} Mix — Overnight Relaxation", "{} Soundscape — Sleep & Deep Rest"]
}
DESCRIPTION_TEMPLATE = "Calm Loop brings high-quality relaxing ambient sounds and nature visuals to help you relax, sleep, meditate, and focus."
TAGS_BASE = ["relaxing","nature","sleep","meditation","ambient","calm","relax","soothing","ASMR","english"]

# ------------- SECRETS / ENV -------------
def env_int(name, default):
    v = os.environ.get(name, "")
    try:
        return int(v) if v and v.strip() != "" else default
    except Exception:
        return default

PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY","")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY","")
COVERR_API_KEY = os.environ.get("COVERR_API_KEY","")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID","")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET","")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN","")
MIXKIT_BIRDS = os.environ.get("MIXKIT_BIRDS_URL","")

# ------------- DURATION & LIMITS -------------
SHORT_MAX_S = env_int("SHORT_MAX_S", 60)          # <= 60s -> Shorts
LONG_MIN_S = env_int("LONG_MIN_S", 120)           # >= 2 minutes
LONG_MAX_S = env_int("LONG_MAX_S", 1800)          # <= 30 minutes
VERY_LONG_MIN_S = env_int("VERY_LONG_MIN_S", 3600) # >= 1 hour
AUDIO_MIN_DB = float(os.environ.get("AUDIO_MIN_DB","-60.0"))
MAX_CANDIDATES = env_int("MAX_CANDIDATES", 18)
TRY_COUNT = env_int("TRY_COUNT", 10)

# ------------- PATHS -------------
ROOT = Path(".").resolve()
WORK = ROOT / "work"
CLIPS = WORK / "clips"
OUT = WORK / "out"
ASSETS = ROOT / "assets"
FALLBACK_LOCAL = ASSETS / "fallback_audio.mp3"
QUOTA_FLAG = WORK / "quota_exceeded.flag"
UPLOAD_LOG = ROOT / "uploads_log.csv"

REQ_HEADERS = {"User-Agent":"Mozilla/5.0"}
REQ_TIMEOUT = 45

# ------------- UTIL -------------
def sh(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
    return subprocess.check_call(cmd, shell=True)

def ensure_dirs():
    WORK.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

def ffprobe_duration(p):
    try:
        out = sh(f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{p}"', capture=True).strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0

def has_audio_stream(p):
    try:
        out = sh(f'ffprobe -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 "{p}"', capture=True)
        return bool(out.strip())
    except Exception:
        return False

def audio_mean_db(p):
    try:
        out = sh(f'ffmpeg -hide_banner -nostats -i "{p}" -af volumedetect -f null /dev/null', capture=True)
        m = re.search(r'mean_volume:\s*([-0-9\.]+)\s*dB', out)
        return float(m.group(1)) if m else None
    except Exception:
        return None

def audio_ok(p, min_db=AUDIO_MIN_DB):
    if not Path(p).exists(): return False
    if not has_audio_stream(p): return False
    mv = audio_mean_db(p)
    if mv is None: return False
    return mv > min_db

def download_url(path, url, headers=None, timeout=REQ_TIMEOUT):
    headers = headers or REQ_HEADERS
    print(f"[DL] {url} -> {path}")
    r = requests.get(url, headers=headers, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return path

# ------------- SEARCH (Pexels, Pixabay, Coverr, Archive) -------------
def search_pexels(q, per_page=8):
    if not PEXELS_API_KEY: return []
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={**REQ_HEADERS,"Authorization":PEXELS_API_KEY}, params={"query":q,"per_page":per_page}, timeout=REQ_TIMEOUT)
        if r.status_code != 200: return []
        data = r.json()
        out=[]
        for v in data.get("videos",[]):
            files = v.get("video_files",[])
            if files:
                best = sorted(files, key=lambda x:(int(x.get("width",0)), int(x.get("height",0))), reverse=True)[0]
                if best.get("link"): out.append(best.get("link"))
        return out
    except Exception as e:
        print("pexels error", e); return []

def search_pixabay(q, per_page=8):
    if not PIXABAY_API_KEY: return []
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key":PIXABAY_API_KEY,"q":q,"per_page":per_page}, headers=REQ_HEADERS, timeout=REQ_TIMEOUT)
        if r.status_code != 200: return []
        data = r.json()
        out=[]
        for h in data.get("hits",[]):
            vids = h.get("videos",{})
            for size in ("large","medium","small"):
                if size in vids and vids[size].get("url"):
                    out.append(vids[size]["url"]); break
        return out
    except Exception as e:
        print("pixabay error", e); return []

def search_coverr(q=None):
    if not COVERR_API_KEY: return []
    try:
        r = requests.get("https://api.coverr.co/videos", headers={**REQ_HEADERS, "Authorization":f"Bearer {COVERR_API_KEY}"}, timeout=REQ_TIMEOUT)
        if r.status_code != 200: return []
        data = r.json()
        out=[]
        for d in data.get("data",[]):
            assets = d.get("assets",[])
            if assets and assets[0].get("url"): out.append(assets[0].get("url"))
        return out
    except Exception as e:
        print("coverr error", e); return []

def search_archive(q, rows=6):
    try:
        qenc = requests.utils.quote(f'("{q}" OR {" ".join(q.split())})')
        url = f"https://archive.org/advancedsearch.php?q={qenc}&fl[]=identifier&rows={rows}&output=json"
        r = requests.get(url, headers=REQ_HEADERS, timeout=REQ_TIMEOUT)
        if r.status_code != 200: return []
        ids = [d.get("identifier") for d in r.json().get("response",{}).get("docs",[])]
        out=[]
        for idv in ids:
            m = requests.get(f"https://archive.org/metadata/{idv}", headers=REQ_HEADERS, timeout=REQ_TIMEOUT)
            if m.status_code != 200: continue
            meta = m.json()
            for f in meta.get("files",[]):
                name = f.get("name","")
                if name.endswith(".mp4") or name.endswith(".m4v"):
                    out.append(f"https://archive.org/download/{idv}/{name}")
            if len(out) >= rows: break
        return out
    except Exception as e:
        print("archive error", e); return []

def gather_candidates(topic):
    urls = []
    urls += search_pexels(topic, per_page=6)
    urls += search_pixabay(topic, per_page=6)
    urls += search_coverr(topic)
    urls += search_archive(topic, rows=6)
    random.shuffle(urls)
    return urls[:MAX_CANDIDATES]

# ------------- VIDEO PROCESSING -------------
def normalize_reencode(inp, outp):
    try:
        sh(f'ffmpeg -y -i "{inp}" -c:v libx264 -preset veryfast -crf 22 -c:a aac -b:a 192k -movflags +faststart "{outp}"')
        return True
    except Exception as e:
        print("normalize_reencode error", e); return False

def make_vertical(inp, outp):
    try:
        sh(f'ffmpeg -y -i "{inp}" -vf "scale=1080:-2, pad=1080:1920:(ow-iw)/2:(oh-ih)/2" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k -movflags +faststart "{outp}"')
        return True
    except Exception as e:
        print("make_vertical error", e); return False

def concat_and_reencode(list_txt, outp):
    try:
        tmp = OUT / "tmp_concat.mp4"
        sh(f'ffmpeg -y -f concat -safe 0 -i "{list_txt}" -c copy "{tmp}"')
        return normalize_reencode(tmp, outp)
    except Exception as e:
        print("concat_and_reencode error", e); return False

def loop_to_target(src, seconds, outp):
    try:
        sh(f'ffmpeg -y -stream_loop -1 -i "{src}" -t {int(seconds)} -c:v libx264 -preset veryfast -crf 22 -c:a aac -b:a 192k -movflags +faststart "{outp}"')
        return True
    except Exception as e:
        print("loop_to_target error", e); return False

def overlay_fallback_audio(video_in, video_out):
    """
    If video_in has audio -> mix with bg audio (soft bg).
    If video_in has NO audio -> map bg audio as sole audio track.
    Try local fallback first, then remote urls.
    """
    candidates = []
    if FALLBACK_LOCAL.exists():
        candidates.append(str(FALLBACK_LOCAL))
    if MIXKIT_BIRDS:
        candidates.append(MIXKIT_BIRDS)
    candidates += [
        "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
    ]
    for audio_src in candidates:
        try:
            if Path(audio_src).exists():
                audio_file = audio_src
            else:
                audio_file = OUT / "tmp_bg.mp3"
                download_url(audio_file, audio_src)
            # If original has audio -> amix
            if has_audio_stream(video_in):
                cmd = (
                    f'ffmpeg -y -i "{video_in}" -i "{audio_file}" '
                    f'-filter_complex "[0:a]volume=1[a0];[1:a]volume=0.14[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]" '
                    f'-map 0:v -map "[aout]" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k -movflags +faststart "{video_out}"'
                )
            else:
                # No audio in original -> use bg audio as the audio track
                cmd = (
                    f'ffmpeg -y -i "{video_in}" -i "{audio_file}" -map 0:v -map 1:a '
                    f'-c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k -shortest -movflags +faststart "{video_out}"'
                )
            sh(cmd)
            if Path(video_out).exists():
                # quick audio check
                if audio_ok(video_out):
                    return True
                else:
                    print("overlay produced file but audio_ok failed, trying next candidate")
            continue
        except Exception as e:
            print("overlay_fallback_audio try failed for", audio_src, e)
            continue
    return False

def has_long_silence(path, silence_db=-50, max_s=2.0):
    try:
        out = sh(f'ffmpeg -hide_banner -nostats -i "{path}" -af "silencedetect=noise={silence_db}dB:d={max_s}" -f null -', capture=True)
        return "silence_start" in out or "silence_end" in out
    except Exception:
        return True

# ------------- YOUTUBE UPLOAD -------------
def get_access_token():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise Exception("Missing Google OAuth secrets.")
    data = {"client_id":GOOGLE_CLIENT_ID,"client_secret":GOOGLE_CLIENT_SECRET,"refresh_token":YT_REFRESH_TOKEN,"grant_type":"refresh_token"}
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
    if r.status_code != 200:
        print("token error", r.status_code, r.text); raise Exception("token")
    return r.json().get("access_token")

def upload_to_youtube(file_path, title, description, tags, privacy="public", max_attempts=3):
    for attempt in range(1, max_attempts+1):
        try:
            token = get_access_token()
            meta = {"snippet":{"title":title,"description":description,"tags":tags,"categoryId":"22"},"status":{"privacyStatus":privacy}}
            headers = {"Authorization":f"Bearer {token}", "Content-Type":"application/json; charset=UTF-8"}
            resp = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                                 headers=headers, data=json.dumps(meta), timeout=30, allow_redirects=False)
            if resp.status_code == 403:
                try:
                    j = resp.json()
                    reason = j.get("error",{}).get("errors",[{}])[0].get("reason","")
                    print("Create session failed:", j)
                    if "quotaExceeded" in reason:
                        QUOTA_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " quotaExceeded\n")
                        raise Exception("quotaExceeded")
                except Exception:
                    pass
                raise Exception("create_session_failed")
            upload_url = resp.headers.get("Location") or resp.headers.get("location")
            if not upload_url:
                print("no upload url", resp.status_code, resp.text); raise Exception("no_url")
            with open(file_path,"rb") as f:
                up = requests.put(upload_url, data=f, headers={"Content-Type":"application/octet-stream"}, timeout=3600)
            if up.status_code not in (200,201):
                print("upload status", up.status_code, up.text)
                if up.status_code == 403 and "quotaExceeded" in (up.text or ""):
                    QUOTA_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " quotaExceeded\n")
                    raise Exception("quotaExceeded")
                raise Exception("upload_failed")
            try:
                return up.json().get("id")
            except Exception:
                return None
        except Exception as e:
            print(f"[upload attempt {attempt}] error:", e)
            if "quotaExceeded" in str(e):
                print("HALT: quotaExceeded detected.")
                raise
            time.sleep(2 * attempt)
            continue
    raise Exception("upload_failed_all")

# ------------- METADATA -------------
def choose_title_desc(vtype, dur_seconds, topic):
    topic_clean = topic.title() if topic else "Relaxing"
    emoji_map={"Rain":"🌧️","Ocean":"🌊","Forest":"🌿","Waterfall":"💧","Snow":"❄️","Clouds":"☁️","Underwater Diving":"🤿","Birds":"🐦"}
    emoji = emoji_map.get(topic_clean, "🌿")
    if vtype == "shorts":
        template = random.choice(TITLE_TEMPLATES["shorts"])
        title = f"{emoji} {template.format(topic_clean)}"
    elif vtype == "long":
        title = f"{emoji} {random.choice(TITLE_TEMPLATES['long']).format(topic_clean)}"
    else:
        title = f"{emoji} {random.choice(TITLE_TEMPLATES['very_long']).format(topic_clean)}"
    hashtags = "#relaxing #nature #sleep #meditation #calm"
    minutes = max(1, int(math.ceil(dur_seconds / 60.0)))
    desc = "\n".join([
        DESCRIPTION_TEMPLATE,
        "",
        f"🔔 Subscribe: https://www.youtube.com/@{CHANNEL_HANDLE}",
        "👍 Like & Share if this helped you relax.",
        f"⏱ Approx duration: {minutes} minute(s).",
        "",
        hashtags
    ])
    tags = TAGS_BASE + [topic.lower() if topic else "relaxing"]
    if vtype == "shorts": tags.append("shorts")
    return title, desc, list(dict.fromkeys(tags))[:20]

# ------------- BUILD FLOW -------------
def pick_and_build(vtype, min_s, max_s):
    ensure_dirs()
    tries = 0
    while tries < TRY_COUNT:
        tries += 1
        topic = random.choice(TOPICS)
        print(f"[search] Attempt {tries} — topic: {topic}")
        cand_urls = gather_candidates(topic)
        if not cand_urls:
            print("No candidates found; retry")
            time.sleep(1)
            continue

        # download up to 8
        downloaded = []
        for i, url in enumerate(cand_urls[:8]):
            try:
                p = CLIPS / f"clip_{int(time.time())}_{i}.mp4"
                download_url(p, url)
                dur = ffprobe_duration(p)
                aud = has_audio_stream(p)
                mv = audio_mean_db(p) if aud else None
                print("Downloaded", p, "dur=", dur, "audio=", aud, "mv=", mv)
                if dur > 0:
                    downloaded.append((p, dur, aud, mv))
            except Exception as e:
                print("download failed", e)
                continue

        if not downloaded:
            print("No downloaded clips — retry")
            time.sleep(1)
            continue

        # SHORTS
        if vtype == "shorts":
            for p,dur,aud,mv in sorted(downloaded, key=lambda x: -x[1]):
                if dur < 4 or dur > SHORT_MAX_S: continue
                candidate = OUT / f"short_candidate_{int(time.time())}.mp4"
                ok_vert = make_vertical(str(p), str(candidate))
                if not ok_vert:
                    try:
                        sh(f'ffmpeg -y -i "{p}" -t {int(min(dur, SHORT_MAX_S))} -c copy "{candidate}"')
                    except Exception:
                        continue
                # ensure file exists
                if not candidate.exists():
                    continue
                # if audio ok -> proceed
                final_with_audio = OUT / f"short_audio_{int(time.time())}.mp4"
                if audio_ok(candidate):
                    # we can normalize
                    reencoded = OUT / f"short_re_{int(time.time())}.mp4"
                    if normalize_reencode(candidate, reencoded):
                        return reencoded, topic
                    else:
                        continue
                else:
                    # overlay fallback audio
                    if overlay_fallback_audio(str(candidate), str(final_with_audio)):
                        if audio_ok(final_with_audio):
                            reencoded = OUT / f"short_re_{int(time.time())}.mp4"
                            if normalize_reencode(final_with_audio, reencoded):
                                return reencoded, topic
                            else:
                                continue
                        else:
                            print("Overlay produced file but audio not OK; try next candidate")
                            continue
                    else:
                        print("Overlay failed for this candidate; try next")
                        continue
            print("No suitable short found — retry")
            time.sleep(1)
            continue

        # LONG / VERY_LONG
        candidates_audio = [t for t in downloaded if t[2] and (t[3] is None or t[3] > AUDIO_MIN_DB)]
        # try single clip
        for p,dur,aud,mv in sorted(candidates_audio, key=lambda x: -x[1]):
            if dur >= min_s and dur <= max_s and not has_long_silence(p):
                final = OUT / f"long_single_{int(time.time())}.mp4"
                if normalize_reencode(p, final) and audio_ok(final):
                    return final, topic
        # concat until min_s
        listfile = OUT / "list.txt"
        if listfile.exists(): listfile.unlink()
        total = 0; idx = 0
        for p,dur,aud,mv in candidates_audio:
            trim = int(min(dur, 300))
            outtrim = OUT / f"trim_{int(time.time())}_{idx}.mp4"
            try:
                sh(f'ffmpeg -y -i "{p}" -t {trim} -c copy "{outtrim}"')
                with open(listfile, "a") as f:
                    f.write(f"file '{outtrim.resolve()}'\n")
                total += trim; idx += 1
            except Exception as e:
                print("trim failed", e); continue
            if total >= min_s: break
        if total >= min_s:
            combined = OUT / f"combined_{int(time.time())}.mp4"
            if concat_and_reencode(listfile, combined):
                if audio_ok(combined):
                    return combined, topic
                if overlay_fallback_audio(str(combined), str(OUT / f"withbg_{int(time.time())}.mp4")):
                    candf = OUT / f"withbg_{int(time.time())}.mp4"
                    if audio_ok(candf):
                        return candf, topic

        # fallback: loop first audio clip
        if candidates_audio:
            first = candidates_audio[0][0]
            outloop = OUT / f"loop_{int(time.time())}.mp4"
            if loop_to_target(first, min_s, outloop) and audio_ok(outloop):
                return outloop, topic

        print("Build attempt failed — retry")
        time.sleep(1)

    return None, None

# ------------- MAIN -------------
def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--type":
        print("Usage: python3 main.py --type shorts|long|very_long"); sys.exit(1)
    vtype = sys.argv[2]
    if QUOTA_FLAG.exists():
        print("Quota flag present. Stop."); sys.exit(1)
    if vtype == "shorts":
        min_s, max_s = 3, SHORT_MAX_S
    elif vtype == "long":
        min_s, max_s = LONG_MIN_S, LONG_MAX_S
    elif vtype == "very_long":
        min_s, max_s = VERY_LONG_MIN_S, 3*3600
    else:
        print("Unknown type"); sys.exit(1)

    ensure_dirs()
    tries = 0
    while tries < TRY_COUNT:
        tries += 1
        print(f"[main] Attempt {tries}/{TRY_COUNT} for type={vtype}")
        final_file, topic = pick_and_build(vtype, min_s, max_s)
        if not final_file:
            print("No final produced; retry"); continue
        safe = OUT / f"final_safe_{int(time.time())}.mp4"
        if not normalize_reencode(final_file, safe):
            print("Final reencode failed; retry"); continue
        dur = ffprobe_duration(safe)
        if not audio_ok(safe):
            print("Final audio not OK, try overlay fallback")
            if not overlay_fallback_audio(str(safe), str(OUT / f"final_with_bg_{int(time.time())}.mp4")):
                print("Overlay fallback failed; retry"); continue
            safe = OUT / f"final_with_bg_{int(time.time())}.mp4"
            if not audio_ok(safe): print("Audio still bad; retry"); continue
        title, desc, tags = choose_title_desc(vtype, dur, topic or "relaxing")
        print("Uploading:", safe, "title:", title)
        try:
            vid = upload_to_youtube(str(safe), title, desc, tags, privacy="public", max_attempts=3)
            url = f"https://youtu.be/{vid}" if vid else "no-id"
            print("[DONE] Uploaded:", url)
            with open(UPLOAD_LOG, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{vtype},{vid},{title}\n")
            return
        except Exception as e:
            print("Upload failed:", e)
            if "quotaExceeded" in str(e):
                print("QuotaExceeded set; aborting"); QUOTA_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " quotaExceeded\n"); raise
            continue
    print("Reached max tries — abort."); sys.exit(1)

if __name__ == "__main__":
    main()
