from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class TextToolRequest(BaseModel):
    text: str


@router.post("/split")
async def split_text(req: TextToolRequest):
    parts = [x.strip() for x in re.split(r"(?<=[。！？!?；;])\s*", req.text) if x.strip()]
    return {"segments": parts}


@router.post("/clean")
async def clean_text(req: TextToolRequest):
    text = re.sub(r"\s+", " ", req.text).strip()
    text = text.replace("，,", "，").replace("。。", "。")
    return {"text": text}


@router.post("/normalize-numbers")
async def normalize_numbers(req: TextToolRequest):
    return {"text": re.sub(r"\d+", lambda m: f"[num:{m.group(0)}]", req.text)}

