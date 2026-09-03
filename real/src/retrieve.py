"""후보 모으기 — LLM 없이 검색만 한다. 세 채널.

**본교 조회가 언제나 마지막 근거다.** 종합서지도 키워드도 "어느 번호를 볼지"만 정해주고,
서가의 의미는 항상 본교(`sogang_db`)에서 읽는다.

  ① 082(업체번호)     `retrieve_prior()`    — 본교에서 그 번호대 의미 확인
  ② 종합서지 득표      `retrieve_union()`    — 타대학이 **이 책**에 매긴 번호 (직접 증거)
                                              082 번호는 제외(1단계에서 이미 판단 + 승계 가능성)
  ③ 키워드 검색       `retrieve_keyword()`  — 이 주제어가 걸린 자리 (간접 증거)
                                              ①②에 이미 있는 번호는 제외

세 채널이 같은 번호를 낼 수 있다. 그때 후보를 새로 만들지 않고 `sources`에 라벨만 더한다
(`merge_candidates`) — 같은 서가 40권을 프롬프트에 두 번 찍는 낭비를 막고,
"증거가 겹쳤다"는 사실은 라벨로 남는다.

**계약 동일:** 어느 채널이든 같은 `CandidateNumber`를 만든다. fit/pipeline은 불변.
"""

from __future__ import annotations

import aladin
import sogang_db
import union_db
from config import (KEYWORD_MIN_HIT, KEYWORD_SHELF,
                    MAX_KEYWORD_CANDIDATES, MAX_UNION_CANDIDATES,
                    SHELF_DESC_TOP, SHELF_SAMPLE)
from schema import BookInput, CandidateNumber


def _make(ddc: str, *, sources: set[str], votes: int = 0, hits: int = 0,
          limit: int = SHELF_SAMPLE,
          holdout: frozenset[str] | None = None) -> CandidateNumber:
    return CandidateNumber(
        ddc_h=ddc,
        is_082_prior="082" in sources,
        sources=set(sources),
        union_votes=votes,
        keyword_hits=hits,
        shelf_count=sogang_db.shelf_count(ddc, holdout=holdout),
        shelf_books=sogang_db.shelf_books(ddc, limit=limit, holdout=holdout),
    )


# ── ① 082 ────────────────────────────────────────────────
def retrieve_prior(book: BookInput,
                   holdout: frozenset[str] | None = None
                   ) -> tuple[CandidateNumber | None, list[str]]:
    """082 후보 하나. 종합서지는 아직 보지 않는다.

    (게이트는 2026-09-02에 없앴다 — 이 분리는 이제 "업체가 준 번호"라는 출처 구분과
    단계별 메시지를 위한 것이다. 모든 책이 3단계까지 간다.)
    """
    prior = (book.ddc_082 or "").strip()
    if not prior:
        return None, ["082 없음 → 종합서지·키워드로 후보를 찾습니다."]

    c = _make(prior, sources={"082"}, holdout=holdout)
    where = "본교에 해당 번호대 있음" if c.shelf_count else "본교에 해당 번호대 없음"
    return c, [f"082({prior}) 있음 → 본교에서 의미 확인 ({where})."]


