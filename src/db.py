"""저장소 접근의 유일한 창구.

**불변식:** 내부는 pandas지만 DataFrame은 이 파일 밖으로 나가지 않는다.
밖으로는 `schema.py`의 타입(CollectionBook / DistBucket)만 내보낸다.
→ 나중에 SQLite로 갈아끼워도 부르는 쪽 코드는 그대로다.
  (retrieve_token/retrieve_embed가 RetrieveResult 하나를 공유하는 것과 같은 패턴)

왜 pandas인가 (docs/design.md §7 결정):
  - 1682행 ≈ 1MB. 메모리에 다 올라간다 → 인덱스·디스크 상주가 필요 없음.
  - 필드가 책마다 다르다(상세페이지 라벨 23종, 3~98%) → 고정 컬럼 테이블과 안 맞음.
  - 실질 쓸모는 둘뿐: ① 분포 집계 groupby ② Streamlit 표 그리기.

담는 것 = 사서 업무 3단계 (docs/design.md §2.2):
  1. 원제 검색      → find_by_original_title()
  2. 청구기호 검색   → find_by_callno()
  3. 제목 검색·분포  → title_distribution()
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import pandas as pd

from config import COLLECTION_PATH, MAX_CANDIDATE_NUMBERS, SHELF_SAMPLE
from schema import CollectionBook, DistBucket
from tokens import tokens as _tokens  # 토큰 규칙은 tokens.py 한 곳 (retrieve_token과 공유)
from tokens import MIN_OVERLAP


@lru_cache(maxsize=1)
def _df() -> pd.DataFrame:
    """collection.json → DataFrame. 원본은 어디까지나 JSON(크롤러가 쓰고 git에 남는다)."""
    with open(COLLECTION_PATH, encoding="utf-8") as f:
        rows = json.load(f)
    df = pd.DataFrame(rows)
    for col in ("title", "author", "publisher", "pub_year", "call_number",
                "ddc_h", "searched_callno"):
        df[col] = df[col].fillna("").astype(str) if col in df else ""
    # detail: 상세크롤 전에는 없는 컬럼 → 빈 dict로 채워 계약을 맞춘다.
    df["detail"] = (
        df["detail"].apply(lambda d: d if isinstance(d, dict) else {})
        if "detail" in df
        else [{} for _ in range(len(df))]
    )
    return df


def _to_books(df: pd.DataFrame) -> list[CollectionBook]:
    """DataFrame → 스키마 타입. **pandas가 밖으로 새는 걸 막는 유일한 지점.**"""
    cols = ["id", "title", "author", "publisher", "pub_year",
            "call_number", "ddc_h", "searched_callno", "detail"]
    return [CollectionBook(**{k: r[k] for k in cols}) for _, r in df.iterrows()]


def stats() -> dict:
    """DB 한눈 요약 (화면 상단 표시용)."""
    df = _df()
    return {
        "총 장서": len(df),
        "청구기호 종류": df["ddc_h"].nunique(),
        "상세정보 보유": int(df["detail"].apply(bool).sum()),
    }


# ── 1단계: 원제 검색 ──────────────────────────────────────
def find_by_original_title(q: str) -> list[CollectionBook]:
    """원제(원서명)로 기존 서지를 찾는다. 있으면 그 청구기호를 **그대로 승계**(결정론적).

    ⚠️ 현재 한계: `detail.원서명`은 **상세페이지 크롤 이후에만** 존재한다.
    그 전에는 제목 부분일치로만 찾으므로, 이 단계는 아직 반쪽이다.
    (상세크롤 = crawler/detail_crawler.py 예정)
    """
    if not q.strip():
        return []
    df = _df()
    orig = df["detail"].apply(lambda d: d.get("원서명", "") or d.get("기타표제", ""))
    hit = orig.str.contains(re.escape(q), case=False, na=False)
    hit |= df["title"].str.contains(re.escape(q), case=False, na=False)
    return _to_books(df[hit])


# ── 2단계: 청구기호 검색 ──────────────────────────────────
def find_by_callno(ddc_h: str, limit: int = SHELF_SAMPLE) -> list[CollectionBook]:
    """청구기호(▼h) 정확 일치 → 그 번호대에 실제로 꽂힌 책들.

    사서가 '082가 본교에서 뭘 의미하나'를 확인하는 단계. 책 제목들이 곧 그 번호의 '의미'.
    """
    if not ddc_h.strip():
        return []
    df = _df()
    return _to_books(df[df["ddc_h"] == ddc_h.strip()].head(limit))


def shelf_titles(ddc_h: str, limit: int = SHELF_SAMPLE) -> list[str]:
    df = _df()
    return df.loc[df["ddc_h"] == ddc_h, "title"].head(limit).tolist()


def shelf_count(ddc_h: str) -> int:
    """⚠️ 크롤이 번호당 50건에서 잘려 있어 **부정확**하다(data/crawl_coverage.json)."""
    df = _df()
    return int((df["ddc_h"] == ddc_h).sum())


# ── 3단계: 제목 검색 → 청구기호 분포 ──────────────────────
def title_distribution(q: str, top: int = MAX_CANDIDATE_NUMBERS,
                       keywords: list[str] | None = None,
                       ddc_082: str | None = None,
                       prior_rejected: bool = False,
                       min_overlap: int = MIN_OVERLAP,
                       ) -> tuple[list[DistBucket], int]:
    """제목이 비슷한 책들을 건져 **청구기호별로 센다** → '720.2가 6권, 711.4가 1권'.

    **`retrieve_token`과 동작을 맞춘다** (토큰 규칙·082 강제 포함·정렬·상한 전부):
      - 토큰 매칭은 `tokens.py` 공유
      - `ddc_082`가 있으면 제목 매칭이 0건이어도 후보에 **강제 포함**(prior는 항상 판단 대상)
      - 정렬: 082 우선 → 매칭 수 내림차순 / 상한: `MAX_CANDIDATE_NUMBERS`

    `prior_rejected=True`(사서가 2단계에서 082를 기각함)이면 후보에는 **남기되
    맨 앞 우선권은 뺀다.** 이미 탈락한 번호를 1순위 자리에 다시 앉히지 않기 위해서다.
    근거를 남기고 사서의 기각을 LLM이 반박할 여지도 남기려고 후보에서 지우지는 않는다
    (docs/design.md §5 단일 정답 강제 금지).

    `min_overlap`: 제목 토큰이 몇 개 겹쳐야 건지나. 기본은 `config.MIN_OVERLAP`(=2, 평가와 동일).
    실측상 **제목만 입력하면 2로는 대부분 0건**이 나온다(「가장 인간적인 도시」 0권,
    1로 낮추면 16권/6종). 화면에서 사서가 낮춰 넓게 훑을 수 있게 인자로 뺐다.
    design.md §2.4 "1차는 recall 우선, threshold를 칼같이 정하지 않는다"와 정합.

    반환: (상위 top개 버킷, 매칭된 총 책 수)
    ⚠️ 순위는 '개수'로 매기지만, 최종 판단은 개수가 아니라 **주제**로 한다(design.md §2.4).
       개수는 (a) 50건 상한 샘플이라 부정확하고 (b) 최다가 정답이 아닌 케이스가 있다.
    """
    qt = _tokens(q)
    for k in keywords or []:
        qt |= _tokens(k)

    df = _df()
    if qt:
        hit = df["title"].apply(lambda t: len(qt & _tokens(t)) >= min_overlap)
        hits = df[hit]
    else:
        hits = df.iloc[0:0]

    buckets: list[DistBucket] = []
    for ddc, grp in hits.groupby("ddc_h"):
        buckets.append(
            DistBucket(
                ddc_h=str(ddc), count=len(grp), books=_to_books(grp),
                shelf_sample=shelf_titles(str(ddc)), shelf_count=shelf_count(str(ddc)),
            )
        )

    # 082 강제 포함 — 제목 매칭이 안 가리켜도 유지/기각 판단 대상으로 올린다.
    prior = (ddc_082 or "").strip()
    if prior and not any(b.ddc_h == prior for b in buckets):
        buckets.append(DistBucket(
            ddc_h=prior, count=0, books=[],
            shelf_sample=shelf_titles(prior), shelf_count=shelf_count(prior),
        ))

    # 정렬·상한도 retrieve_token과 동일하게.
    # 단 사서가 이미 기각한 082는 맨 앞 우선권을 뺀다(개수순으로 밀림).
    prior_first = bool(prior) and not prior_rejected
    buckets.sort(key=lambda b: (prior_first and b.ddc_h == prior, b.count), reverse=True)
    return buckets[:top], len(hits)


# ── 화면 표시용 (Streamlit이 표로 그린다) ─────────────────
def books_to_table(books: list[CollectionBook]) -> pd.DataFrame:
    """사람이 보는 표. 여기서만 DataFrame을 만들어 넘긴다(표시 전용, 조회 아님)."""
    return pd.DataFrame(
        [
            {
                "제목": b.title,
                "저자": b.author,
                "청구기호": b.call_number,
                "분류기호(▼h)": b.ddc_h,
                "출판년": b.pub_year,
                "주제명": b.detail.get("일반주제명", ""),
            }
            for b in books
        ]
    )
