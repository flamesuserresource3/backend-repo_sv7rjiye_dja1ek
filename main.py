import os
import re
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
import requests
from html import unescape

app = FastAPI(title="Singgihasu API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InspectRequest(BaseModel):
    url: HttpUrl


class MediaItem(BaseModel):
    type: str  # "video" or "image"
    url: HttpUrl
    thumbnail: Optional[HttpUrl] = None


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        # Try to import database module
        from database import db

        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            # Try to list collections to verify connectivity
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]  # Show first 10 collections
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


INSTAGRAM_URL_RE = re.compile(r"^(https?://)?(www\.)?instagram\.com/(p|reel|reels|tv)/[A-Za-z0-9_-]+/?")


def extract_meta_tags(html: str) -> dict:
    # Find all meta tags with property/content
    meta_pattern = re.compile(r'<meta[^>]+property=[\"\']([^\"\']+)[\"\'][^>]+content=[\"\']([^\"\']+)[\"\'][^>]*>', re.IGNORECASE)
    tags = {}
    for prop, content in meta_pattern.findall(html):
        tags[prop.lower()] = unescape(content)
    # Also check name attributes commonly used
    name_pattern = re.compile(r'<meta[^>]+name=[\"\']([^\"\']+)[\"\'][^>]+content=[\"\']([^\"\']+)[\"\'][^>]*>', re.IGNORECASE)
    for name, content in name_pattern.findall(html):
        tags[name.lower()] = unescape(content)
    return tags


@app.post("/api/instagram/inspect", response_model=List[MediaItem])
def instagram_inspect(payload: InspectRequest):
    url = str(payload.url)
    # Basic validation for instagram URL patterns
    if not INSTAGRAM_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="Please provide a valid Instagram post/reel URL.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=12)
    except requests.RequestException:
        raise HTTPException(status_code=502, detail="Failed to fetch the Instagram page.")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="Instagram responded with an error.")

    html = resp.text
    tags = extract_meta_tags(html)

    media: List[MediaItem] = []

    # Prefer OpenGraph video when available
    og_video = tags.get("og:video") or tags.get("twitter:player:stream")
    og_image = tags.get("og:image") or tags.get("twitter:image")

    if og_video:
        item = MediaItem(type="video", url=og_video, thumbnail=og_image)
        media.append(item)

    # Some posts have multiple images via og:image:alt or others; try to collect a couple variants
    # Fallback when only image is available
    if og_image and not media:
        media.append(MediaItem(type="image", url=og_image, thumbnail=og_image))

    # Attempt to parse JSON inside the HTML for additional resources (very light-weight heuristic)
    # Search for "video_url":"..." or "display_url":"..."
    for m in re.finditer(r'"(video_url|display_url)":"(https?:\\/\\/[^\"]+)"', html):
        kind = m.group(1)
        raw = m.group(2).encode('utf-8').decode('unicode_escape')
        candidate = raw.replace('\\/', '/')
        if kind == "video_url":
            if all(x.url != candidate for x in media):
                media.append(MediaItem(type="video", url=candidate, thumbnail=og_image))
        else:
            if all(x.url != candidate for x in media):
                media.append(MediaItem(type="image", url=candidate, thumbnail=candidate))

    if not media:
        # If nothing parsed, provide a friendly error
        raise HTTPException(status_code=422, detail="Could not extract media from this URL. It may be private or blocked.")

    return media


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
