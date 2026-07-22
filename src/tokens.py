"""제목 토큰 매칭 규칙 — **한 곳**.

`retrieve_token`(평가 경로)과 `db`(데모 경로)가 이 파일 하나를 공유한다.
전에는 두 곳에 복사돼 있어, 한쪽만 고치면 두 경로가 조용히 어긋날 수 있었다.
(AGENTS.md "복제 금지" — classify/pipeline을 공유하는 것과 같은 이유)

의도된 한계: 글자가 안 겹치면 못 잡는다.
  - "AI시대" ≠ "인공지능시대"  (동의어 불가)
  - "건축가가" ≠ "건축"         (한국어 조사가 붙어 다른 토큰이 됨)
→ 이게 V2(retrieve_embed, 임베딩)와 비교하는 실험의 핵심. docs/design.md §7.
"""

from __future__ import annotations

import re

from config import MIN_OVERLAP  # noqa: F401  — 값의 주인은 config.py. 여기선 재수출만.

# 조사·관사 등 의미 없는 토큰
STOP = {"의", "를", "을", "이", "가", "은", "는", "와", "과", "에",
        "for", "the", "of", "a"}


def tokens(text: str) -> set[str]:
    """문자열 → 토큰 집합. 한 글자와 STOP은 버린다."""
    return {
        t.lower()
        for t in re.findall(r"[0-9A-Za-z가-힣]+", text or "")
        if len(t) > 1 and t.lower() not in STOP
    }


def overlaps(query: set[str], title: str, min_overlap: int = MIN_OVERLAP) -> bool:
    """이 책 제목이 검색어와 충분히 겹치는가."""
    return len(query & tokens(title)) >= min_overlap
