"""파이프라인 설정. 모델·경로 등 한 곳에서 관리."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# .env는 lib_copilot/ 또는 상위(모노레포 루트 yozm_ai_agent/)에 있을 수 있다.
# 현재 위치에서 위로 거슬러 올라가며 첫 .env를 찾아 로드한다.
load_dotenv(find_dotenv(usecwd=True))

# LLM 모델 — 교체하려면 이 줄만 수정
MODEL = os.environ.get("LIBCOPILOT_MODEL", "claude-opus-4-8")

# classify 단계 최대 출력 토큰
MAX_TOKENS = 4000

# ── 1차 검색기 선택 (funnel) ──
# "token": 제목 토큰 겹침(V1) / "embed": 임베딩 의미 유사(V2)
RETRIEVER = os.environ.get("LIBCOPILOT_RETRIEVER", "token")

# 1차 필터 파라미터
MIN_OVERLAP = 2          # 제목 토큰이 이만큼 겹치면 후보로 (recall 넓게)
MAX_CANDIDATE_NUMBERS = 15  # 2차 LLM에 넘길 후보 번호 상한
SHELF_SAMPLE = 40        # 후보 번호대에서 LLM에 보여줄 책 수 (가진 만큼 다, 상한 40)

# 경로
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
COLLECTION_PATH = DATA_DIR / "collection.json"
SCENARIO_DIR = DATA_DIR / "scenarios"
OUTPUT_DIR = PROJECT_ROOT / "output"   # 실행 산출물(중간물 json + 분석 md)
RUNS_DIR = PROJECT_ROOT / "runs"       # (나중) 평가 정량 로그 jsonl
