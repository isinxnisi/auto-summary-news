import asyncio
import json
import logging
import os
import shlex
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger("media-api")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


class ParameterBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SceneDraft(ParameterBaseModel):
    startFrame: int = Field(..., ge=0)
    durationSec: Optional[float] = Field(None, ge=0)


class ScriptItem(ParameterBaseModel):
    type: Optional[str] = Field("talk", description="トーク/ナレーション種類")
    speaker: Optional[str] = Field(None, description="left/right など話者キー")
    text: Optional[str] = None
    voice: Optional[str] = None
    voiceSec: Optional[float] = Field(None, ge=0)
    durationFrames: Optional[int] = Field(None, ge=0)
    durationSec: Optional[float] = Field(None, ge=0)


class ScriptGroupDraft(ParameterBaseModel):
    id: str
    gapSec: Optional[float] = Field(None, ge=0)
    keepStack: Optional[bool] = True
    items: List[ScriptItem]


class ParameterJsonDraft(ParameterBaseModel):
    spec: dict
    meta: dict
    scenes: List[SceneDraft]
    scriptGroups: List[ScriptGroupDraft]
    captions: Optional[List[dict]] = None
    banners: Optional[List[dict]] = None
    speeches: Optional[List[dict]] = None
    vars: Optional[dict] = None


class JobOptions(BaseModel):
    render: bool = True
    overwrite: bool = False
    generateAudio: bool = True
    dryRun: bool = False


class VideoJobRequest(BaseModel):
    videoId: str = Field(..., pattern=r"^[A-Za-z0-9._-]+$", description="最終ファイル名のベース")
    parameter: ParameterJsonDraft
    options: JobOptions = Field(default_factory=JobOptions)
    voicePresets: Optional[Dict[str, Dict[str, Any]]] = None


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class JobRecord:
    job_id: str
    request: VideoJobRequest
    status: JobStatus = JobStatus.queued
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    progress: Dict[str, Any] = field(default_factory=lambda: {"stage": "waiting"})
    result: Optional[dict] = None
    error: Optional[dict] = None


PROJECTS_ROOT = Path(os.getenv("MEDIA_PROJECT_DIR", "/remotion-projects"))
VIDEO_OUT_DIR = Path(os.getenv("MEDIA_OUTPUT_DIR", "/remotion-out"))
PARAM_TEMPLATE = os.getenv("MEDIA_PARAM_TEMPLATE", "{video_id}/parameter.json")
VOICEVOX_URL = os.getenv("VOICEVOX_URL", "http://voicevox:50021").rstrip("/")
VOICEVOX_TIMEOUT = float(os.getenv("VOICEVOX_TIMEOUT", "30"))
HOOK_MARGIN_SEC = float(os.getenv("HOOK_MARGIN_SEC", "0.8"))
MIN_HOOK_SEC = float(os.getenv("MIN_HOOK_SEC", "3.0"))
DEFAULT_CHAR_PER_SEC = float(os.getenv("DEFAULT_CHAR_PER_SEC", "8.0"))
HOOK_AUDIO_PATH = os.getenv("HOOK_AUDIO_PATH", "media/audio/hook.wav")
REMOTION_WORKDIR_IN_CONTAINER = os.getenv("REMOTION_WORKDIR_IN_CONTAINER", "/app/ns-video")
REMOTION_DOCKER_SERVICE = os.getenv("REMOTION_DOCKER_SERVICE", "remotion")
REMOTION_DOCKER_SHELL = os.getenv("REMOTION_DOCKER_SHELL", "/bin/sh")
REMOTION_DOCKER_USER = os.getenv("REMOTION_DOCKER_USER", "node")
REMOTION_RENDER_COMMAND = os.getenv(
    "REMOTION_RENDER_COMMAND",
    "REMOTION_BUNDLE_CACHE=/tmp/remotion-cache REMOTION_DISABLE_DEFAULT_FOLDER_CLEANUP=true npm run render:project -- --project {video_id}",
)
REMOTION_OUTPUT_TEMPLATE = os.getenv("REMOTION_OUTPUT_TEMPLATE", "{video_id}.mp4")
VOICE_QUERY_KEYS = {
    "speedScale",
    "pitchScale",
    "intonationScale",
    "volumeScale",
    "prePhonemeLength",
    "postPhonemeLength",
    "pitch",
    "pauseLength",
    "pauseLengthScale",
    "outputSamplingRate",
    "outputStereo",
}


