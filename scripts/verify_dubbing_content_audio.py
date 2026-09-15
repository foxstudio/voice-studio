#!/usr/bin/env python3
"""Inspect raw audio transcription differences in a disposable model/data root.

The source service is read-only. This diagnostic is not final-cut acceptance
and never blocks importing or editing media. Model snapshots are not kept.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--model-directory", type=Path, required=True)
    parser.add_argument("--bad-result", action="append", default=[])
    parser.add_argument("--good-result", action="append", default=[])
    args = parser.parse_args()
    if not args.bad_result or not args.good_result:
        parser.error("Supply both known bad and known good results")
    model_source = args.model_directory.resolve(strict=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="voice-studio-content-audio-") as temporary:
        root = Path(temporary)
        for name in ("models", "voices", "outputs", "exports", "projects", "cache", "logs", "tmp"):
            path = root / name
            path.mkdir()
            os.environ[f"VOICE_STUDIO_{name.upper()}_DIR"] = str(path)
        os.environ.update({
            "VOICE_STUDIO_DATA_DIR": str(root),
            "VOICE_STUDIO_DB_PATH": str(root / "config" / "voice_studio.db"),
            "TMPDIR": str(root / "tmp"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        })
        # A real owned snapshot: never point a writable test tree at user models.
        destination = root / "models" / "qwen3-asr-mlx"
        if sys.platform == "darwin":
            subprocess.run(["cp", "-cR", str(model_source), str(destination)], check=True)
        else:
            shutil.copytree(model_source, destination)
        sys.path.insert(0, str(ROOT / "backend"))
        from app.domains.video_localization.dubbing_content_integrity import content_finding
        from app.domains.video_localization.dubbing_production import compare_transcripts
        from app.services.tts_content_verification import acquire_content_evidence

        api = args.api_url.rstrip("/")
        wanted = set(args.bad_result + args.good_result)
        histories = {}
        offset = 0
        while wanted - histories.keys():
            query = urllib.parse.urlencode({"project_id": args.project_id,
                                           "limit": 100, "offset": offset})
            with urllib.request.urlopen(f"{api}/history?{query}", timeout=30) as response:
                page = json.load(response)
            if not page:
                raise RuntimeError("Requested result was not found in project history")
            histories.update({item["result_id"]: item for item in page
                              if item["result_id"] in wanted})
            offset += len(page)
        outcomes = []
        for result_id, expected_block in [
            *((value, True) for value in args.bad_result),
            *((value, False) for value in args.good_result),
        ]:
            history = histories[result_id]
            if history.get("project_id") != args.project_id:
                raise RuntimeError("Result is outside the specified project")
            audio = root / f"{result_id}.wav"
            with urllib.request.urlopen(f"{api}/history/{result_id}/audio", timeout=60) as response:
                with audio.open("wb") as target:
                    shutil.copyfileobj(response, target)
            before = time.monotonic()
            evidence = acquire_content_evidence(audio)
            if evidence.status != "complete":
                raise RuntimeError(f"Content transcription unavailable: {evidence.error_code}")
            cached = acquire_content_evidence(audio, evidence)
            if cached != evidence:
                raise RuntimeError("Identical audio/config did not reuse content evidence")
            parameters = history.get("parameter_snapshot") or {}
            expected = parameters.get("text") or history["input_text"]
            comparison = compare_transcripts(expected, evidence.transcript,
                                            reference_text=parameters.get("ref_text") or "")
            finding = content_finding(comparison)
            differs = finding is not None
            row = {"result_id": result_id, "expected": expected,
                   "heard": evidence.transcript, "finding": finding,
                   "matched_expected_outcome": differs == expected_block,
                   "seconds": round(time.monotonic() - before, 3)}
            outcomes.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        success = all(row["matched_expected_outcome"] for row in outcomes)
        print(json.dumps({"passed": success, "cases": len(outcomes),
                          "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)
        return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
