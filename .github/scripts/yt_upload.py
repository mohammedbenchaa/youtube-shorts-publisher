#!/usr/bin/env python3
"""Upload a video to YouTube (runs inside GitHub Actions).

Credentials come from repo secrets via environment variables:
YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN.
Prints video_id= / watch_url= / shorts_url= on success.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default="public")
    args = ap.parse_args()

    cid = os.environ["YT_CLIENT_ID"]
    csec = os.environ["YT_CLIENT_SECRET"]
    rtok = os.environ["YT_REFRESH_TOKEN"]

    # 1. download the video
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        vreq = urllib.request.Request(args.video_url, method="GET")
        with urllib.request.build_opener(NoRedirect()).open(vreq, timeout=300) as r:
            size = 0
            with open(tmp.name, "wb") as f:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
        if size == 0:
            raise RuntimeError("downloaded video is empty")

        # 2. token
        tok = mint_token(cid, csec, rtok)

        # 3. resumable upload session
        meta = {
            "snippet": {
                "title": args.title[:100],
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

        # 4. upload bytes
        with open(tmp.name, "rb") as f:
            payload = f.read()
        put = urllib.request.Request(session_url, method="PUT")
        put.add_header("Authorization", f"Bearer {tok}")
        put.add_header("Content-Type", "video/mp4")
        put.add_header("Content-Length", str(len(payload)))
        _, _, data = http_json(put, data=payload, timeout=600)
        vid = data.get("id")
        if not vid:
            raise RuntimeError("no video id returned")
        print(f"video_id={vid}")
        print(f"watch_url=https://www.youtube.com/watch?v={vid}")
        print(f"shorts_url=https://www.youtube.com/shorts/{vid}")
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


if __name__ == "__main__":
    main()
