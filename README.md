# lib_copilot

도서관 청구기호(DDC 852 ▼h) 부여를 돕는 **사서-AI 협업 에이전트** (Ver 1 개발 중).

신규 도서의 서지 정보를 입력하면 → 실제 사서 업무 순서대로 검색하고 → **▼h 후보 여러 개 + 판단 근거(XAI)**를 제시한다.
정답이 갈리는 케이스는 단일 답을 강제하지 않고 **사람 사서에게 넘긴다(HITL)**.

> 기반: 교수님의 [`sogang_renAIssance`](../../sogang_renAIssance) 설계를 참조하되, **실제 업무 프로세스**에 맞춰 다시 설계.
> 전체 설계 배경은 [`docs/design.md`](docs/design.md) 참고.

---

## 왜 이 문제인가

현재 청구기호(특히 852 ▼h)를 정하는 과정은 **repetitive하고 비일관적**이다.
- repetitive → **자동화**한다.
- 비일관적(사서 암묵지) → **일관된 로직**으로 대체하고, 사람은 판단이 갈리는 지점에만 개입한다.

→ 시간 단축 + 암묵지(tacit)를 분명한 규칙으로.

## 핵심 개념

### 1. 업무 프로세스 = 파이프라인
하/중 난이도 케이스의 공통 패턴은 파이프라인화된다:

```
082 확인 → 본교서지 청구기호 검색(의미 확인) → 종합서지 제목 검색(분포 확인) → 필요시 키워드 검색 → 확정
```

082(업체 DDC)는 **선택 입력(prior)**. 있으면 청구기호 검색으로, 없으면 제목 검색으로 진입한다.
에이전트는 이 경로를 **말로 설명하며** 진행한다(그 자체가 XAI).

### 2. 난이도 = 라벨이 아니라 "에스컬레이션 신호의 강도"
하/중/상은 딱 나뉘는 정답 라벨이 아니다. 시스템은 난이도를 *입력으로 받지 않고*, **검색 결과의 모양을 보고 스스로 판정**한다.
아래 신호가 강하면 단일 정답을 강제하지 않고 후보+근거를 제시하며 HITL로 올린다:
- 후보가 성격 다른 **대분류를 넘나듦** (문학 800 vs 부동산 330 vs 성찰 170)
- 제목·메타만으론 부족 → **목차/책소개 필요**
- 제목 검색 **분포가 흩어짐**
- **기관 간 분류 상이** (교보·알라딘 등)

### 3. 시뮬레이션 전제
실제 도서관 시스템에 붙지 않는다. 고은님 근무 기록(케이스 분석 문서)을 **정답셋**으로,
"업무 중 마주치는 책들"을 모은 DB(`data/collection.json`)를 검색 대상으로 두고 시뮬레이션한다.

## 아키텍처 — funnel 2단계

```
collection.json (본교 장서 1682건)
   ① 1차 retrieve (recall 넓게)  → 후보 번호 + 각 번호대 샘플 책
   ② 2차 classify (LLM, precision) → ▼h 후보 + 근거 + 에스컬레이션 ★
```

1차는 **두 구현, 같은 계약** (내부만 다름 / 2차·배선 불변). `config.RETRIEVER`로 스위치하고 평가로 비교:

| 모듈 | 역할 | LLM? |
|------|------|------|
| [src/retrieve_token.py](src/retrieve_token.py) | 1차 V1: 제목 토큰 겹침 (recall) | ❌ |
| [src/retrieve_embed.py](src/retrieve_embed.py) | 1차 V2: 임베딩 의미 유사 (백엔드 결정 후) | ❌ |
| [src/classify.py](src/classify.py) | 2차: ▼h 판단 + 에스컬레이션 (**주제 우선·개수 보조**) | ✅ (유일) |
| [src/pipeline.py](src/pipeline.py) | 배선 + 검색기 스위치 | — |
| [eval/evaluate.py](eval/evaluate.py) | 골든셋 평가 (검색기별 비교) | — |

> 제목 유사도는 "후보를 넓게 건지는 그물"일 뿐, 정답 결정자가 아니다. 결정은 2차 LLM이 **주제**로. (자세히: [docs/design.md](docs/design.md))

## 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # ANTHROPIC_API_KEY 입력

python src/run.py                                  # 케이스 5 분류 (기본, output/에 덤프)
LIBCOPILOT_RETRIEVER=embed python src/run.py       # V2(임베딩)로 실행 (구현 후)
python eval/evaluate.py                             # 골든셋 평가 리포트

python crawler/callno_crawler.py                   # 청구기호 크롤 → data/ 갱신
```

## 개발 상태 (마일스톤)

- **M0~M2 완료** — 스키마·크롤 DB(1682건)·케이스5 골든셋·funnel 배선·classify(Claude) 관통.
- **다음:** V2(임베딩) 구현 후 V1과 비교 · 상 케이스 골든셋 확충 · shelf_sample 대표성 개선.

시행착오·의사결정은 [`docs/devlog.md`](docs/devlog.md), 실행 산출물은 `output/`에 기록한다.

## 구조

```
lib_copilot/
├── docs/          design.md(설계) · devlog.md(시행착오 로그)
├── data/          collection.json(DB) · scenarios/(골든셋)
├── src/           schema · config · route · retrieve · classify · pipeline · run
├── eval/          evaluate.py
└── runs/          평가 런 산출물(jsonl)
```