# ── ② 종합서지 ────────────────────────────────────────────
def retrieve_union(book: BookInput, prior_h: str = "",
                   holdout: frozenset[str] | None = None
                   ) -> tuple[list[CandidateNumber], int, list[str]]:
    """타대학 득표 상위 N. `(후보, 082 번호의 득표수, 메시지)`.

    082 번호는 후보에서 뺀다 — 타대학이 업체 082를 그대로 승계했을 수 있어 독립 증거가
    아니다. 다만 "종합서지에서도 발견됐다"는 **사실까지 버리지는 않는다**: 그 득표수를
    함께 돌려주고, pipeline이 키워드 중복(merge_candidates)과 같은 규칙으로 082 후보의
    sources에 라벨만 더한다. 후보도 점수도 새로 생기지 않는다(2026-09-02).
    """
    votes, meta = union_db.voting(book.title)
    msgs: list[str] = []

    if not meta["matched_title"]:
        return [], 0, ["종합서지에서 이 책을 못 찾음 → 082/키워드만으로 판단."]
    prior_votes = votes.get(prior_h, 0) if prior_h else 0

    cands = [_make(ddc, sources={"종합서지"}, votes=votes[ddc], holdout=holdout)
             for ddc in votes if ddc != prior_h]

    # ⚠️ **본교에 0권인 번호는 뺀다**(2026-08-12). 서가를 못 읽는 번호는 LLM에게 판단할
    #    재료가 없다 — 프롬프트에 "(본교 미보유)" 한 줄만 들어가고 질문 자체가 성립 안 한다.
    #    12건 실측: 5개 케이스에 6개가 있었고 하나도 정답이 아니었다.
    dropped = [c.ddc_h for c in cands if c.shelf_count == 0]
    cands = [c for c in cands if c.shelf_count > 0]

    # ── 득표 상위 N위까지. **동점은 다 넣는다** ──
    # 딱 N개로 자르면 동점끼리 순서가 흔들릴 때 정답이 밀린다
    # (#11 부린왕자: 179.9(3)·811.36(1)·332.6324(1) — 정답이 3위인데 뒤 둘이 동점).
    cands.sort(key=lambda c: c.union_votes, reverse=True)
    before = {c.ddc_h for c in cands}
    if len(cands) > MAX_UNION_CANDIDATES:
        cut = cands[MAX_UNION_CANDIDATES - 1].union_votes
        cands = [c for c in cands if c.union_votes >= cut]
    rank_dropped = sorted(before - {c.ddc_h for c in cands})

    dist = ", ".join(f"{c.ddc_h}({c.union_votes})" for c in cands[:6]) or "(DDC 득표 없음)"
    msgs.append(
        f"종합서지 매칭: 「{meta['matched_title'][:30]}」 "
        f"· DDC {meta['ddc_libraries']}개 대학(서강 {meta['home_excluded']}개 제외) "
        f"· KDC {meta['kdc_libraries']}개(미집계).")
    if prior_votes:
        excl = (f" ※082({prior_h})도 {prior_votes}개 대학이 매김 — 업체 번호 승계 가능성이 "
                f"있어 독립 득표로는 안 세고 출처 라벨만 남깁니다.")
    elif prior_h:
        excl = f" ※082({prior_h})는 1단계에서 판단하므로 득표에서 제외."
    else:
        excl = ""
    msgs.append(f"DDC voting(순서 힌트): {dist}{excl}")
    if dropped:
        msgs.append(f"후보에서 뺀 번호: {', '.join(dropped)} — 본교에 0권이라 서가 의미를 "
                    f"읽을 수 없습니다.")
    if rank_dropped:
        msgs.append(f"득표 {MAX_UNION_CANDIDATES}위 밖이라 뺀 번호: {', '.join(rank_dropped)} "
                    f"(동점은 함께 남깁니다).")
    return cands, prior_votes, msgs


