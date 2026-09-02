"""파이프라인 배선 — **3단계 누적 + 독립 fit**.

  ① 알라딘 보강           빈 칸만 (책소개·원제·부제)                     LLM 0회
  ② 0차 승계 검색         기존 판본이 있으면 그 청구기호를 그대로.        LLM 0회

  【1단계】 082 하나
      fit ×1              082 서가 → shelf_fit. **게이트는 없다**(2026-09-02 제거 —
                          단일 서가 점수는 082의 정오와 상관이 약해 값이 존재하지 않았다.
                          config.py THRESHOLD_1 주석). 082는 최초 후보일 뿐이다.

  【2단계】 082 + 종합서지 득표 3위
      fit ×N              **새로 생긴 후보에만.** 082 점수는 1단계 것을 그대로 쓴다.
      게이트              여기까지로 이미 수렴이면 종결(decide.STAGE2_GATE, 기본 on).
                          최종 판정과 **같은 두 조건**을 쓴다 — 별도 문턱이 없다.

  【3단계】 082 + 종합서지 + 키워드
      키워드 추출 ×1      책 정보만 본다 (서가를 보여주면 그 어휘로 물든다)
      fit ×N              역시 새 후보에만
      판정                decide.decide() — 파일럿은 전부 사서 검토, 문턱은 참고로 기록

**같은 서가는 전 과정에서 딱 한 번 평가된다.** 예전 배선에서는 082가 1차·중간·최종
세 번 프롬프트에 들어가 매번 다른 점수를 받았다 — 그래서 "이 책과 082 서가의 적합도"라고
말할 수 있는 값이 없었고 문턱을 재산정할 수 없었다(devlog 2026-08-18).

한 단계 안의 fit 호출은 서로를 안 보므로 **동시에** 던진다(config.FIT_CONCURRENCY).
단계 사이는 순차다 — 앞 단계 결과로 다음 단계 진입을 정하기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import aladin
import decide
import fit
import keywords as _keywords
import llm
import retrieve
import sogang_db
from schema import (BookInput, CandidateDecision, KeywordExtraction,
                    RetrieveResult, ShelfFitAssessment)


@dataclass
class PipelineOutput:
    retrieve: RetrieveResult                    # 후보와 그 근거 (082/종합서지/키워드)
    decision: CandidateDecision                 # 정렬된 점수 벡터 + 코드가 정한 결론
    inherited: bool = False                     # 0차 승계로 끝났나 (LLM 0회)
    prior: ShelfFitAssessment | None = None     # 082 후보의 fit (082가 없거나 본교 0권이면 None)
    keywords: KeywordExtraction | None = None   # 3단계까지 갔을 때만
    went_keyword: bool = False                  # 3단계를 탔나
    trace: list = field(default_factory=list)   # LLM 원물 — 프롬프트 전문·출력·토큰·지연


def _inheritance_result(book: BookInput, rec) -> PipelineOutput:
    """0차 승계 산출물을 downstream과 같은 타입으로 합성(LLM 호출 없음).

    점수 1.0은 LLM이 매긴 게 아니라 **결정론적 규칙의 표시**다. 기존 판본이 있으면
    주제를 다시 추론할 이유가 없다.
    """
    a = ShelfFitAssessment(
        h=rec.ddc_h, shelf_label="기존 판본·원서 승계", shelf_fit=1.0,
        fit_reasoning=(f"본교에 기존 판본/원서 「{rec.title}」"
                       f"(청구기호 {rec.call_number or rec.ddc_h})가 있어 그 청구기호를 승계합니다. "
                       f"결정론적 규칙(원서/기존 서지 우선)이라 주제 재추론이 불필요합니다."),
        cited_books=[rec.title])
    r = RetrieveResult(
        retriever="inheritance",
        messages=[f"0차 승계: 원제/기존서지 검색 → 「{rec.title}」({rec.ddc_h}) 발견 → 승계."])
    d = CandidateDecision(
        assessments=[a], converged=True,
        reason="0차 승계는 결정론적 규칙이라 문턱을 대지 않습니다 — 사서가 볼 게 없습니다.")
    return PipelineOutput(retrieve=r, decision=d, inherited=True)


def classify_book(book: BookInput,
                  holdout: frozenset[str] | None = None) -> PipelineOutput:
    """신규 도서 한 건을 관통시켜 ▼h 후보와 점수를 만든다."""
    # ── ① 입력 보강 ──
    book = aladin.enrich(book)

    # ── ② 0차 승계 ──
    rec = sogang_db.find_inheritance(book, holdout=holdout)
    if rec is not None:
        return _inheritance_result(book, rec)

    scored: list[ShelfFitAssessment] = []

    # ── 【1단계】 082 ──
    prior_cand, msgs = retrieve.retrieve_prior(book, holdout)
    if prior_cand is not None:
        retrieve.add_descriptions([prior_cand], book.title)
    r = RetrieveResult(retriever="default", messages=msgs, prior_candidate=prior_cand)

    prior = fit.assess(book, prior_cand) if prior_cand is not None else None
    if prior is not None:
        scored.append(prior)
        r.messages.append(f"082({prior.h}) 서가 적합도 {prior.shelf_fit:.2f} — "
                          "게이트 없이 후보로만 씁니다. 판정은 전체 후보를 모은 뒤 합니다.")
    elif prior_cand is not None:
        r.messages.append(f"082({prior_cand.ddc_h})는 본교 0권이라 서가를 읽을 수 없어 "
                          "점수 없이 기록만 남깁니다.")

    # ── 【2단계】 + 종합서지 ──
    prior_h = prior_cand.ddc_h if prior_cand else ""
    union_cands, prior_votes, m = retrieve.retrieve_union(book, prior_h, holdout)
    # 082 번호가 종합서지에서도 나왔으면 — 키워드 중복(merge_candidates)과 같은 규칙으로
    # 발견 사실만 라벨·득표수로 남긴다. 독립 증거로는 안 세므로 후보·fit·판정은 그대로다.
    if prior_cand is not None and prior_votes:
        prior_cand.sources.add("종합서지")
        prior_cand.union_votes = prior_votes
    r.union_candidates = union_cands
    r.messages += m
    retrieve.add_descriptions(union_cands, book.title)
    scored += fit.assess_many(book, union_cands)      # 새 후보에만

    # ── 2단계 게이트 ──
    # 여기까지의 후보만으로 판정이 이미 수렴이면 키워드 단계를 건너뛴다.
    # **최종 판정과 같은 함수·같은 두 조건**이다(별도 문턱 없음) — 후보 집합만 다르다.
    # 확정은 어차피 사서가 하지만, "종합서지까지로 충분하다"는 판단으로
    # 키워드 LLM 호출과 군더더기 후보를 아낀다.
    d2 = decide.decide(scored)
    if decide.STAGE2_GATE and d2.converged:
        r.messages.append(
            f"2단계 종결: {d2.assessments[0].h}가 점수·격차 기준을 모두 넘어 "
            f"키워드 검색은 하지 않았습니다.")
        return PipelineOutput(retrieve=r, decision=d2, prior=prior,
                              went_keyword=False, trace=llm.take_trace())

    # ── 【3단계】 + 키워드 ──
    kw = _keywords.extract_keywords(book)
    known = ([prior_cand] if prior_cand else []) + union_cands
    exclude = {c.ddc_h for c in known}
    kcands, rows, min_hit, m = retrieve.retrieve_keyword(kw.keywords, exclude, holdout)
    # 이미 있는 번호가 키워드 검색에도 걸렸으면 후보를 새로 만들지 않고 라벨만 더한다.
    # (점수를 다시 매기지 않는다 — 같은 서가에 점수가 둘 생기면 안 된다)
    retrieve.merge_candidates(known, rows)
    r.keyword_candidates = kcands
    r.keyword_query = list(kw.keywords)
    r.keyword_hits_all = rows          # 전량 — 컷 재계산·재분류 큐의 재료
    r.keyword_min_hit = min_hit
    r.messages += m
    retrieve.add_descriptions(kcands, book.title)
    scored += fit.assess_many(book, kcands)           # 역시 새 후보에만

    # ── 판정 (LLM 없음) ──
    d = decide.decide(scored)
    return PipelineOutput(retrieve=r, decision=d, prior=prior, keywords=kw,
                          went_keyword=True, trace=llm.take_trace())
