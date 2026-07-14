# AGENTS.md — lib_copilot 작업 규약

AI 에이전트(및 사람)가 이 저장소에서 작업할 때 지키는 규칙. 설계 배경은 `docs/design.md`.

## 프로젝트 한 줄 요약
실제 사서의 청구기호(852 ▼h) 부여 업무를 파이프라인으로 옮기고, 판단이 갈리는 지점만 사람에게 넘기는(HITL) 협업 에이전트. 시나리오 골든셋으로 평가하며 고도화한다.

## 아키텍처 불변식 (깨지 말 것)
- **데이터 계약은 `src/schema.py` 한 곳.** 단계 간에는 스키마 타입만 주고받는다. 내부 구현을 바꿔도 배선(`pipeline.py`)은 불변.
- **funnel 2단계** (설계: design.md §2): ① 1차 `retrieve_*`(recall, 넓게) → `RetrieveResult` → ② 2차 `classify`(precision, LLM).
- **1차 검색기는 두 구현, 같은 계약.** `retrieve_token`(토큰)·`retrieve_embed`(임베딩) 모두 `retrieve(book) -> RetrieveResult`. **classify/pipeline은 공유(복제 금지)**. `config.RETRIEVER`로 스위치.
- **LLM 호출은 `classify.py` 한 곳에만.** 나머지는 순수 파이썬. 1차를 LLM으로 만들지 말 것(비용·scale) — 1차는 토큰/임베딩.
- **주제 우선 · 개수 보조.** classify 프롬프트는 shelf_sample로 주제를 판단하게 하고 개수는 보조로만. 이유: 개수는 상한 샘플이라 부정확 + 개수최다≠정답.
- **082는 선택 입력(prior).** 있으면 후보에 포함해 유지/기각을 LLM이 판단. 없다고 실패하지 않는다.
- **단일 정답을 강제하지 않는다.** 에스컬레이션 신호가 있으면 후보+근거를 제시하고 `escalate=true`.
- **중간산출물은 `output/`에 json 덤프**(사람이 검수). 단계 간 전달은 메모리 객체(파일 불필요).

## 코드 스타일
- Python 3.12, 타입힌트 사용, `from __future__ import annotations`.
- 검증 모델은 pydantic v2 (`BaseModel`).
- 주석/문서는 한국어. 기존 파일의 톤(간결한 설명 + 근거)을 따른다.
- 새 의존성은 `requirements.txt`에 추가하고 이유를 devlog에.

## 데이터 규약
- **정답셋:** `data/scenarios/<caseNN_slug>.json` — `input`(BookInput) / `expected`(정답) / `human_trace`(사서 실제 검색) 3블록.
  - `expected`는 `writer_final`(작성자 확정)과 `review_final`(교열 최종)을 **분리**해 담는다. 상 케이스는 둘이 다르다.
- **DB:** `data/collection.json` — "업무 중 마주치는 책들". 각 항목 최소 `title, call_number, ddc_h, category, keywords`.
  현재는 시드(수기). 이후 crawler 결과로 확장한다.
- 대용량 데이터/크롤 산출물은 커밋하지 않는다(.gitignore).

## 로깅 (두 층으로 분리)
- **`docs/devlog.md` (사람용):** 왜 바꿨나 — 프롬프트 수정, 유사도 기준 변경, 실패한 시도와 이유. 시간순 append.
- **`runs/<타임스탬프>.jsonl` (기계용):** 케이스별 예측/정답/신호. 정량 개선 추적용.
- ⚠️ 스크립트 내부에서 `Date.now()`류 시간 함수에 의존하지 말 것 — 타임스탬프는 호출부(shell)에서 주입.

## 평가 우선
- 코드를 바꾸면 `python eval/evaluate.py`로 골든셋 지표(Exact/Prefix/Top-k)를 확인한다.
- baseline("검색 분포 최빈 ▼h") 대비 LLM+규칙이 얼마나 기여하는지 항상 비교한다.

## 설계 질문 — 결정됨 (상세: design.md §5)
- 제목 유사도: funnel로 우회(1차 recall 넓게, 2차 LLM 판단). 방법은 토큰(V1)/임베딩(V2) 두 버전 → 평가로 결정.
- 주제 판단: 후보 번호대의 shelf_sample로. **개수 아니라 주제 우선.**
- 남은 것: 임베딩 백엔드(V2), shelf_sample 대표성(케이스5에서 편향 관찰), 상 케이스 골든셋 확충.

## 하지 말 것
- 시나리오 전제를 벗어난 임의 데이터로 정답을 만들지 않는다(정답은 케이스 분석 문서 기준).
- ▼i(도서기호)·▼m(부가기호)·852 조립은 Ver 1 스코프 밖. 지금 손대지 않는다.
