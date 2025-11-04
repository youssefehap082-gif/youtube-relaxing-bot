#!/usr/bin/env python3
# main.py - Calm Loop uploader (updated fixes: UA headers, quota handling, Mixkit fallback, faster retries)
import os, sys, time, random, re, subprocess, requests, json, math
from pathlib import Path

# ---------------- CONFIG ----------------
CHANNEL_HANDLE = "CalmLoop-l6p"
TOPICS = ["relaxing","rain","ocean","forest","waterfall","snow","clouds","desert night","mountain","river","calm beach","winter cozy","campfire","underwater diving","birds","sunset","sunrise","drone aerial","night stars"]
TITLE_TEMPLATES = {
    "shorts": ["Instant Calm — {}", "Quick Relaxation: {}", "{} Mini Escape", "{} Moment to Breathe"],
    "long": ["{} Ambience for Relaxation & Focus", "Soothing {} Sounds — Relax & Sleep", "Peaceful {} Ambience — Calm Your Mind"],
    "very_long": ["Extended {} Mix — Overnight Relaxation", "{} Soundscape — Sleep & Deep Rest"]
}
DESCRIPTION_TEMPLATE = "Calm Loop brings high-quality relaxing ambient sounds and nature visuals to help you relax, sleep, meditate, and focus."
TAGS_BASE = ["relaxing","nature","sleep","meditation","ambient","calm","relax","soothing","ASMR","english"]

# secrets / env
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY") or ""
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY") or ""
COVERR_API_KEY = os.environ.get("COVERR_API_KEY") or ""
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID") or ""
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET") or ""
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN") or ""
MIXKIT_BIRDS = os.environ.get("MIXKIT_BIRDS_URL") or ""

# thresholds & limits (tweakable)
SHORT_THRESHOLD_SECONDS = int(os.environ.get("SHORT_THRESHOLD_SECONDS","120"))   # <= this is treated as shorts
SHORT_MAX_S = int(os.environ.get("SHORT_MAX_S","120"))
LONG_MIN_S = int(os.environ.get("LONG_MIN_S","120"))
LONG_MAX_S = int(os.environ.get("LONG_MAX_S","1800"))   # 30 minutes
VERY_LONG_MIN_S = int(os.environ.get("VERY_LONG_MIN_S","3600"))  # 1 hour
AUDIO_MIN_DB = float(os.environ.get("AUDIO_MIN_DB","-60.0"))
MAX_CANDIDATES = int(os.environ.get("MAX_CANDIDATES","18"))
TRY_COUNT = int(os.environ.get("TRY_COUNT","10"))

# paths
WORK = Path("work")
CLIPS = WORK/"clips"
OUT = WORK/"out"
FINAL = OUT/"final_video.mp4"
UPLOAD_FLAG = Path("quota_exceeded.flag")

# request headers & timeouts
REQ_HEADERS = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
REQ_TIMEOUT = 45

