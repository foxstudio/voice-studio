from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
from funasr.models.campplus.model import CAMPPlus


def main() -> None:
    payload = json.loads(sys.stdin.read())
    model_path = Path(payload["model_path"])
    clips = [str(Path(item)) for item in payload.get("clips") or []]
    if not clips:
        print(json.dumps({"embeddings": []}))
        return

    model = CAMPPlus()
    weights = torch.load(model_path / "campplus_cn_common.bin", map_location="cpu", weights_only=True)
    model.load_state_dict(weights)
    model.eval()
    with torch.inference_mode():
        results, metadata = model.inference(clips, device="cpu", fs=16000)
        embeddings = torch.nn.functional.normalize(results[0]["spk_embedding"], dim=1)
    print(
        json.dumps(
            {
                "embeddings": embeddings.cpu().tolist(),
                "metadata": metadata,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
