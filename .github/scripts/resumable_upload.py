# Uploads a finished video to the owner's YouTube channel (YouTube Data API v3,
# resumable upload, standard library only).
#   python youtube_upload.py <video.mp4> <render.json> [out.json]
# render.json: {"title": "...", "description": "...", "tags": [...], "privacy": "unlisted",
#               "language": "ar", "category": "28"}
# Secrets, from the environment (GitHub Actions secrets): YT_CLIENT_ID, YT_CLIENT_SECRET,
# YT_REFRESH_TOKEN. Never printed.
# Known limit: a Google Cloud project that has not passed YouTube's API audit can have its
# uploads forced to "private" whatever privacy is asked for. The result reports the status
# YouTube actually applied, so a forced private upload is visible in the log.
import os, sys, json, time, urllib.request, urllib.parse, urllib.error


def env(n):
    v = os.environ.get(n, "").strip()
    if not v:
        sys.exit(f"Missing secret {n}")
    return v


def token():
    body = urllib.parse.urlencode({"client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
                                   "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body), timeout=60) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"Google refused the refresh token (HTTP {e.code}): {e.read()[:200].decode('utf-8', 'replace')}")


def upload(video, meta, access):
    snippet = {"title": meta["title"][:100], "description": meta["description"][:5000],
               "tags": meta.get("tags", []), "categoryId": str(meta.get("category", "28")),
               "defaultLanguage": meta.get("language", "ar"), "defaultAudioLanguage": meta.get("language", "ar")}
    status = {"privacyStatus": meta.get("privacy", "unlisted"), "selfDeclaredMadeForKids": False}
    size = os.path.getsize(video)
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps({"snippet": snippet, "status": status}).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + access, "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            session = r.headers["Location"]
    except urllib.error.HTTPError as e:
        sys.exit(f"YouTube refused to start the upload (HTTP {e.code}): {e.read()[:400].decode('utf-8', 'replace')}")
    chunk = 8 * 1024 * 1024
    with open(video, "rb") as f:
        pos = 0
        while pos < size:
            data = f.read(chunk)
            end = pos + len(data) - 1
            for attempt in range(5):
                put = urllib.request.Request(session, data=data, method="PUT",
                                             headers={"Content-Length": str(len(data)),
                                                      "Content-Range": f"bytes {pos}-{end}/{size}"})
                try:
                    with urllib.request.urlopen(put, timeout=300) as r:
                        return json.load(r)             # 200/201: finished
                except urllib.error.HTTPError as e:
                    if e.code == 308:                    # chunk accepted, send the next
                        break
                    if e.code >= 500 and attempt < 4:
                        time.sleep(5 * (attempt + 1))
                        continue
                    sys.exit(f"Upload failed (HTTP {e.code}): {e.read()[:400].decode('utf-8', 'replace')}")
            pos = end + 1
            print(f"uploaded {pos * 100 // size}%", flush=True)
    sys.exit("Upload ended without a response from YouTube")


if __name__ == "__main__":
    video, meta_file = sys.argv[1], sys.argv[2]
    meta = json.load(open(meta_file, encoding="utf-8"))
    res = upload(video, meta, token())
    vid = res["id"]
    out = {"id": vid, "url": "https://youtu.be/" + vid, "studio": "https://studio.youtube.com/video/" + vid + "/edit",
           "asked": meta.get("privacy", "unlisted"), "applied": res.get("status", {}).get("privacyStatus")}
    print(json.dumps(out, indent=1))
    if out["applied"] != out["asked"]:
        print(f'::warning::YouTube applied "{out["applied"]}" instead of "{out["asked"]}" (unaudited API project?)')
    if len(sys.argv) > 3:
        json.dump(out, open(sys.argv[3], "w"), indent=1)
