"""파이프라인 전 구간의 데이터 계약(스키마).

funnel 구조(docs/design.md):
  ① 1차 retrieve (recall 넓게) → RetrieveResult(후보 번호 + 근거)
  ② 2차 classify (precision, LLM) → ClassificationResult(▼h 후보 + 에스컬레이션)

retrieve는 두 구현(retrieve_token / retrieve_embed)이 모두 이 RetrieveResult를 반환한다.
내부 방식(토큰↔임베딩)이 달라도 계약은 동일 → classify/pipeline은 불변.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ── ① 입력 ────────────────────────────────────────────────
class BookInput(BaseModel):
    """분류 대상 신규 도서의 서지 정보."""

    title: str
    subtitle: str | None = None
    author: str | None = None
    publisher: str | None = None
    pub_year: int | None = None

    is_translation: bool = False
    original_title: str | None = None
    original_lang: str | None = None

    # 082: 업체 제공 DDC. 선택 입력(prior).
    ddc_082: str | None = None

    keywords: list[str] = Field(default_factory=list)
    toc: list[str] = Field(default_factory=list, description="목차")
    description: str | None = Field(default=None, description="책 소개")


# ── ② 1차 retrieve 산출물 ─────────────────────────────────
class CandidateNumber(BaseModel):
    """후보 청구기호(▼h) 하나 + 근거.

    1차가 제목 매칭/임베딩으로 건진 뒤, 각 후보 번호대의 DB 책 샘플을 붙인다.
    2차(LLM)는 shelf_sample로 '주제'를 파악하고, 개수는 보조로만 쓴다.
    """

    ddc_h: str
    is_082_prior: bool = Field(default=False, description="082 업체번호에서 온 후보인가")
    title_hit_count: int = Field(default=0, description="제목 매칭이 이 번호를 가리킨 횟수")
    shelf_count: int = Field(
        default=0, description="DB에서 이 번호대 책 수 (상한 샘플이라 부정확할 수 있음)"
    )
    title_hits: list[str] = Field(
        default_factory=list, description="이 번호를 가리킨(제목 매칭된) 책 제목"
    )
    shelf_sample: list[str] = Field(
        default_factory=list, description="이 번호대 대표 책 제목 (주제 파악용)"
    )


class RetrieveResult(BaseModel):
    """1차 검색기 산출물 (retrieve_token / retrieve_embed 공통 계약)."""

    retriever: str = Field(description="token | embed")
    query_terms: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list, description="082 라우팅 멘트(XAI)")
    total_title_hits: int = 0
    candidates: list[CandidateNumber] = Field(default_factory=list)


# ── ③ 2차 classify 산출물 ─────────────────────────────────
class CandidateSource(str, Enum):
    KEPT_082 = "082_kept"          # 082를 그대로 채택
    REJECTED_082 = "082_rejected"  # 082 기각 후 재선택
    TITLE_MATCH = "title_match"    # 제목 매칭 후보에서
    INHERITED = "inherited"        # 기존 서지/원서 승계(번역서 등)


class Candidate(BaseModel):
    """▼h 후보 하나 + 판단 근거(XAI)."""

    h: str = Field(description="분류기호, 예: 720.2")
    label: str = Field(description="분류 항목명/의미, 예: 도시·건축·공간")
    confidence: float = Field(description="적합도 0~1")
    source: CandidateSource
    reasoning: str = Field(description="왜 이 번호인지 (주제 판단 우선 + 참고 근거)")
    similar_refs: list[str] = Field(default_factory=list)


class EscalationSignal(str, Enum):
    CROSS_MAJOR = "cross_major"        # 성격 다른 대분류를 넘나듦
    NEEDS_TOC = "needs_toc"            # 메타만으론 부족, 목차/책소개 필요
    SCATTERED_DIST = "scattered_dist"  # 후보가 흩어져 수렴 안 함
    ORG_DISAGREE = "org_disagree"      # 기관 간 분류 상이


class ClassificationResult(BaseModel):
    """2차 판단 산출물: ▼h 후보 + 에스컬레이션."""

    candidates: list[Candidate] = Field(description="적합도 내림차순 후보")
    ambiguity: float = Field(default=0.0, description="0(명확)~1(모호)")
    escalate: bool = Field(default=False, description="HITL 필요 여부")
    signals: list[EscalationSignal] = Field(default_factory=list)
    notes: str | None = None
