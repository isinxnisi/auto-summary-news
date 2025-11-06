from typing import Optional
from fastapi import APIRouter, HTTPException
import httpx
import trafilatura
from readability import Document
import json

router = APIRouter()

class ExtractRequest(BaseModel):
    url: str
    timeout_sec: int = 15
    with_metadata: bool = True

# ---------- helpers ----------
def _download(url: str, timeout: int) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.get(url, headers=headers)
        r.raise_for_status()
        return r.content

def _guid_from_url(u: str) -> str:
    return hashlib.sha1(u.encode("utf-8")).hexdigest()

def _normalize_json(j: dict, base_url: str) -> dict:
    authors = []
    if isinstance(j.get("authors"), list):
        authors = j["authors"]
    elif j.get("author"):
        authors = [j["author"]]
    return {
        "title": j.get("title") or "",
        "text": j.get("text") or "",
        "date": j.get("date") or j.get("raw_date") or "",
        "authors": ", ".join(authors),
        "url": base_url,
        "host": j.get("source-hostname") or urlparse(base_url).hostname or "",
        "lang": j.get("language") or "",
        "guid": _guid_from_url(base_url),
    }

def _extract_trafilatura(html: bytes, base_url: str, with_metadata: bool):
    out = trafilatura.extract(
        html,
        url=base_url,
        output="json" if with_metadata else "txt",
        with_metadata=with_metadata,
        include_images=False,
        include_tables=False,
        include_comments=False,
        favor_recall=True,
        no_fallback=False,
        deduplicate=True,
    )
    if not out:
        return None
    if with_metadata:
        try:
            j = json.loads(out)
        except Exception:
            return None
        j["_normalized"] = _normalize_json(j, base_url)
        return j
    return {"text": out, "_normalized": _normalize_json({"text": out}, base_url)}

def _extract_readability(html: bytes, base_url: str, with_metadata: bool):
    try:
        doc = Document(html)
        title = doc.title() or ""
        summary_html = doc.summary(html_partial=True)
        tree = lxml.html.fromstring(summary_html)
        text = tree.text_content().strip()
        if not text:
            return None
        norm = {
            "title": title,
            "text": text,
            "date": "",
            "authors": "",
            "url": base_url,
            "host": urlparse(base_url).hostname or "",
            "lang": "",
            "guid": _guid_from_url(base_url),
        }
        if with_metadata:
            return {"title": title, "text": text, "_normalized": norm}
        else:
            return {"text": text, "_normalized": norm}
    except Exception:
        return None

def _extract(html: bytes, base_url: str, with_metadata: bool):
    # 1st: Trafilatura
    r1 = _extract_trafilatura(html, base_url, with_metadata)
    if r1:
        return r1
    # 2nd: Readability fallback
    r2 = _extract_readability(html, base_url, with_metadata)
    if r2:
        return r2
    return None

# ---------- endpoints ----------
@app.get("/extract")
def extract_get(
    url: str = Query(..., description="Target URL"),
    timeout_sec: int = 15,
    with_metadata: bool = True,
):
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise HTTPException(400, "Invalid URL scheme")
    try:
        html = _download(url, timeout_sec)
        result = _extract(html, url, with_metadata)
        if not result:
            raise HTTPException(422, "Extraction failed")
        return {"ok": True, "result": result}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Fetch failed: {e}")  # type: ignore
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Fetch failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Internal error: {e}")

@app.post("/extract")
def extract_post(req: ExtractRequest):
    return extract_get(req.url, req.timeout_sec, req.with_metadata)

@app.post("/batch")
def batch(
    urls: list[str] = Body(..., embed=True, description="List of URLs"),
    timeout_sec: int = 15,
    with_metadata: bool = True,
):
    items = []
    for u in urls:
        try:
            html = _download(u, timeout_sec)
            result = _extract(html, u, with_metadata)
            items.append({"url": u, "ok": bool(result), "result": result})
        except Exception as e:
            items.append({"url": u, "ok": False, "error": str(e)})
    return {"count": len(items), "items": items}