# ── ③ 키워드 ──────────────────────────────────────────────
def retrieve_keyword(kws: list[str], exclude: set[str],
                     holdout: frozenset[str] | None = None
                     ) -> tuple[list[CandidateNumber], list[tuple[str, int]], int, list[str]]:
    """검색어 → 후보 번호대. `(후보, 전량결과, 실제문턱, 메시지)`.

    **전량 결과를 함께 돌려준다.** 자른 뒤만 남기면 두 가지를 잃는다:
      ① "몇 위까지 잘라야 사서가 본 책이 들어오나"를 재실행 없이 못 쓸어본다
      ② 꼬리(1~2권짜리 외딴 번호)가 재분류 큐의 씨앗인데 그게 사라진다 (design.md §11.1)

    문턱(`KEYWORD_MIN_HIT`)으로 조여 검색한다. 예전엔 후보가 적으면 1개 이상으로 완화해
    재검색했는데 없앴다(2026-09-02) — 1개 매칭은 흔한 낱말 하나가 큰 서가를 통째로
    끌어오는 수준의 근거라(config 주석의 #7 실측), 후보가 모자랄 때만 눈금을 바꾸면
    "케이스마다 다른 기준으로 뽑힌 후보"가 섞인다. 0개면 0개인 채로 다음으로 간다.
    """
    msgs: list[str] = []
    # 포함 관계 검색어는 긴 쪽을 버린다(2026-09-03). 'Language acquisition' ⊂
    # 'Second language acquisition'을 둘 다 받으면, 긴 구절이 적힌 책은 그것만으로
    # 2-hit을 자동 충족해 min_hit이 그 단어에 한해 무력화된다 — #10 실측에서 이 이중
    # 카운트가 408.0071을 80권으로 부풀려 컷을 통째로 밀었다(devlog 9-03). 긴 쪽에
    # 걸리는 책은 짧은 쪽에도 걸리므로 검색 범위는 안 줄고 이중 카운트만 사라진다.
    # 프롬프트가 이미 금지하는 행동("부분일치로 개수를 채우지 않는다")의 코드 집행이다
    # — h를 코드가 찍고 cited_books를 코드가 대조하는 것과 같은 원칙(LLM 출력 검증).
    kws = list(dict.fromkeys(kws))          # 완전 중복도 같은 이중 카운트를 만든다
    dropped = [a for a in kws
               if any(b != a and b.casefold() in a.casefold() for b in kws)]
    if dropped:
        kws = [a for a in kws if a not in dropped]
        msgs.append(f"검색어 중복 제거: {', '.join(dropped)} — 남은 검색어에 부분일치로 "
                    f"포함돼 한 단어로 칩니다(겹침 개수 계산 왜곡 방지).")
    min_hit = KEYWORD_MIN_HIT
    rows = sogang_db.search_by_keywords(kws, min_hit=min_hit, holdout=holdout)
    fresh = [(h, n) for h, n in rows if h not in exclude]

    picked = fresh[:MAX_KEYWORD_CANDIDATES]
    cands = [_make(h, sources={"키워드검색"}, hits=n, limit=KEYWORD_SHELF,
                   holdout=holdout)
             for h, n in picked]

    msgs.insert(0, f"키워드 검색({', '.join(kws)}) · {min_hit}개 이상 매칭 "
                   f"· {len(rows)}개 번호대 중 {len(picked)}개 채택.")
    if picked:
        msgs.append("키워드 후보: " + ", ".join(f"{h}({n}권)" for h, n in picked))
    dup = [h for h, _ in rows[:MAX_KEYWORD_CANDIDATES + 3] if h in exclude]
    if dup:
        msgs.append(f"이미 후보인 번호라 새로 만들지 않음(출처 라벨만 추가): {', '.join(dup)}")
    return cands, rows, min_hit, msgs


# ── 병합 ──────────────────────────────────────────────────
def merge_candidates(base: list[CandidateNumber],
                     rows: list[tuple[str, int]]) -> None:
    """키워드 검색 결과 중 **이미 후보인 번호**의 출처 라벨을 갱신한다(제자리 수정).

    후보를 새로 만들지 않는다 — 같은 서가를 두 번 찍으면 프롬프트만 커지고 LLM도 혼란스럽다.
    대신 "종합서지에도 있고 키워드에도 걸렸다"는 **증거 중첩**을 라벨과 hits로 남긴다.
    """
    by_h = {c.ddc_h: c for c in base}
    for h, n in rows:
        c = by_h.get(h)
        if c is not None:
            c.sources.add("키워드검색")
            c.keyword_hits = n


def add_descriptions(cands: list[CandidateNumber], for_title: str) -> int:
    """후보 서가 책 중 ISBN 있는 책에 알라딘 책소개를 붙인다.

    후보를 다 모은 **뒤에 한 번에** 부른다 — 같은 책이 두 후보에 걸쳐 있을 때 중복 호출을
    피하려는 것이다. 못 받는 책은 빈 채로 둔다(양서·ISBN 없는 책이 많다).
    """
    if SHELF_DESC_TOP <= 0:
        return 0
    targets = [b for c in cands for b in c.shelf_books[:SHELF_DESC_TOP] if b.isbn]
    if not targets:
        return 0
    got = aladin.describe_many(targets, for_title)
    n = 0
    for b in targets:
        b.description = got.get(b.isbn, "") or ""
        n += bool(b.description)
    return n
