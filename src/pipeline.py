"""파이프라인 배선: 입력 → 1차 retrieve → 2차 classify.

1차 검색기는 config.RETRIEVER로 스위치(token / embed). 2차(classify)와 배선은 불변.
교수님 설계 원칙: 내부(검색 방식)를 갈아끼워도 계약(RetrieveResult)과 배선은 그대로.
"""

from __future__ import annotations

from dataclasses import dataclass

from classify import classify
from config import RETRIEVER
from schema import BookInput, ClassificationResult, RetrieveResult


def _get_retriever():
    """config.RETRIEVER에 따라 1차 검색기 함수를 고른다."""
    if RETRIEVER == "embed":
        from retrieve_embed import retrieve  # V2 (임베딩) — 백엔드 결정 후 구현
    else:
        from retrieve_token import retrieve   # V1 (토큰 겹침)
    return retrieve


@dataclass
class PipelineOutput:
    retrieve: RetrieveResult
    result: ClassificationResult


def classify_book(book: BookInput) -> PipelineOutput:
    """신규 도서 한 건을 끝까지 관통시켜 ▼h 후보를 만든다."""
    retrieve = _get_retriever()
    r = retrieve(book)          # ① 1차: 후보 번호 좁히기 (recall)
    res = classify(book, r)     # ② 2차: 주제 우선 판단 (precision, Claude)
    return PipelineOutput(retrieve=r, result=res)