# helpers
def sh(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
    return subprocess.check_call(cmd, shell=True)

def ensure_dirs():
    WORK.mkdir(exist_ok=True)
    CLIPS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

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
    if not has_audio_stream(p): return False
    mv = audio_mean_db(p)
    if mv is None: return False
    return mv > min_db

def download_url(path,url, headers=None, timeout=REQ_TIMEOUT):
    headers = headers or REQ_HEADERS
    print(f"DL -> {url}")
    r = requests.get(url, headers=headers, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(path,"wb") as f:
        for chunk in r.iter_content(8192):
            if chunk: f.write(chunk)
    return path

# searchers (simple robust)
def search_pexels(q, per_page=10):
    if not PEXELS_API_KEY: return []
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={**REQ_HEADERS,"Authorization":PEXELS_API_KEY}, params={"query":q,"per_page":per_page}, timeout=REQ_TIMEOUT)
        if r.status_code!=200: return []
        data=r.json()
        out=[]
        for v in data.get("videos",[]):
            files=v.get("video_files",[])
            if files:
                best=sorted(files, key=lambda x:(int(x.get("width",0)), int(x.get("height",0))), reverse=True)[0]
                if best.get("link"): out.append(best["link"])
        return out
    except Exception as e:
        print("pexels err",e); return []

def search_pixabay(q, per_page=10):
    if not PIXABAY_API_KEY: return []
    try:
        r = requests.get("https://pixabay.com/api/videos/", params={"key":PIXABAY_API_KEY,"q":q,"per_page":per_page}, headers=REQ_HEADERS, timeout=REQ_TIMEOUT)
        if r.status_code!=200: return []
        data=r.json()
        out=[]
        for h in data.get("hits",[]):
            vids=h.get("videos",{})
            for k in ("large","medium","tiny"):
                if k in vids and vids[k].get("url"):
                    out.append(vids[k]["url"]); break
        return out
    except Exception as e:
        print("pixabay err",e); return []

def search_coverr(q=None):
    if not COVERR_API_KEY: return []
    try:
        r = requests.get("https://api.coverr.co/videos", headers={**REQ_HEADERS, "Authorization":f"Bearer {COVERR_API_KEY}"}, timeout=REQ_TIMEOUT)
        if r.status_code!=200: return []
        data=r.json()
        out=[]
        for d in data.get("data",[]): 
            assets=d.get("assets",[])
            if assets and assets[0].get("url"): out.append(assets[0]["url"])
        return out
    except Exception as e:
        print("coverr err",e); return []

def search_internet_archive(q, rows=8):
    try:
        qenc = requests.utils.quote(f'("video" OR mediatype:movies) AND ({q})')
        url=f"https://archive.org/advancedsearch.php?q={qenc}&fl[]=identifier&rows={rows}&output=json"
        r=requests.get(url, headers=REQ_HEADERS, timeout=REQ_TIMEOUT)
        if r.status_code!=200: return []
        ids=[d.get("identifier") for d in r.json().get("response",{}).get("docs",[])]
        out=[]
        for idv in ids:
            m=requests.get(f"https://archive.org/metadata/{idv}", headers=REQ_HEADERS, timeout=REQ_TIMEOUT)
            if m.status_code!=200: continue
            meta=m.json()
            for f in meta.get("files",[]):
                name=f.get("name","")
                if name.endswith(".mp4") or name.endswith(".m4v"):
                    out.append(f"https://archive.org/download/{idv}/{name}")
            if len(out)>=rows: break
        return out
    except Exception as e:
        print("ia err",e); return []

# assemble candidate list
def gather_candidates(topic):
    c=[]
    c+=search_pexels(topic, per_page=6)
    c+=search_pixabay(topic, per_page=6)
    c+=search_coverr(topic)
    c+=search_internet_archive(topic, rows=6)
    random.shuffle(c)
    return c[:MAX_CANDIDATES]

# build video (short/long/very_long)
def pick_and_build(vtype, min_s, max_s):
    ensure_dirs()
    attempts=0
    while attempts<TRY_COUNT:
        attempts+=1
        topic=random.choice(TOPICS)
        print(f"[search] attempt {attempts} topic={topic}")
        cand=gather_candidates(topic)
        if not cand:
            print("no candidates; retry"); time.sleep(2); continue

        downloaded=[]
        for i,url in enumerate(cand):
            p = CLIPS/f"clip_{attempts}_{i}.mp4"
            try:
                download_url(p, url)
            except Exception as e:
                print("dl failed",e); continue
            dur=ffprobe_duration(p)
            aud=has_audio_stream(p)
            mv=audio_mean_db(p) if aud else None
            print("D:",p,"dur=",dur,"audio=",aud,"mv=",mv)
            if dur<=0:
                p.unlink(missing_ok=True); continue
            downloaded.append((p,dur,aud,mv))
            if len(downloaded)>=8: break

        if not downloaded:
            print("no good downloads; retry"); time.sleep(1); continue

        # SHORTS handling: pick a single clip with good audio <= SHORT_MAX_S
        if vtype=="shorts":
            for p,dur,aud,mv in sorted(downloaded, key=lambda x:-x[1]):
                if not aud or mv is None or mv<=AUDIO_MIN_DB: continue
                target = min(int(dur), SHORT_MAX_S)
                if target < 6: continue
                tmp = OUT/f"short_trim_{int(time.time())}.mp4"
                try:
                    sh(f'ffmpeg -y -i "{p}" -t {target} -c copy "{tmp}"')
                except Exception:
                    try: sh(f'ffmpeg -y -i "{p}" -ss 0 -t {target} -c copy "{tmp}"')
                    except Exception as e: print("trim short err",e); continue
                final_tmp = OUT/f"short_re_{int(time.time())}.mp4"
                if not normalize_reencode(tmp, final_tmp): tmp.unlink(missing_ok=True); continue
                if not audio_ok(final_tmp): final_tmp.unlink(missing_ok=True); continue
                # vertical if <= threshold
                if ffprobe_duration(final_tmp) <= SHORT_THRESHOLD_SECONDS:
                    vert = OUT/f"short_vert_{int(time.time())}.mp4"
                    if make_vertical(final_tmp, vert): return vert, topic
                    return final_tmp, topic
            print("no suitable short; retry"); continue

        # LONG and VERY_LONG: prefer single long clip with audio, else concat/loop
        candidates_audio = [t for t in downloaded if t[2] and (t[3] is None or t[3] > AUDIO_MIN_DB)]
        # try single long clip
        for p,dur,aud,mv in sorted(candidates_audio, key=lambda x:-x[1]):
            if dur >= min_s and dur <= max_s and not has_long_silence(p):
                final_re = OUT/f"long_single_{int(time.time())}.mp4"
                if normalize_reencode(p, final_re) and audio_ok(final_re):
                    return final_re, topic
        # else concat small clips until min_s
        listf = OUT/"list.txt"
        if listf.exists(): listf.unlink()
        total=0; idx=0
        for p,dur,aud,mv in candidates_audio:
            trim = min(int(dur), 180)
            outtrim = OUT/f"trim_{int(time.time())}_{idx}.mp4"
            try: sh(f'ffmpeg -y -i "{p}" -t {trim} -c copy "{outtrim}"')
            except Exception as e: print("trim err",e); continue
            with open(listf,"a") as f: f.write(f"file '{outtrim.resolve()}'\n")
            total+=trim; idx+=1
            if total>=min_s: break
        if total>=min_s:
            combined = OUT/f"combined_{int(time.time())}.mp4"
            if concat_and_reencode(listf, combined):
                if audio_ok(combined): return combined, topic
                # try overlay birds or fallback audio from one clip
                if overlay_birds(combined, OUT/"with_birds.mp4"): 
                    if audio_ok(OUT/"with_birds.mp4"): return OUT/"with_birds.mp4", topic
                # try extract audio from one downloaded clip and merge
                for p,dur,aud,mv in candidates_audio:
                    try:
                        bg = OUT/"bg_from_clip.mp3"
                        sh(f'ffmpeg -y -i "{p}" -vn -ar 44100 -ac 2 -b:a 128k "{bg}"')
                        sh(f'ffmpeg -y -i "{combined}" -i "{bg}" -shortest -c:v copy -c:a aac -b:a 128k -movflags +faststart "{OUT}/final_with_bg.mp4"')
                        if audio_ok(OUT/"final_with_bg.mp4"): return OUT/"final_with_bg.mp4", topic
                    except Exception: continue
            print("concat failed; retry")
        # fallback: loop first good clip to min_s
        if candidates_audio:
            first = candidates_audio[0][0]
            loopout = OUT/f"loop_{int(time.time())}.mp4"
            if loop_to_target(first, min_s, loopout) and audio_ok(loopout):
                return loopout, topic
        # nothing acceptable => retry
        time.sleep(1)
    return None, None

# utility ffmpeg functions used above
def normalize_reencode(inp, outp):
    try:
        sh(f'ffmpeg -y -i "{inp}" -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart "{outp}"')
        return True
    except Exception as e:
        print("reencode err",e); return False

def make_vertical(inp,outp):
    try:
        sh(f'ffmpeg -y -i "{inp}" -vf "scale=1080:-2, pad=1080:1920:(ow-iw)/2:(oh-ih)/2" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k -movflags +faststart "{outp}"')
        return True
    except Exception as e:
        print("vertical err",e); return False

def concat_and_reencode(list_txt, outp):
    try:
        tmp = OUT/"tmp_concat.mp4"
        sh(f'ffmpeg -y -f concat -safe 0 -i "{list_txt}" -c copy "{tmp}"')
        return normalize_reencode(tmp, outp)
    except Exception as e:
        print("concat err",e); return False

def loop_to_target(src, seconds, outp):
    try:
        sh(f'ffmpeg -y -stream_loop -1 -i "{src}" -t {int(seconds)} -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k -movflags +faststart "{outp}"')
        return True
    except Exception as e:
        print("loop err",e); return False

def has_long_silence(path, silence_db=-50, max_s=1.5):
    try:
        out = sh(f'ffmpeg -hide_banner -nostats -i "{path}" -af "silencedetect=noise={silence_db}dB:d={max_s}" -f null -', capture=True)
        return "silence_start" in out or "silence_end" in out
    except Exception:
        return True

# overlay_birds: try MIXKIT_BIRDS then fallback list
FALLBACK_AUDIOS = [MIXKIT_BIRDS, "https://archive.org/download/ambient-sounds/ambient01.mp3", "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"]
def overlay_birds(video_in, video_out):
    for url in FALLBACK_AUDIOS:
        if not url: continue
        try:
            tmp = OUT/"tmp_audio.mp3"
            download_url(tmp, url)
            # mix audio softly under existing audio or add if missing
            sh(f'ffmpeg -y -i "{video_in}" -stream_loop -1 -i "{tmp}" -filter_complex "[1:a]volume=0.12[a1];[0:a][a1]amerge=inputs=2[aout]" -map 0:v -map "[aout]" -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k -movflags +faststart "{video_out}"')
            return True
        except Exception as e:
            print("overlay try failed for", url, e)
            continue
    return False

# ----------------- YouTube upload ----------------
def get_access_token():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise Exception("Missing Google OAuth secrets.")
    data={"client_id":GOOGLE_CLIENT_ID,"client_secret":GOOGLE_CLIENT_SECRET,"refresh_token":YT_REFRESH_TOKEN,"grant_type":"refresh_token"}
    r = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=20)
    if r.status_code!=200:
        print("token err", r.status_code, r.text); raise Exception("token")
    return r.json().get("access_token")

