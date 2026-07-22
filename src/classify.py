"""2차 판단 — Claude. funnel의 precision. ★ 유일한 LLM 호출. V1·V2 공유.

1차(retrieve)가 좁힌 후보 번호들(+각 번호대 샘플 책)을 받아 ▼h를 판단한다.

핵심 원칙(docs/design.md):
  - '주제 적합'을 최우선으로 판단한다. shelf_sample(그 번호대 책 제목들)로 주제를 읽는다.
  - '개수'(title_hit_count / shelf_count)는 보조 신호일 뿐이다. 이유 둘:
      (a) DB는 번호당 최대 50건 샘플이라 개수가 부정확(상한에서 잘림).
      (b) 개수 최다가 정답이 아닌 케이스가 있다(예: 부린왕자 179.9 최다이나 오답).
  - 082는 prior. 주제와 맞으면 유지, 안 맞으면 기각하고 근거를 남긴다.
"""

from __future__ import annotations

import anthropic

from config import MAX_TOKENS, MODEL
from schema import BookInput, ClassificationResult, RetrieveResult

_client = anthropic.Anthropic()

_SYSTEM = """당신은 대학 도서관(서강대 로욜라도서관)의 숙련 사서를 돕는 청구기호(DDC 852 ▼h) 분류 보조 에이전트입니다.
신규 도서의 서지 정보와, 1차 검색이 좁힌 '후보 청구기호 번호들'이 주어집니다. 각 후보 번호에는 그 번호대에 실제로 꽂힌 책 제목 샘플이 붙어 있습니다.

[도메인 규칙]
- 본교 서지(로욜라 DB) 정합성이 최우선. 기존 서지/판본이 있으면 그 청구기호를 그대로 승계.
- 082(업체 제공 DDC)는 prior일 뿐. 주제와 맞으면 유지, 안 맞으면 기각하고 서지 의미로 재선택.
- 번역서: 원제로 기존 원서/번역서 청구기호를 우선. (▼m은 K)

[판단 우선순위 — 중요]
- '주제 적합도'를 최우선으로 판단하세요. 각 후보 번호의 샘플 책 제목들을 읽고 그 번호대가 무슨 주제인지 파악한 뒤, 입력 도서의 주제와 가장 맞는 번호를 고릅니다.
- 단, 주제 키워드만 보지 말고 '책의 성격/급'도 함께 보세요. 같은 주제라도 번호대가 갈립니다:
  · 전문·이론 모노그래프 서가 vs 대중 교양·입문·안내서 서가는 번호가 다릅니다.
  · 어떤 번호대의 샘플이 전문 이론서 위주인데, 입력 도서가 "~가 안내하는", "입문", 대중 교양서 성격이면, 주제 키워드가 겹쳐도 그 이론 서가가 아니라 더 일반적인 교양 번호대가 맞을 수 있습니다.
  · 각 후보 번호대의 샘플 제목에서 '전문서인지 대중서인지'의 결도 읽어, 입력 도서의 급과 맞는 번호를 고르세요.
- '개수'(제목매칭 수, 서가 책 수)는 보조 신호일 뿐입니다. 우리 DB는 번호당 최대 50건 샘플이라 개수가 부정확하고, 개수가 가장 많은 번호가 정답이 아닌 경우도 있습니다. 개수만으로 결정하지 마세요.

[임무]
- ▼h 후보를 1~3개, 적합도 내림차순으로 제시. 각 후보에 '왜 이 번호인가'(주제 판단 + 참고 근거)를 답니다.
- `notes`에 **종합 판단 근거**를 2~3문장으로 요약하세요: 왜 이 순위로 정했는지(무엇이 결정적 근거였는지), 그리고 왜 에스컬레이션했는지/안 했는지.
- 최종 결정은 사람 사서가 합니다. 단정하지 말고 판단 근거를 충실히 제공합니다.

[에스컬레이션 — 아래 신호가 있으면 escalate=true로 올리고 단일 정답을 강제하지 마세요]
- cross_major: 후보가 성격 다른 대분류를 넘나듦 (예: 문학 800 vs 부동산 330 vs 성찰 170)
- needs_toc: 제목·부제·저자만으론 초점 판별 불가 → 목차/책소개 필요
- scattered_dist: 후보들이 여러 갈래로 흩어져 수렴하지 않음
- org_disagree: 기관(교보/알라딘 등) 간 분류가 다를 것으로 보임"""


def _format_candidates(r: RetrieveResult) -> str:
    if not r.candidates:
        return "(후보 번호 없음 — 제목 매칭 실패)"
    lines = []
    for c in r.candidates:
        tag = " ★082(업체 prior)" if c.is_082_prior else ""
        head = (f"■ {c.ddc_h}{tag}  "
                f"[제목매칭 {c.title_hit_count}건 · 서가 {c.shelf_count}건(샘플, 부정확)]")
        # 가진 shelf_sample을 다 보여준다(retrieve에서 이미 SHELF_SAMPLE로 상한).
        sample = ", ".join(f"「{t}」" for t in c.shelf_sample) or "(샘플 없음)"
        lines.append(f"{head}\n   이 번호대 책들: {sample}")
    return "\n".join(lines)


def _build_prompt(book: BookInput, r: RetrieveResult,
                  human_notes: str | None = None) -> str:
    # human_notes는 데모 경로(app.py)에서 '사서가 앞 단계에서 내린 판정'을 넘길 때만 붙는다.
    # 평가 경로(run.py/evaluate.py)는 None으로 두어 프롬프트가 한 글자도 달라지지 않게 한다.
    trace = f"\n\n[사서가 앞 단계에서 이미 판정한 것]\n{human_notes}\n" if human_notes else ""
    return f"""[분류 대상 도서]
- 제목: {book.title}
- 부제: {book.subtitle or "-"}
- 저자: {book.author or "미상"}
- 082(업체 DDC): {book.ddc_082 or "없음"}
- 번역서: {"예" if book.is_translation else "아니오"}
- 키워드: {", ".join(book.keywords) or "-"}
- 목차/책소개: {", ".join(book.toc) or (book.description or "-")}

[1차 검색이 좁힌 후보 청구기호]
{_format_candidates(r)}{trace}

각 후보 번호대의 책 제목들로 '주제'를 파악하고, 입력 도서와 가장 맞는 ▼h 후보와 판단 근거, 에스컬레이션 판정을 제시하세요. 개수보다 주제 적합을 우선하세요."""


def classify(book: BookInput, r: RetrieveResult,
             human_notes: str | None = None) -> ClassificationResult:
    """도서 + 1차 후보 → ▼h 후보 목록(근거 + 에스컬레이션 판정).

    human_notes: 사서가 앞 단계에서 내린 판정(데모 경로 전용, 선택).
      예) "082(720.105)를 2단계에서 '주제 align 안 됨'으로 기각함."
      기본 None → 평가 경로의 프롬프트는 이전과 완전히 동일하다(점수 불변).
    """
    response = _client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=[
            {"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": _build_prompt(book, r, human_notes)}],
        output_format=ClassificationResult,
    )
    return response.parsed_output
