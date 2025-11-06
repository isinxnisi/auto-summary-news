from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field
import re
from datetime import datetime, timezone

router = APIRouter()

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