def upload_to_youtube(file_path, title, description, tags, privacy="public", max_attempts=3):
    for attempt in range(1, max_attempts+1):
        try:
            token = get_access_token()
            metadata={"snippet":{"title":title,"description":description,"tags":tags,"categoryId":"22"},"status":{"privacyStatus":privacy}}
            headers={**REQ_HEADERS, "Authorization":f"Bearer {token}", "Content-Type":"application/json; charset=UTF-8"}
            resp = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", headers=headers, data=json.dumps(metadata), timeout=30)
            if resp.status_code==403:
                j=resp.json()
                reason = j.get("error",{}).get("errors",[{}])[0].get("reason","")
                print("Create session failed:", resp.status_code, j)
                if "quotaExceeded" in reason:
                    UPLOAD_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " quotaExceeded\n")
                    raise Exception("quotaExceeded")
                raise Exception("create_session_failed")
            upload_url = resp.headers.get("Location") or resp.headers.get("location")
            if not upload_url:
                print("no upload url", resp.status_code, resp.text); raise Exception("no_url")
            # upload file
            with open(file_path,"rb") as f:
                up = requests.put(upload_url, data=f, headers={"Content-Type":"application/octet-stream"}, timeout=3600)
            if up.status_code not in (200,201):
                print("upload status", up.status_code, up.text)
                if up.status_code==403 and "quotaExceeded" in up.text:
                    UPLOAD_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + " quotaExceeded\n")
                    raise Exception("quotaExceeded")
                raise Exception("upload_failed")
            try:
                resj = up.json()
                return resj.get("id")
            except Exception:
                return None
        except Exception as e:
            print(f"[upload] attempt {attempt} err",e)
            if "quotaExceeded" in str(e):
                print("Quota exceeded — stop further uploads. Check Google Cloud Console.")
                raise
            time.sleep(3*attempt)
            continue
    raise Exception("upload_failed_all")

