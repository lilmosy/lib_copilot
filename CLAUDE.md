# CLAUDE.md — lib_copilot

> 이 파일은 세션 시작 시 자동 로드된다. **상세 내용을 여기 복붙하지 말고**, 아래 문서로 안내만 한다.

## 한 줄 소개
도서관 청구기호(852 ▼h) 부여를 돕는 **사서-AI 협업 에이전트**. 실제 사서 업무를 funnel 파이프라인으로 이식하고, 판단이 갈리는 지점만 사람에게 넘긴다(HITL). 시나리오 골든셋으로 평가하며 고도화.

## 먼저 읽을 문서 (여기에 진짜 내용이 있다)
- **[docs/design.md](docs/design.md)** — 설계 정본. 시스템 개요(§2)·데이터 계약(§3)·모듈(§4)·평가/누출(§8). "왜 이렇게" 궁금하면 여기.
- **[docs/devlog.md](docs/devlog.md)** — 시행착오·의사결정 시간순 로그. "무슨 일이 있었나"는 여기.
- **[AGENTS.md](AGENTS.md)** — 코딩/작업 규약(불변식). 코드 손대기 전 필독.
- **[output/](output/)** — 케이스별 실행 분석(`*_analysis.md` = 한눈에 보기 + 시도별).

## 아키텍처 (한 줄)
`BookInput → ① retrieve(recall 넓게, token/embed 스위치) → ② classify(Claude, 주제·급 판단 + 에스컬레이션) → ▼h 후보`. LLM은 classify 한 곳만.

## 실행
```bash
cp .env.example .env      # ANTHROPIC_API_KEY (루트 .env도 자동 탐색)
python src/run.py                    # 케이스 실행 → output/ 덤프
python eval/evaluate.py              # 골든셋 평가 (Exact/Prefix/Top-k)
python crawler/callno_crawler.py     # 청구기호 크롤 → data/ 갱신
```

## 현재 상태 (2026-07-15)
- Ver1 funnel 관통 완료. DB = 청구기호 크롤 실데이터 **1682건**. 검색기는 **token**(V1)만 구현, embed(V2)는 자리만.
- 골든셋 3케이스: #3 향신료(하)·#5 인간적도시(중)·#11 부린왕자(상). 평가 Exact 33/Prefix 67/Top-k 67.

## 다음 할 일 & 주의
- **다음:** V2(임베딩) 구현·비교 · **홀드아웃**(누출 제거) · analyze(키워드 추출) 정식화 · shelf_sample 대표성.
- ⚠️ **데이터 누출:** DB를 같은 도서관에서 크롤해, 시나리오 입력 도서가 DB에 이미 있으면 정답째로 누출됨(케이스3). 공정 평가엔 홀드아웃 필요. (design.md §8)
- ⚠️ **골든셋 keywords는 수기로 넣은 것**(LLM/크롤 아님) → analyze 단계로 정식화 필요. (design.md §3.1, §7)
- ⚠️ 애매 케이스는 top pick이 **비결정적** → Exact 말고 Top-k+에스컬레이션으로 평가.

## 작업 후
- 시행착오는 `docs/devlog.md`에, 실행 분석은 `output/*_analysis.md`에 남긴다.
