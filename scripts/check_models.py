#!/usr/bin/env python3
"""Check that every model in config/models.yaml works on this machine.

    python scripts/check_models.py            # all checks
    python scripts/check_models.py --skip-audit --no-limits

What it does, one model at a time (keep_alive 0, so each model is unloaded
before the next one loads):

1. Server: version, installed tags and digests; warns if a second Ollama
   server answers on [::1] (the old "two servers" problem).
2. Reading and audit models: one JSON-schema call on a short invented
   passage. The answer must parse, fill speaker and stance, and its quotes
   must be found by archive.quote_locator (software locates, model quotes).
3. Embedding model: one text, one image, one short audio clip, and one short
   video (sent as video if the server takes it, otherwise as sampled frames +
   the audio track). Checks the vector size and that media are not ignored.
   With limits on: probes how much audio fits in one input.

Test media are generated locally (macOS `say`, `afconvert`, `ffmpeg`); no
source material is used. The JSON report goes to data/model_check/ (ignored
by git). Exit code 0 only if every required check passes.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from archive.config import load_models_config  # noqa: E402
from archive.ollama import OllamaClient, OllamaError, cosine, media_input  # noqa: E402
from archive.quote_locator import locate_quote  # noqa: E402

# Invented for this check; names and organisation are fictional.
PASSAGE = (
    "The parliamentary committee heard evidence in March 2023. Maria Lopes, a former "
    "member of the Riverside Fellowship, told the committee: \"They promised me that "
    "prayer would heal my attraction to women, and for years I believed I was broken.\" "
    "In a written reply, the Fellowship's director, Paul Kerr, said its courses \"support "
    "people who want to live according to their faith\" and denied that anyone was harmed."
)
EXPECTED = {"maria lopes": "critical", "paul kerr": "supportive"}

READING_SCHEMA = {
    "type": "object",
    "properties": {
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "stance": {"type": "string",
                               "enum": ["supportive", "critical", "neutral", "unclear"]},
                    "quote": {"type": "string"},
                },
                "required": ["speaker", "stance", "quote"],
            },
        }
    },
    "required": ["statements"],
}

READING_PROMPT = (
    "Read the passage. List each person who makes a statement about the change "
    "efforts described. For each give: speaker (full name), stance towards the "
    "change efforts (supportive, critical, neutral or unclear), and quote: the "
    "shortest verbatim span from the passage, copied exactly, at most 200 "
    "characters, that shows the stance. Reply with JSON only.\n\nPassage:\n"
)

SPEECH = "Conversion therapy is a harmful practice that tries to change who people are."
TEXT_MATCH = "Conversion therapy is a harmful practice."
TEXT_OTHER = "The weather in Bergen is rainy in November."


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Test media
# ---------------------------------------------------------------------------

def make_media(folder: Path) -> dict[str, Path]:
    for tool in ("say", "afconvert", "ffmpeg"):
        if not shutil.which(tool):
            raise SystemExit(f"missing tool: {tool} (needs macOS and ffmpeg)")
    media = {}
    aiff = folder / "speech.aiff"
    run(["say", "-o", str(aiff), SPEECH])
    media["audio"] = folder / "speech.wav"
    run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(media["audio"])])
    for colour in ("red", "blue"):
        media[colour] = folder / f"{colour}.png"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=256x256",
             "-frames:v", "1", str(media[colour])])
    # 6-second test-pattern video with the speech as its sound track.
    media["video"] = folder / "clip.mp4"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=6",
         "-i", str(media["audio"]), "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(media["video"])])
    return media


def video_parts(video: Path, folder: Path, fps: float = 1.0) -> tuple[list[Path], Path]:
    frames_dir = folder / "frames"
    frames_dir.mkdir(exist_ok=True)
    run(["ffmpeg", "-y", "-i", str(video), "-vf", f"fps={fps}", str(frames_dir / "f%03d.png")])
    track = folder / "track.wav"
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(track)])
    return sorted(frames_dir.glob("f*.png")), track


def long_audio(source: Path, seconds: int, folder: Path) -> Path:
    out = folder / f"long_{seconds}.wav"
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(source), "-t", str(seconds),
         "-ac", "1", "-ar", "16000", str(out)])
    return out


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_server(client: OllamaClient, cfg) -> dict:
    out: dict = {"base_url": cfg.base_url, "version": client.version()}
    tags = client.tags()
    out["models"] = {}
    for name, role in cfg.roles.items():
        out["models"][name] = {"tag": role.model, "installed": role.model in tags,
                               "digest": tags.get(role.model, "")[:12]}
    other = OllamaClient("http://[::1]:11434", timeout_seconds=5)
    try:
        other_version = other.version()
    except OllamaError:
        other_version = None
    out["second_server_ipv6"] = other_version
    out["second_server_warning"] = (
        other_version is not None and cfg.base_url.startswith("http://127.0.0.1"))
    return out


def check_reading(client: OllamaClient, role) -> dict:
    extra = role.extra
    result = client.chat_json(
        role.model, [{"role": "user", "content": READING_PROMPT + PASSAGE}],
        schema=READING_SCHEMA, temperature=role.temperature,
        num_predict=int(extra.get("num_predict", 1024)), num_ctx=extra.get("num_ctx"))
    report = {
        "model": role.model, "seconds": round(result.seconds, 1),
        "prompt_tokens": result.prompt_tokens, "output_tokens": result.output_tokens,
        "done_reason": result.done_reason, "json_ok": isinstance(result.data, dict),
        "reply": result.data if result.data is not None else result.text[:500],
    }
    statements = (result.data or {}).get("statements") or [] if report["json_ok"] else []
    records = [{"unit_id": "u1", "text": PASSAGE}]
    located, fields_ok, stance_hits = 0, 0, 0
    for item in statements:
        if locate_quote(item.get("quote"), records, preferred_unit_id="u1"):
            located += 1
        if item.get("speaker") and item.get("stance"):
            fields_ok += 1
        speaker = str(item.get("speaker", "")).lower()
        for name, stance in EXPECTED.items():
            if name in speaker and item.get("stance") == stance:
                stance_hits += 1
    report.update(statements=len(statements), quotes_located=located,
                  speaker_and_stance_filled=fields_ok, expected_stances_matched=stance_hits)
    report["ok"] = (report["json_ok"] and not result.truncated and len(statements) >= 2
                    and located == len(statements) and fields_ok == len(statements))
    return report


def check_embedding(client: OllamaClient, role, media: dict[str, Path], folder: Path,
                    limits: bool) -> dict:
    model, dims = role.model, role.dimensions
    report: dict = {"model": model, "expected_dimensions": dims}

    def one(item) -> tuple[list[float], float]:
        res = client.embed(model, [item], keep_alive="2m")
        return res.vectors[0], res.seconds

    # Text
    texts = client.embed(model, [TEXT_MATCH, TEXT_OTHER, "a plain red image",
                                 "a plain blue image"], keep_alive="2m")
    t_match, t_other, t_red, t_blue = texts.vectors
    report["text"] = {"ok": len(t_match) == dims, "dimensions": len(t_match),
                      "seconds_for_4": round(texts.seconds, 2)}

    # Image: media must change the vector, and colour should match its caption.
    v_red, s_red = one(media_input(image=media["red"].read_bytes()))
    v_blue, _ = one(media_input(image=media["blue"].read_bytes()))
    report["image"] = {
        "ok": len(v_red) == dims and cosine(v_red, v_blue) < 0.999,
        "dimensions": len(v_red), "seconds": round(s_red, 2),
        "cos_red_vs_blue_image": round(cosine(v_red, v_blue), 4),
        "cos_red_image_to_red_text": round(cosine(v_red, t_red), 4),
        "cos_red_image_to_blue_text": round(cosine(v_red, t_blue), 4),
    }
    report["image"]["caption_ranking_ok"] = (
        report["image"]["cos_red_image_to_red_text"] > report["image"]["cos_red_image_to_blue_text"])

    # Audio: spoken sentence should sit closer to its own text than to an unrelated one.
    v_audio, s_audio = one(media_input(audio=media["audio"].read_bytes()))
    report["audio"] = {
        "ok": len(v_audio) == dims, "dimensions": len(v_audio), "seconds": round(s_audio, 2),
        "clip_seconds": 5,
        "cos_to_matching_text": round(cosine(v_audio, t_match), 4),
        "cos_to_unrelated_text": round(cosine(v_audio, t_other), 4),
    }
    report["audio"]["text_ranking_ok"] = (
        report["audio"]["cos_to_matching_text"] > report["audio"]["cos_to_unrelated_text"])

    # Video: try it directly; fall back to frames + audio track.
    video_b64 = __import__("base64").b64encode(media["video"].read_bytes()).decode()
    direct: dict = {}
    try:
        res = client.embed(model, [{"video": video_b64}], keep_alive="2m")
        vec = res.vectors[0]
        empty = client.embed(model, [""], keep_alive="2m").vectors[0]
        direct = {"accepted": True, "dimensions": len(vec),
                  "same_as_empty_text": cosine(vec, empty) > 0.999}
    except OllamaError as exc:
        direct = {"accepted": False, "error": str(exc)[:200]}
    frames, track = video_parts(media["video"], folder)
    started = time.monotonic()
    frame_vecs = client.embed(model, [media_input(image=f.read_bytes()) for f in frames],
                              keep_alive="2m").vectors
    track_vec = client.embed(model, [media_input(audio=track.read_bytes())],
                             keep_alive="2m").vectors[0]
    report["video"] = {
        "direct": direct,
        "frames_plus_audio": {
            "ok": all(len(v) == dims for v in frame_vecs) and len(track_vec) == dims,
            "frames": len(frame_vecs), "fps": 1.0,
            "seconds": round(time.monotonic() - started, 2),
            "cos_track_to_speech_clip": round(cosine(track_vec, v_audio), 4),
        },
    }
    report["video"]["ok"] = report["video"]["frames_plus_audio"]["ok"]

    # Audio length limit (truncate=False makes an over-long clip an error).
    if limits:
        probes = []
        for seconds in (60, 300, 330, 400):
            path = long_audio(media["audio"], seconds, folder)
            try:
                _, took = one(media_input(audio=path.read_bytes()))
                probes.append({"seconds": seconds, "ok": True, "took": round(took, 1)})
            except OllamaError as exc:
                probes.append({"seconds": seconds, "ok": False, "error": str(exc)[:160]})
        report["audio_length_probe"] = probes

    client.unload(model)
    report["ok"] = all(report[k]["ok"] for k in ("text", "image", "audio", "video"))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-reading", action="store_true")
    parser.add_argument("--skip-audit", action="store_true")
    parser.add_argument("--skip-embedding", action="store_true")
    parser.add_argument("--no-limits", action="store_true", help="skip the audio length probe")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "model_check")
    args = parser.parse_args(argv)

    cfg = load_models_config()
    client = OllamaClient(cfg.base_url, cfg.timeout_seconds)
    report: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "host": platform.node(), "config": str(cfg.path)}
    try:
        report["server"] = check_server(client, cfg)
    except OllamaError as exc:
        print(f"FAIL server: {exc}")
        return 2
    models = report["server"]["models"]
    absent = [m["tag"] for name, m in models.items() if name.startswith("trial_") and not m["installed"]]
    if absent:  # trial roles keep their settings on record after the model is removed (or was never local)
        print(f"WARN trial models not installed: {', '.join(absent)}")
    missing = [m["tag"] for name, m in models.items() if not name.startswith("trial_") and not m["installed"]]
    if missing:
        print(f"FAIL not installed: {', '.join(missing)}")
        return 2
    if report["server"]["second_server_warning"]:
        print(f"WARN a second Ollama server ({report['server']['second_server_ipv6']}) "
              "answers on [::1]:11434")

    checks: dict = {}
    for role_name, skip in (("reading", args.skip_reading), ("audit", args.skip_audit)):
        if skip:
            continue
        print(f"... {role_name}: {cfg.model_for(role_name)}", flush=True)
        try:
            checks[role_name] = check_reading(client, cfg.role(role_name))
        except OllamaError as exc:
            checks[role_name] = {"ok": False, "error": str(exc)}
    if not args.skip_embedding:
        print(f"... embedding: {cfg.model_for('embedding')}", flush=True)
        with tempfile.TemporaryDirectory(prefix="sogice-modelcheck-") as tmp:
            folder = Path(tmp)
            try:
                checks["embedding"] = check_embedding(
                    client, cfg.role("embedding"), make_media(folder), folder, not args.no_limits)
            except OllamaError as exc:
                checks["embedding"] = {"ok": False, "error": str(exc)}
    report["checks"] = checks
    report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report["loaded_after"] = client.loaded()

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"check-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, result in checks.items():
        print(f"{'PASS' if result.get('ok') else 'FAIL'} {name}")
    print(f"report: {path}")
    return 0 if checks and all(r.get("ok") for r in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
