from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import text_normalizer

router = APIRouter()


class TextToolRequest(BaseModel):
    text: str


@router.post("/split")
async def split_text(req: TextToolRequest):
    return {"segments": text_normalizer.split_sentences(req.text)}


@router.post("/clean")
async def clean_text(req: TextToolRequest):
    return {"text": text_normalizer.clean_text(req.text)}


@router.post("/normalize-numbers")
async def normalize_numbers(req: TextToolRequest):
    return {"text": text_normalizer.normalize_spoken_numbers(req.text)}
