"""V1 1차 검색기 — 제목 토큰 겹침 (recall 넓게).

funnel의 1차(recall): 입력의 제목/부제/키워드 토큰이 MIN_OVERLAP개 이상 겹치는 DB 책을
모두 건진다(top-k 컷 없음). 걸린 책들의 청구기호 = 후보 번호. 082가 있으면 prior로 포함.
각 후보 번호대의 DB 책 샘플(shelf_sample)을 붙여, 2차(LLM)가 '주제'를 판단하게 한다.

의도된 한계: 제목 글자가 안 겹치면 못 잡는다(예: "AI시대"≠"인공지능시대").
→ 이게 V2(retrieve_embed, 임베딩)와 비교하는 실험의 핵심. docs/design.md 참조.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache

from config import (
    COLLECTION_PATH,
    MAX_CANDIDATE_NUMBERS,
    MIN_OVERLAP,
    SHELF_SAMPLE,
)
from schema import BookInput, CandidateNumber, RetrieveResult
from tokens import tokens as _tokens  # 토큰 규칙은 tokens.py 한 곳 (db.py와 공유)


@lru_cache(maxsize=1)
def _load_db() -> list[dict]:
    with open(COLLECTION_PATH, encoding="utf-8") as f:
        return json.load(f)


def _query_terms(book: BookInput) -> set[str]:
    """입력 도서의 검색 용어. 제목+부제+키워드 (DB 책은 제목만 있으므로 비대칭)."""
    q = _tokens(book.title) | _tokens(book.subtitle or "")
    for k in book.keywords:
        q |= _tokens(k)
    return q


def retrieve(book: BookInput) -> RetrieveResult:
    db = _load_db()
    q = _query_terms(book)

    # ① 제목 매칭: 토큰이 MIN_OVERLAP개 이상 겹치는 책을 모두 건진다.
    hits_by_num: dict[str, list[str]] = defaultdict(list)
    total_hits = 0
    for b in db:
        if len(q & _tokens(b["title"])) >= MIN_OVERLAP:
            hits_by_num[b["ddc_h"]].append(b["title"])
            total_hits += 1

    # ② DB 전체를 번호대별로 묶어둔다 (후보 번호의 '의미' 샘플용).
    shelf: dict[str, list[str]] = defaultdict(list)
    for b in db:
        shelf[b["ddc_h"]].append(b["title"])

    # ③ 후보 번호 = 매칭이 가리킨 번호들 (+ 082 prior).
    prior = book.ddc_082
    candidate_ddcs = set(hits_by_num)
    if prior:
        candidate_ddcs.add(prior)

    cands: list[CandidateNumber] = []
    for ddc in candidate_ddcs:
        cands.append(
            CandidateNumber(
                ddc_h=ddc,
                is_082_prior=(ddc == prior),
                title_hit_count=len(hits_by_num.get(ddc, [])),
                shelf_count=len(shelf.get(ddc, [])),
                title_hits=hits_by_num.get(ddc, [])[:SHELF_SAMPLE],
                shelf_sample=shelf.get(ddc, [])[:SHELF_SAMPLE],
            )
        )

    # 정렬: 082 prior를 앞에, 그다음 제목매칭 많은 순. 상한 적용.
    cands.sort(key=lambda c: (c.is_082_prior, c.title_hit_count), reverse=True)
    cands = cands[:MAX_CANDIDATE_NUMBERS]

    messages = []
    if prior:
        in_db = "본교에 해당 번호대 책 있음" if shelf.get(prior) else "본교에 해당 번호대 책 없음"
        messages.append(f"082({prior}) 있음 → 청구기호 검색으로 의미 확인 ({in_db}).")
    else:
        messages.append("082 없음 → 책제목 검색으로 후보 번호를 찾습니다.")
    messages.append(f"제목 매칭 {total_hits}건 → 후보 번호 {len(candidate_ddcs)}개.")

    return RetrieveResult(
        retriever="token",
        query_terms=sorted(q),
        messages=messages,
        total_title_hits=total_hits,
        candidates=cands,
    )