# title/desc chooser
def choose_title_desc(vtype,dur,topic):
    topic_clean = topic.title()
    emoji_map={"Rain":"🌧️","Ocean":"🌊","Forest":"🌿","Waterfall":"💧","Snow":"❄️","Clouds":"☁️","Desert Night":"🏜️","Mountain":"🏔️","River":"🏞️","Calm Beach":"🏝️","Underwater Diving":"🤿","Birds":"🐦","Sunset":"🌇","Sunrise":"🌅"} 
    emoji = emoji_map.get(topic_clean, "🌿")
    if vtype=="shorts":
        template = random.choice(TITLE_TEMPLATES["shorts"])
        title = f"{emoji} {template.format(topic_clean)}"
    elif vtype=="long":
        title = f"{emoji} {random.choice(TITLE_TEMPLATES['long']).format(topic_clean)}"
    else:
        title = f"{emoji} {random.choice(TITLE_TEMPLATES['very_long']).format(topic_clean)}"
    hot = ["#relaxing","#nature","#sleep","#meditation","#calm"]
    hashtags = " ".join(hot)
    minutes = max(1, int(math.ceil(dur/60.0)))
    desc = "\n".join([
        DESCRIPTION_TEMPLATE,
        "",
        f"🔔 Subscribe: https://www.youtube.com/@{CHANNEL_HANDLE}",
        "👍 Like & Share if this helped you relax.",
        f"⏱ Approx duration: {minutes} minute(s).",
        "",
        hashtags
    ])
    tags = TAGS_BASE + [topic.lower()]
    if vtype=="shorts": tags.append("shorts")
    return title, desc, list(dict.fromkeys(tags))[:20]

