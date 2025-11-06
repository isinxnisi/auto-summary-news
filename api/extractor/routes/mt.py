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

