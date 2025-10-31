import os, random, requests, time
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

# إعداد متغيرات البيئة
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
YOUTUBE_TOKEN = "token.json"

# رابط Pexels
PEXELS_URL = "https://api.pexels.com/videos/search?query=relaxing+nature&per_page=10"

# تحميل فيديو من Pexels
def download_video():
    headers = {"Authorization": PEXELS_API_KEY}
    res = requests.get(PEXELS_URL, headers=headers)
    videos = res.json().get("videos", [])
    if not videos:
        print("❌ لا يوجد فيديوهات متاحة.")
        return None
    video_url = random.choice(videos)["video_files"][0]["link"]
    file_name = "relax.mp4"
    open(file_name, "wb").write(requests.get(video_url).content)
    return file_name

# رفع الفيديو على YouTube
def upload_to_youtube(file_path):
    creds = Credentials.from_authorized_user_file(YOUTUBE_TOKEN)
    youtube = build("youtube", "v3", credentials=creds)

    title = f"Relaxing Nature Sounds {random.randint(1000,9999)}"
    description = "Enjoy relaxing nature sounds 🌿 Perfect for sleep, meditation, and peace."
    tags = ["relax", "nature", "sleep", "calm"]

    request_body = {
        "snippet": {
            "categoryId": "19",
            "title": title,
            "description": description,
            "tags": tags
        },
        "status": {"privacyStatus": "public"}
    }

    media = MediaFileUpload(file_path)
    youtube.videos().insert(part="snippet,status", body=request_body, media_body=media).execute()
    print(f"✅ Uploaded: {title}")

if _name_ == "_main_":
    print("⏳ Starting automation...")
    video = download_video()
    if video:
        upload_to_youtube(video)
    else:
        print("⚠️ No video uploaded.")
