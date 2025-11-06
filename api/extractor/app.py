from fastapi import FastAPI, Query, Body, HTTPException
from pydantic import BaseModel
from urllib.parse import urlparse
import httpx
import trafilatura
from readability import Document
import lxml.html
import hashlib
import json

app = FastAPI(title="Text Extractor (Private)", version="0.2.0")

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


# === ============
# === ============  MT (Argos Translate) minimal endpoint  ============ ===
# 既存の import 群の下に置いてOK。重複 import は気にしなくて大丈夫です。
# === ============
# === ============

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
import os, re, pathlib, time

try:
    from argostranslate import package as argos_package
    from argostranslate import translate as argos_translate
    ARGOS_AVAILABLE = True
except Exception as ex:
    ARGOS_AVAILABLE = False
    _argos_import_error = str(ex)

mt_router = APIRouter(prefix="/mt", tags=["mt"])

# モデル保存先（永続化推奨）：docker-compose の volume に合わせてもOK
ARGOS_MODEL_DIR = os.getenv("ARGOS_MODEL_DIR", "/models/argos")
pathlib.Path(ARGOS_MODEL_DIR).mkdir(parents=True, exist_ok=True)

# 言語コードを素直に寄せる（zh-CN→zh など）
def _norm_lang(code: str) -> str:
    if not code:
        return code
    code = code.lower()
    # よく来るやつだけ丸める
    if code.startswith("en"): return "en"
    if code.startswith("ja"): return "ja"
    if code.startswith("zh"): return "zh"
    if code.startswith("ko"): return "ko"
    if code.startswith("fr"): return "fr"
    if code.startswith("de"): return "de"
    if code.startswith("es"): return "es"
    return code.split("-")[0]

def _split_for_mt(text: str, max_len: int = 2000) -> List[str]:
    """シンプル分割：句点・改行で区切りつつ max_len で丸める（閲覧用途想定）"""
    if len(text) <= max_len:
        return [text]
    # 句点優先で切る → まだ長ければ max_len で強制
    parts = re.split(r"(?<=[。．.!?！？])\s+", text)
    chunks, cur = [], ""
    for p in parts:
        if len(cur) + len(p) + 1 <= max_len:
            cur += (p if not cur else " " + p)
        else:
            if cur:
                chunks.append(cur)
            if len(p) <= max_len:
                cur = p
            else:
                # さらにデカい場合は強制分割
                for i in range(0, len(p), max_len):
                    seg = p[i:i+max_len]
                    if cur:
                        chunks.append(cur)
                        cur = ""
                    chunks.append(seg)
    if cur:
        chunks.append(cur)
    return chunks

# 初回のみ：対象ペアが無ければ取得→インストール
# 置き換え：初回のみモデルを取得・導入（Argos 1.9+ 対応）
def _ensure_pair_installed(src: str, tgt: str) -> None:
    src, tgt = _norm_lang(src), _norm_lang(tgt)
    os.environ.setdefault("ARGOS_DATA_DIR", ARGOS_MODEL_DIR)
    pathlib.Path(os.environ["ARGOS_DATA_DIR"]).mkdir(parents=True, exist_ok=True)

    # 互換: インストール済みペア確認
    try:
        get_installed_translations = getattr(argos_translate, "get_installed_translations", None)
        if callable(get_installed_translations):
            for tr in get_installed_translations():
                if tr.from_lang.code == src and tr.to_lang.code == tgt:
                    return
        else:
            for l in argos_translate.get_installed_languages():
                g = getattr(l, "get_translations", None)
                if callable(g):
                    if any(t.to_lang.code == tgt for t in l.get_translations() if l.code == src):
                        return
                else:
                    ts = getattr(l, "translations", []) or []
                    if l.code == src and any(t.to_lang.code == tgt for t in ts):
                        return
    except Exception:
        pass

    # ここまで来たら未インストール → 取得・導入
    argos_package.update_package_index()
    pkgs = [p for p in argos_package.get_available_packages() if p.from_code == src and p.to_code == tgt]
    if not pkgs:
        raise RuntimeError(f"Argos model not found for {src}->{tgt}")

    pkg = pkgs[0]
    download_path = pkg.download()  # ← 引数なし（互換）
    argos_package.install_from_path(download_path)

def _translate(text: str, src: str, tgt: str) -> str:
    src, tgt = _norm_lang(src), _norm_lang(tgt)
    _ensure_pair_installed(src, tgt)
    # トランスレータ取得
    langs = argos_translate.get_installed_languages()
    from_lang = next((l for l in langs if l.code == src), None)
    to_lang = next((l for l in langs if l.code == tgt), None)
    if not from_lang or not to_lang:
        raise RuntimeError(f"Installed languages not found: {src}->{tgt}")
    translator = from_lang.get_translation(to_lang)

    # 長文は分割 → 連結（閲覧用）
    chunks = _split_for_mt(text, max_len=2000)
    outs: List[str] = []
    for ch in chunks:
        outs.append(translator.translate(ch))
    return "\n".join(outs)