# -------- Main runner --------
def main():
    if len(sys.argv)<3 or sys.argv[1]!="--type":
        print("Usage: python main.py --type shorts|long|very_long"); sys.exit(1)
    vtype=sys.argv[2]
    if Path("quota_exceeded.flag").exists():
        print("Previous run flagged quotaExceeded. Stop until you clear or change schedule."); sys.exit(1)
    if vtype=="shorts":
        min_s,max_s=5,SHORT_MAX_S
    elif vtype=="long":
        min_s,max_s=LONG_MIN_S,LONG_MAX_S
    else:
        min_s,max_s=VERY_LONG_MIN_S,3*3600

    ensure_dirs()
    attempts=0
    while attempts<TRY_COUNT:
        attempts+=1
        print(f"[main] attempt {attempts}/{TRY_COUNT}")
        final, topic = pick_and_build(vtype, min_s, max_s)
        if not final:
            print("Producer failed — retry")
            continue
        # final safe reencode
        safe = OUT/f"final_safe_{int(time.time())}.mp4"
        if not normalize_reencode(final, safe):
            print("Reencode failed; retry"); continue
        dur = ffprobe_duration(safe)
        print("Produced", safe, "dur", dur, "audio_ok", audio_ok(safe))
        if not audio_ok(safe): 
            print("Audio not OK after final encode — retry")
            safe.unlink(missing_ok=True); continue
        title,desc,tags = choose_title_desc(vtype,dur,topic or "relaxing")
        try:
            vid = upload_to_youtube(safe, title, desc, tags, privacy="public", max_attempts=3)
            url = f"https://youtu.be/{vid}" if vid else "no-id"
            print("[DONE] uploaded:",url)
            with open("uploads_log.csv","a") as f: f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')},{vid},{title}\n")
            return
        except Exception as e:
            if "quotaExceeded" in str(e):
                print("HALT due to quotaExceeded. Inspect Google Cloud console and reduce schedule or request quota increase.")
                raise
            print("Upload failed:",e)
            continue
    print("Reached TRY_COUNT; abort"); sys.exit(1)

if __name__=="__main__":
    main()
