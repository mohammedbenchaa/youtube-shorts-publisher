#!/usr/bin/env python3
"""Upload a video to YouTube (runs inside GitHub Actions).

Credentials come from repo secrets via environment variables:
YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN.

Modes:
  1. Upload:  --video-url ... [--thumbnail-url ...]
  2. Thumbnail only: --video-id ... --thumbnail-url ...

Prints video_id= / watch_url= / shorts_url= on success,
and thumbnail_set=true when a thumbnail was applied.
"""
import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


def http_headers(req, data=None, timeout=120):
    """Raw request returning (status, headers) — for empty-body responses."""
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, data=data, timeout=timeout) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {}
        detail = err.get("error")
        if isinstance(detail, dict):
            msg = detail.get("message", f"http_{e.code}")
        elif isinstance(detail, str):
            msg = f"{detail}: {err.get('error_description', '')}"
        else:
            msg = f"http_{e.code}"
        raise RuntimeError(f"request failed ({e.code}): {msg}")


def http_json(req, data=None, timeout=120):
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, data=data, timeout=timeout) as r:
            return r.status, dict(r.headers), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {}
        detail = err.get("error")
        if isinstance(detail, dict):
            msg = detail.get("message", f"http_{e.code}")
        elif isinstance(detail, str):
            msg = f"{detail}: {err.get('error_description', '')}"
        else:
            msg = f"http_{e.code}"
        raise RuntimeError(f"request failed ({e.code}): {msg}")


def mint_token(cid, csec, rtok):
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": cid,
        "client_secret": csec,
        "refresh_token": rtok,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    _, _, data = http_json(req, data=body, timeout=60)
    tok = data.get("access_token")
    if not tok:
        raise RuntimeError("no access_token returned")
    return tok


def download_file(url, timeout=300):
    """Download a remote file, returning (bytes, content_type_guess)."""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as r:
        data = r.read()
    if not data:
        raise RuntimeError("downloaded file is empty")
    return data


def guess_image_type(data, url):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    low = (url or "").lower()
    if low.endswith(".png"):
        return "image/png"
    return "image/jpeg"


def set_thumbnail(tok, video_id, img_bytes, content_type):
    """Apply a custom thumbnail to an existing video (thumbnails.set)."""
    if len(img_bytes) > 2 * 1024 * 1024:
        raise RuntimeError("thumbnail image must be under 2 MB")
    url = (API + "/upload/youtube/v3/thumbnails/set"
           f"?videoId={urllib.parse.quote(video_id)}&uploadType=media")
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Content-Type", content_type)
    req.add_header("Content-Length", str(len(img_bytes)))
    _, _, data = http_json(req, data=img_bytes, timeout=120)
    items = data.get("items") or []
    if not items:
        raise RuntimeError("thumbnail.set returned no items")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", default="",
                    help="Direct URL of the mp4 to upload (upload mode)")
    ap.add_argument("--video-id", default="",
                    help="Existing video ID: skips the upload, only sets the thumbnail")
    ap.add_argument("--thumbnail-url", default="",
                    help="Direct URL of the thumbnail image (png/jpg, under 2 MB)")
    ap.add_argument("--title", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default="public")
    args = ap.parse_args()

    video_url = (args.video_url or "").strip()
    video_id = (args.video_id or "").strip()
    thumb_url = (args.thumbnail_url or "").strip()

    cid = os.environ["YT_CLIENT_ID"]
    csec = os.environ["YT_CLIENT_SECRET"]
    rtok = os.environ["YT_REFRESH_TOKEN"]

    if not video_url and not video_id:
        raise RuntimeError("need --video-url (upload) or --video-id (thumbnail only)")
    if video_id and not video_url and not thumb_url:
        raise RuntimeError("thumbnail-only mode needs --thumbnail-url")

    # 1. token
    tok = mint_token(cid, csec, rtok)

    vid = None
    if video_url:
        # --- upload mode ---
        # 1a. download the video
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        try:
            payload = download_file(video_url)
            size = len(payload)

            # 2. resumable upload session
            meta = {
                "snippet": {
                    "title": args.title[:100] or "Short",
                    "description": args.description[:5000],
                    "categoryId": "28",
                },
                "status": {
                    "privacyStatus": args.privacy,
                    "selfDeclaredMadeForKids": False,
                },
            }
            init = API + "/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
            req = urllib.request.Request(init, method="POST")
            req.add_header("Authorization", f"Bearer {tok}")
            req.add_header("Content-Type", "application/json; charset=UTF-8")
            req.add_header("X-Upload-Content-Type", "video/mp4")
            req.add_header("X-Upload-Content-Length", str(size))
            # NB: session initiation returns 200 with an EMPTY body;
            # the session URL comes in the Location header — do not JSON-parse it.
            _, headers = http_headers(req, data=json.dumps(meta).encode())
            session_url = headers.get("Location")
            if not session_url:
                raise RuntimeError("no resumable session URL")

            # 3. upload bytes
            put = urllib.request.Request(session_url, method="PUT")
            put.add_header("Authorization", f"Bearer {tok}")
            put.add_header("Content-Type", "video/mp4")
            put.add_header("Content-Length", str(size))
            _, _, data = http_json(put, data=payload, timeout=600)
            vid = data.get("id")
            if not vid:
                raise RuntimeError("no video id returned")
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    elif video_id:
        # --- thumbnail-only mode ---
        vid = video_id
    else:
        raise RuntimeError("need --video-url (upload) or --video-id (thumbnail only)")

    if thumb_url:
        img = download_file(thumb_url, timeout=120)
        ctype = guess_image_type(img, thumb_url)
        set_thumbnail(tok, vid, img, ctype)
        print("thumbnail_set=true")

    print(f"video_id={vid}")
    print(f"watch_url=https://www.youtube.com/watch?v={vid}")
    print(f"shorts_url=https://www.youtube.com/shorts/{vid}")


if __name__ == "__main__":
    main()