class MTRequest(BaseModel):
    text: str = Field(..., description="原文テキスト")
    source_lang: Optional[str] = Field(None, description="en/ja/zh など（未指定なら自分で判断してもOK）")
    target_lang: str = Field("ja", description="既定は日本語へ")

class MTResponse(BaseModel):
    translated_text: str
    engine: str = "argos"
    elapsed_ms: int

@mt_router.post("", response_model=MTResponse)
def mt_translate(req: MTRequest):
    if not ARGOS_AVAILABLE:
        raise HTTPException(status_code=500, detail=f"Argos not available: {_argos_import_error}")
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")

    src = _norm_lang(req.source_lang) if req.source_lang else "en"  # ざっくり既定：英→日
    tgt = _norm_lang(req.target_lang or "ja")
    t0 = time.time()
    try:
        out = _translate(req.text, src, tgt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mt failed: {e}")
    dt = int((time.time() - t0) * 1000)
    return MTResponse(translated_text=out, elapsed_ms=dt)


@mt_router.get("/models")
def mt_models():
    if not ARGOS_AVAILABLE:
        raise HTTPException(status_code=500, detail=f"Argos not available: {_argos_import_error}")

    langs = argos_translate.get_installed_languages()
    # Argos 1.x / 2.x 互換: to言語一覧を安全に組み立てる
    pairs = []
    try:
        # 2.x ならグローバルで取れる
        get_installed_translations = getattr(argos_translate, "get_installed_translations", None)
        if callable(get_installed_translations):
            for tr in get_installed_translations():
                pairs.append((tr.from_lang.code, tr.to_lang.code))
        else:
            # 1.x 互換
            for l in langs:
                g = getattr(l, "get_translations", None)
                if callable(g):
                    for t in l.get_translations():
                        pairs.append((l.code, t.to_lang.code))
                else:
                    # 2.x 互換: .translations プロパティ
                    ts = getattr(l, "translations", []) or []
                    for t in ts:
                        pairs.append((l.code, t.to_lang.code))
    except Exception:
        pairs = []

    return {
        "models": [
            {
                "code": l.code,
                "name": getattr(l, "name", l.code),
                "to": sorted({to for (frm, to) in pairs if frm == l.code})
            }
            for l in langs
        ],
        "dir": os.getenv("ARGOS_DATA_DIR", ARGOS_MODEL_DIR),
    }

app.include_router(mt_router)  # 既存の app に必ずマウント（再生成しない）

# === ============  /mt endpoint end  ============ ===

# --- add to app.py (FastAPI) ---
from pydantic import BaseModel
import re
from datetime import datetime, timezone

class AnalyzeReq(BaseModel):
    title: str | None = None
    body: str
    fetched_at: str | None = None  # ISO8601
    lang: str | None = None

def _clamp100(x: float) -> float:
    return max(0.0, min(100.0, x))

def _analyze_free_core(title: str | None, body: str, fetched_at_iso: str | None, lang: str | None):
    if not body or not body.strip():
        return {"error": "empty body"}
    words = len(re.split(r"\s+", body.strip()))
    urls  = len(re.findall(r"https?://\S+", body))
    nums  = len(re.findall(r"\d+", body))
    bangs = len(re.findall(r"[!！?？]+", body))

    s_fresh = 0
    try:
        if fetched_at_iso:
            dt  = datetime.fromisoformat(fetched_at_iso.replace("Z","+00:00"))
            now = datetime.now(timezone.utc)
            h   = (now - dt).total_seconds() / 3600.0
            s_fresh = 40 if h <= 24 else 30 if h <= 48 else 10 if h <= 24*7 else 5 if h <= 24*30 else 0
    except Exception:
        s_fresh = 0

    ratio_nums = nums/words if words else 0.0
    ratio_urls = urls/words if words else 0.0
    s_quality  = _clamp100(100.0*ratio_nums)*0.25
    s_quality -= _clamp100(100.0*ratio_urls)*0.10
    s_quality -= min(bangs, 20) * 0.3
    s_quality  = max(0.0, min(25.0, s_quality))
    s_sense    = float(min(bangs, 15))
    s_ever     = 10.0 if words >= 300 else (5.0 if words >= 120 else 0.0)
    s_novel    = 10.0
    s_pri      = round(0.40*s_fresh + 0.25*s_quality + 0.20*s_ever + 0.15*s_novel)

    return {
        "scores_v1":   {"freshness":s_fresh,"quality":s_quality,"novelty":s_novel,"sensation":s_sense,"evergreen":s_ever,"priority":s_pri},
        "features_v1": {"lang":(lang or ""),"words":words,"urls":urls,"numbers":nums,"bangs":bangs,"freshness_score_raw":s_fresh}
    }

@app.post("/analyze_free")
def analyze_free(req: AnalyzeReq):
    res = _analyze_free_core(req.title, req.body, req.fetched_at, req.lang)
    if "error" in res:
        return {"ok": False, "error": res["error"]}
    return {"ok": True, **res}