def _load_speaker_map() -> Dict[str, int]:
    raw = os.getenv("VOICEVOX_SPEAKER_MAP")
    default_left = int(os.getenv("VOICEVOX_LEFT_SPEAKER", "8"))
    default_right = int(os.getenv("VOICEVOX_RIGHT_SPEAKER", "3"))
    default_map = {"left": default_left, "right": default_right}
    if not raw:
        return default_map
    try:
        data = json.loads(raw)
        parsed = {str(k): int(v) for k, v in data.items()}
        return parsed or default_map
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to parse VOICEVOX_SPEAKER_MAP (%s). Fallback to defaults.", exc)
        return default_map


VOICEVOX_SPEAKERS = _load_speaker_map()
_fallback_speaker = next(iter(VOICEVOX_SPEAKERS.values()), 3)
DEFAULT_VOICEVOX_SPEAKER = int(os.getenv("VOICEVOX_DEFAULT_SPEAKER", str(_fallback_speaker)))
HOOK_VOICEVOX_SPEAKER = int(os.getenv("VOICEVOX_HOOK_SPEAKER", str(DEFAULT_VOICEVOX_SPEAKER)))

PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
VIDEO_OUT_DIR.mkdir(parents=True, exist_ok=True)

def _extract_voice_presets(
    parameter: ParameterJsonDraft,
    request_presets: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    presets: Dict[str, Dict[str, Any]] = {}
    vars_section = parameter.vars or {}
    raw_presets = vars_section.get("voicePresets") if isinstance(vars_section, dict) else None
    if isinstance(raw_presets, dict):
        for key, value in raw_presets.items():
            if isinstance(value, dict):
                presets[str(key)] = value
    if isinstance(request_presets, dict):
        for key, value in request_presets.items():
            if isinstance(value, dict):
                presets[str(key)] = value
    return presets

JOBS: Dict[str, JobRecord] = {}
JOBS_LOCK = asyncio.Lock()

app = FastAPI(title="media-api", version="0.2.0")


def _job_summary(job: JobRecord) -> dict:
    return {
        "jobId": job.job_id,
        "status": job.status,
        "videoId": job.request.videoId,
        "createdAt": job.created_at.isoformat(),
        "updatedAt": job.updated_at.isoformat(),
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
    }


async def _update_job(job_id: str, *, status: Optional[JobStatus] = None, progress: Optional[Dict[str, Any]] = None, result: Optional[dict] = None, error: Optional[dict] = None):
    async with JOBS_LOCK:
        job = JOBS[job_id]
        if status:
            job.status = status
        if progress:
            job.progress = {**(job.progress or {}), **progress}
        if result is not None:
            job.result = result
        if error is not None:
            job.error = error
        job.updated_at = datetime.now(timezone.utc)


def _project_dir(video_id: str) -> Path:
    path = PROJECTS_ROOT / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_join(base: Path, relative: str) -> Path:
    clean = relative.strip().lstrip("/\\")
    rel_path = Path(clean)
    rel_parts = [p for p in rel_path.parts if p not in ("", ".", "..")]
    full = (base / Path(*rel_parts)).resolve()
    base_resolved = base.resolve()
    if not str(full).startswith(str(base_resolved)):
        raise ValueError("unsafe path detected")
    full.parent.mkdir(parents=True, exist_ok=True)
    return full


def _parameter_path(video_id: str) -> Path:
    rel = PARAM_TEMPLATE.format(video_id=video_id).strip()
    return _safe_join(PROJECTS_ROOT, rel)


def _normalize_voice_path(video_id: str, value: str) -> str:
    if not value:
        return ""
    clean = value.strip()
    prefixes = [
        f"/data/projects/{video_id}/",
        f"data/projects/{video_id}/",
        f"/{video_id}/",
        f"{video_id}/",
    ]
    for prefix in prefixes:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    return clean.lstrip("/\\")


def _default_voice_name(video_id: str, group_index: int, item_index: int) -> str:
    return f"media/audio/{video_id}-{group_index + 1:02d}-{item_index + 1:02d}.wav"


def _resolve_voice_file(video_id: str, voice_value: str) -> Path:
    project = _project_dir(video_id)
    target_rel = _normalize_voice_path(video_id, voice_value)
    if not target_rel:
        raise ValueError("voice path is empty")
    return _safe_join(project, target_rel)


def _estimate_voice_sec(text: Optional[str]) -> float:
    if not text:
        return 0.0
    length = len(text.strip())
    if length == 0:
        return 0.0
    seconds = max(0.4, length / max(DEFAULT_CHAR_PER_SEC, 1))
    return round(seconds, 3)


async def _probe_audio_duration(path: Path) -> float:
    if not path.exists():
        raise FileNotFoundError(path)
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {stderr.decode().strip() or stdout.decode().strip()}")
    try:
        return round(float(stdout.decode().strip()), 3)
    except ValueError as exc:  # pragma: no cover
        raise RuntimeError(f"invalid ffprobe output: {stdout!r}") from exc


async def _synthesize_voice(client: httpx.AsyncClient, text: str, speaker_id: int, settings: Optional[Dict[str, Any]] = None) -> bytes:
    query = await client.post("/audio_query", params={"text": text, "speaker": speaker_id})
    query.raise_for_status()
    query_data = query.json()
    if settings:
        for key, value in settings.items():
            if key in VOICE_QUERY_KEYS:
                query_data[key] = value
    synthesis = await client.post("/synthesis", params={"speaker": speaker_id}, json=query_data)
    synthesis.raise_for_status()
    return synthesis.content


def _resolve_speaker_id(item: ScriptItem) -> int:
    extra = getattr(item, "model_extra", {}) or {}
    candidate = extra.get("voicevoxSpeaker") or extra.get("ttsSpeakerId") or extra.get("voiceSpeaker")
    if candidate is not None:
        try:
            return int(candidate)
        except (TypeError, ValueError):
            logger.warning("Invalid speaker override %s. Falling back.", candidate)
    if item.speaker and item.speaker in VOICEVOX_SPEAKERS:
        return VOICEVOX_SPEAKERS[item.speaker]
    return DEFAULT_VOICEVOX_SPEAKER


def _compute_scene0_duration(parameter: ParameterJsonDraft) -> Optional[float]:
    if not parameter.scenes or not parameter.scriptGroups:
        return None
    first_scene = parameter.scenes[0]
    audio = getattr(first_scene, "audio", None)
    audio_duration: Optional[float] = None
    if isinstance(audio, dict):
        val = audio.get("durationSec")
        if isinstance(val, (int, float)):
            audio_duration = float(val)
    if not audio_duration:
        first_group = parameter.scriptGroups[0]
        total = 0.0
        gap = first_group.gapSec or 0.0
        for idx, item in enumerate(first_group.items):
            total += item.voiceSec or _estimate_voice_sec(item.text)
            if idx < (len(first_group.items) - 1):
                total += gap
        total += HOOK_MARGIN_SEC
        audio_duration = max(MIN_HOOK_SEC, total)
    computed = round(audio_duration, 3)
    first_scene.durationSec = computed
    return computed


def _voice_settings_for_object(
    obj: ParameterBaseModel,
    voice_presets: Dict[str, Dict[str, Any]],
    *,
    default_key: Optional[str],
    fallback_speaker: int,
) -> tuple[int, Dict[str, Any]]:
    extra = getattr(obj, "model_extra", {}) or {}
    settings: Dict[str, Any] = {}

    speaker_keys: List[str] = []
    voice_speaker = extra.get("voiceSpeaker")
    if voice_speaker:
        speaker_keys.append(str(voice_speaker))
    if default_key:
        speaker_keys.append(str(default_key))

    for key in speaker_keys:
        preset = voice_presets.get(key)
        if isinstance(preset, dict):
            settings.update(preset)

    preset_name = extra.get("voicePreset")
    if isinstance(preset_name, str):
        preset = voice_presets.get(preset_name)
        if isinstance(preset, dict):
            settings.update(preset)

    inline = extra.get("voiceTts")
    if isinstance(inline, dict):
        settings.update(inline)

    speaker_id = settings.get("speakerId")
    try:
        speaker_id = int(speaker_id)
    except (TypeError, ValueError):
        speaker_id = None
    if speaker_id is None:
        speaker_id = fallback_speaker
    settings = {k: v for k, v in settings.items() if k != "speakerId"}
    return speaker_id, settings


def _hook_scene(parameter: ParameterJsonDraft):
    if not parameter.scenes:
        return None
    scene = parameter.scenes[0]
    text = getattr(scene, "text", None)
    if not text:
        return None
    return scene


def _prepare_scene_audio(scene: SceneDraft) -> Dict[str, Any]:
    audio = getattr(scene, "audio", None)
    if isinstance(audio, str):
        return {"src": audio, "volume": 1, "startFrom": 0}
    if isinstance(audio, dict):
        return audio
    return {"src": HOOK_AUDIO_PATH, "volume": 1, "startFrom": 0}


async def _process_script_groups(
    job_id: str,
    video_id: str,
    job: JobRecord,
    parameter: ParameterJsonDraft,
    options: JobOptions,
):
    script_groups = parameter.scriptGroups or []
    hook_scene = _hook_scene(parameter)
    hook_required = hook_scene is not None
    request_voice_presets = job.request.voicePresets if hasattr(job.request, "voicePresets") else None
    voice_presets = _extract_voice_presets(parameter, request_voice_presets)
    total_items = sum(len(group.items) for group in script_groups) + (1 if hook_required else 0)
    await _update_job(job_id, progress={"stage": "tts", "ttsTotal": total_items, "ttsDone": 0})
    if total_items == 0:
        return

    async with httpx.AsyncClient(base_url=VOICEVOX_URL, timeout=httpx.Timeout(VOICEVOX_TIMEOUT)) as client:
        done = 0
        for group_index, group in enumerate(script_groups):
            for item_index, item in enumerate(group.items):
                text = (item.text or "").strip()
                if not text:
                    item.voiceSec = 0.0
                    done += 1
                    await _update_job(job_id, progress={"ttsDone": done})
                    continue
                voice_path = item.voice or _default_voice_name(video_id, group_index, item_index)
                item.voice = voice_path
                target = _resolve_voice_file(video_id, voice_path)
                if options.generateAudio:
                    fallback_speaker = _resolve_speaker_id(item)
                    speaker_id, settings = _voice_settings_for_object(
                        item,
                        voice_presets,
                        default_key=item.speaker,
                        fallback_speaker=fallback_speaker,
                    )
                    audio_bytes = await _synthesize_voice(client, text, speaker_id, settings)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(audio_bytes)
                elif not target.exists():
                    raise RuntimeError(f"voice file missing for {voice_path}")
                try:
                    duration = await _probe_audio_duration(target)
                except FileNotFoundError:
                    duration = _estimate_voice_sec(text)
                item.voiceSec = duration
                done += 1
                await _update_job(job_id, progress={"ttsDone": done})

        if hook_required:
            audio_conf = _prepare_scene_audio(hook_scene)
            text = getattr(hook_scene, "text", "") or ""
            voice_path = audio_conf.get("src") or HOOK_AUDIO_PATH
            if not isinstance(voice_path, str) or not voice_path.strip():
                voice_path = HOOK_AUDIO_PATH
            audio_conf["src"] = voice_path
            target = _resolve_voice_file(video_id, voice_path)
            if options.generateAudio:
                speaker_id, settings = _voice_settings_for_object(
                    hook_scene,
                    voice_presets,
                    default_key="hook",
                    fallback_speaker=HOOK_VOICEVOX_SPEAKER,
                )
                audio_bytes = await _synthesize_voice(client, text.strip(), speaker_id, settings)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(audio_bytes)
            elif not target.exists():
                raise RuntimeError(f"voice file missing for {voice_path}")
            try:
                duration = await _probe_audio_duration(target)
            except FileNotFoundError:
                duration = _estimate_voice_sec(text)
            audio_conf["durationSec"] = duration
            setattr(hook_scene, "audio", audio_conf)
            done += 1
            await _update_job(job_id, progress={"ttsDone": done})


def _dump_parameter(parameter: ParameterJsonDraft) -> dict:
    return parameter.model_dump(mode="json", exclude_none=False)


def _write_parameter(video_id: str, parameter: ParameterJsonDraft) -> Path:
    data = _dump_parameter(parameter)
    param_path = _parameter_path(video_id)
    param_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return param_path


async def _render_video(video_id: str) -> Path:
    safe_video = shlex.quote(video_id)
    render_command = REMOTION_RENDER_COMMAND.format(video_id=safe_video)
    env_exports = [
        f"npm_config_project={safe_video}",
        f"REMOTION_PROJECT={safe_video}",
    ]
    exports = " ".join(env_exports)
    if exports:
        render_command = f"{exports} {render_command}"
    if REMOTION_WORKDIR_IN_CONTAINER:
        render_command = f"cd {shlex.quote(REMOTION_WORKDIR_IN_CONTAINER)} && {render_command}"
    exec_cmd = [
        "docker",
        "exec",
        "--user",
        REMOTION_DOCKER_USER,
        REMOTION_DOCKER_SERVICE,
        REMOTION_DOCKER_SHELL,
        "-c",
        render_command,
    ]
    proc = await asyncio.create_subprocess_exec(
        *exec_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Remotion render failed ({proc.returncode}): {stderr.decode().strip() or stdout.decode().strip()}")
    rel_output = REMOTION_OUTPUT_TEMPLATE.format(video_id=video_id).lstrip("/\\")
    produced = (VIDEO_OUT_DIR / rel_output).resolve()
    if not produced.exists():
        raise RuntimeError(f"Remotion output not found: {produced}")
    return produced


async def _run_job(job_id: str):
    job = JOBS[job_id]
    await _update_job(job_id, status=JobStatus.running, progress={"stage": "tts"})
    try:
        video_id = job.request.videoId
        parameter = job.request.parameter.model_copy(deep=True)
        parameter.meta.setdefault("videoId", video_id)

        await _process_script_groups(job_id, video_id, job, parameter, job.request.options)
        await _update_job(job_id, progress={"stage": "param-building"})
        hook_duration = _compute_scene0_duration(parameter)
        param_path = _write_parameter(video_id, parameter)

        result: Dict[str, Any] = {
            "parameterPath": str(param_path),
        }
        if hook_duration:
            result["hookSec"] = hook_duration

        total_sec: Optional[float] = None
        try:
            if parameter.scenes:
                secs = [sc.durationSec for sc in parameter.scenes if sc.durationSec]
                if secs:
                    total_sec = round(sum(secs), 3)
            if not total_sec:
                fps = float(parameter.spec.get("fps", 30))
                frames = float(parameter.spec.get("durationInFrames") or 0)
                if frames > 0:
                    total_sec = round(frames / fps, 3)
        except Exception:
            total_sec = None
        if total_sec:
            result["totalSec"] = total_sec

        if job.request.options.render and not job.request.options.dryRun:
            if REMOTION_RENDER_COMMAND:
                await _update_job(job_id, progress={"stage": "rendering"})
                video_path = await _render_video(video_id)
                result["videoPath"] = str(video_path)
            else:
                logger.info("REMOTION_RENDER_COMMAND not set. Skipping render for %s.", video_id)

        await _update_job(job_id, status=JobStatus.done, result=result, progress={"stage": "finishing"})
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        await _update_job(
            job_id,
            status=JobStatus.failed,
            error={"code": "internal_error", "message": str(exc)},
            progress={"stage": "failed"},
        )


@app.post("/video-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(req: VideoJobRequest, background_tasks: BackgroundTasks):
    video_id = req.videoId
    if not req.options.overwrite:
        param_path = _parameter_path(video_id)
        video_path = VIDEO_OUT_DIR / f"{video_id}.mp4"
        if param_path.exists() or video_path.exists():
            raise HTTPException(status_code=409, detail="videoId already exists. Set overwrite=true to replace.")

    job_id = uuid.uuid4().hex
    record = JobRecord(job_id=job_id, request=req)
    async with JOBS_LOCK:
        JOBS[job_id] = record
    background_tasks.add_task(_run_job, job_id)
    return _job_summary(record)


@app.get("/video-jobs/{job_id}")
async def get_job(job_id: str):
    async with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_summary(job)


@app.get("/video-jobs")
async def list_jobs(status: Optional[JobStatus] = None):
    async with JOBS_LOCK:
        values = list(JOBS.values())
    if status:
        values = [job for job in values if job.status == status]
    return [_job_summary(job) for job in values]


@app.get("/healthz")
async def healthz():
    return {"ok": True}
