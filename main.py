#!/usr/bin/env python3
"""여행 계획 CLI 프로그램 뼈대."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from google import genai

# 1차 추천 JSON에 반드시 있어야 하는 키
REQUIRED_KEYS = ("recommended_city", "weather", "events", "reason")
GEMINI_MODEL = "gemini-3.6-flash"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
RESULTS_DIR = Path("results")

# 모델이 따라야 할 응답 스키마 설명
JSON_SCHEMA_HINT = """{
  "recommended_city": "string, 예: 제주",
  "weather": "string, 해당 시기 일반적 날씨 요약",
  "events": ["string", "string"],
  "reason": "string, 추천 근거 2~4문장"
}"""


def parse_date(value: str) -> str:
    """--date 값이 YYYY-MM-DD 형식인지 검증한다."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다: '{value}' (YYYY-MM-DD)"
        )


def _require_gemini_api_key() -> str:
    """`.env`에서 GEMINI_API_KEY를 읽고, 없으면 설정 방법을 안내한 뒤 종료한다."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_gemini_api_key_here":
        print(
            "GEMINI_API_KEY가 설정되어 있지 않습니다.\n"
            "프로젝트 루트에 .env 파일을 만들고 다음처럼 키를 넣으세요:\n"
            "  GEMINI_API_KEY=발급받은_키\n"
            "키는 Google AI Studio에서 발급할 수 있습니다:\n"
            "  https://aistudio.google.com/apikey\n"
            "참고용 템플릿은 .env.example 을 보세요.",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def _build_recommendation_prompt(date: str) -> str:
    """날짜를 넣고 순수 JSON만 받도록 1차 추천 프롬프트를 만든다."""
    return f"""너는 한국 국내 여행 추천 도우미다.
여행 날짜: {date}

이 날짜(또는 그 시기)에 가기 좋은 한국 도시 하나를 추천하라.

반드시 아래 JSON 스키마와 동일한 키만 사용해 응답하라.
다른 설명, 인사, 마크다운, 코드블록(```) 없이 순수 JSON 텍스트만 출력하라.

{JSON_SCHEMA_HINT}
"""


def _parse_recommendation_json(text: str):
    """모델 응답 문자열을 JSON 딕셔너리로 파싱한다. 실패하면 None."""
    if not text:
        return None
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if any(key not in data for key in REQUIRED_KEYS):
        return None
    return data


def _generate_text(client: genai.Client, prompt: str) -> str:
    """Gemini에 텍스트를 보내고 응답 본문을 반환한다."""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return (response.text or "").strip()


def get_recommendation(date: str, errors: list | None = None):
    """1차 추천을 Gemini(LLM)로 생성하고, 파싱된 딕셔너리를 반환한다."""
    # .env에서 API 키를 읽고 없으면 즉시 종료
    api_key = _require_gemini_api_key()
    client = genai.Client(api_key=api_key)

    # 지정한 JSON 스키마로만 답하도록 요청
    prompt = _build_recommendation_prompt(date)
    raw = _generate_text(client, prompt)
    parsed = _parse_recommendation_json(raw)
    if parsed is not None:
        return parsed

    # 파싱 실패 시 필수 키만 JSON으로 다시 달라고 1회만 재요청
    retry_prompt = (
        "이전 응답이 유효한 JSON이 아니었다. "
        "필수 키만 다시 JSON으로 출력하라. "
        "설명이나 마크다운 코드블록 없이 순수 JSON 텍스트만 출력하라.\n\n"
        f"{JSON_SCHEMA_HINT}"
    )
    raw_retry = _generate_text(client, retry_prompt)
    parsed = _parse_recommendation_json(raw_retry)
    if parsed is not None:
        return parsed

    # 재요청도 실패하면 상위에서 errors에 남길 수 있도록 None 반환
    if errors is not None:
        errors.append("1차 추천 JSON 파싱에 실패했습니다.")
    return None


def _record_place_error(errors: list | None, error_type: str, message: str) -> list:
    """맛집 검색 오류를 errors에 남기고, 호출 측이 그대로 쓸 빈 리스트를 반환한다."""
    if errors is not None:
        errors.append(
            {
                "step": "place_search",
                "type": error_type,
                "message": message,
            }
        )
    return []


def _kakao_place_to_dict(place: dict) -> dict:
    """카카오 documents 항목을 내부 맛집 형식으로 변환한다."""
    road = (place.get("road_address_name") or "").strip()
    return {
        "name": place.get("place_name"),
        "address": road or place.get("address_name"),
        "category": place.get("category_name"),
        "url": place.get("place_url"),
        "x": place.get("x"),
        "y": place.get("y"),
    }


def search_restaurants(city: str, errors: list | None = None):
    """카카오 로컬 키워드 검색으로 맛집을 최대 5곳 조회한다."""
    # .env에서 카카오 REST API 키를 읽는다
    load_dotenv()
    api_key = os.getenv("KAKAO_REST_API_KEY", "").strip()
    if not api_key or api_key == "your_kakao_rest_api_key_here":
        return _record_place_error(
            errors,
            "AUTH_ERROR",
            "KAKAO_REST_API_KEY가 설정되어 있지 않습니다.",
        )

    if not city:
        return _record_place_error(
            errors,
            "EMPTY_RESULT",
            "도시명이 없어 맛집 검색을 건너뜁니다.",
        )

    query = f"{city} 맛집"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {"query": query, "size": 5}

    try:
        # 키워드 검색 API 호출 (최대 10초 대기)
        response = requests.get(
            KAKAO_KEYWORD_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
    except requests.Timeout:
        return _record_place_error(
            errors,
            "NETWORK_ERROR",
            "맛집 검색 요청이 시간 초과되었습니다.",
        )
    except requests.RequestException as exc:
        return _record_place_error(
            errors,
            "NETWORK_ERROR",
            f"맛집 검색 네트워크 오류: {exc}",
        )

    # 인증 실패여도 프로그램은 계속 진행한다
    if response.status_code in (401, 403):
        return _record_place_error(
            errors,
            "AUTH_ERROR",
            f"HTTP {response.status_code}",
        )

    if response.status_code != 200:
        return _record_place_error(
            errors,
            "NETWORK_ERROR",
            f"HTTP {response.status_code}",
        )

    try:
        payload = response.json()
    except ValueError:
        return _record_place_error(
            errors,
            "NETWORK_ERROR",
            "맛집 검색 응답 JSON을 파싱하지 못했습니다.",
        )

    documents = payload.get("documents") or []
    if not documents:
        return _record_place_error(
            errors,
            "EMPTY_RESULT",
            f"'{query}' 검색 결과가 0건입니다.",
        )

    # documents에서 최대 5개를 내부 형식으로 변환한다
    return [_kakao_place_to_dict(place) for place in documents[:5]]


def _format_error_line(item) -> str:
    """errors 항목을 리포트용 한 줄로 만든다."""
    if isinstance(item, dict):
        step = item.get("step", "")
        error_type = item.get("type", "")
        message = item.get("message", "")
        return f"- {step}: {error_type} — {message}".strip()
    return f"- {item}"


def _fallback_report(date: str, rec_json, restaurants, errors) -> str:
    """LLM 없이 rec_json/restaurants/errors로 최소 마크다운 리포트를 조립한다."""
    rec = rec_json if isinstance(rec_json, dict) else {}
    city = rec.get("recommended_city") or "데이터 없음"
    reason = rec.get("reason") or "데이터 없음"
    weather = rec.get("weather") or "데이터 없음"

    events = rec.get("events") or []
    if isinstance(events, list) and events:
        events_md = "\n".join(f"- {event}" for event in events)
    else:
        events_md = "데이터 없음"

    # 맛집이 없으면 섹션에 '데이터 없음'만 둔다
    if restaurants:
        restaurant_lines = []
        for place in restaurants:
            name = place.get("name") or ""
            category = place.get("category") or ""
            address = place.get("address") or ""
            url = place.get("url") or ""
            restaurant_lines.append(
                f"- {name} ({category}) — {address} {url}".strip()
            )
        restaurants_md = "\n".join(restaurant_lines)
    else:
        restaurants_md = "데이터 없음"

    # 오류가 없으면 섹션에 '없음'만 둔다
    if errors:
        errors_md = "\n".join(_format_error_line(item) for item in errors)
    else:
        errors_md = "없음"

    return f"""# {date} 국내 여행 추천 리포트

## 추천 지역
{city}

## 추천 이유
{reason}

## 날씨 요약
{weather}

## 행사/축제
{events_md}

## 맛집 추천
{restaurants_md}

## 1일 일정 제안 (오전/오후/저녁)
- 오전: {city} 주요 명소 둘러보기
- 오후: 지역 맛집·카페 탐방
- 저녁: 야경 감상 또는 휴식

## 오류 요약(errors)
{errors_md}
"""


def _build_report_prompt(date: str, rec_json, restaurants, errors) -> str:
    """1차 추천·맛집·오류를 요약해 마크다운 리포트 생성을 지시한다."""
    rec_summary = json.dumps(rec_json, ensure_ascii=False, indent=2) if rec_json else "없음"
    restaurants_summary = (
        json.dumps(restaurants, ensure_ascii=False, indent=2)
        if restaurants
        else "없음 (빈 리스트)"
    )
    errors_summary = (
        json.dumps(errors, ensure_ascii=False, indent=2) if errors else "없음 (빈 리스트)"
    )
    empty_restaurants_rule = (
        'restaurants가 비어 있으므로 "## 맛집 추천" 본문에는 반드시 "데이터 없음"이라고만 써라.'
        if not restaurants
        else "맛집 목록을 바탕으로 추천을 정리하라."
    )
    empty_errors_rule = (
        'errors가 비어 있으므로 "## 오류 요약(errors)" 본문에는 반드시 "없음"이라고만 써라.'
        if not errors
        else "errors 내용을 간결히 요약하라."
    )
    return f"""너는 국내 여행 리포트 작성기다. 아래 데이터를 바탕으로 마크다운 리포트만 출력하라.
앞뒤 설명이나 코드블록(```) 없이 마크다운 본문만 출력하라.

여행 날짜: {date}

1차 추천 요약(rec_json):
{rec_summary}

맛집 목록 요약(restaurants):
{restaurants_summary}

오류 목록 요약(errors):
{errors_summary}

반드시 아래 제목 구조를 이 순서 그대로 포함하라:
# {date} 국내 여행 추천 리포트
## 추천 지역
## 추천 이유
## 날씨 요약
## 행사/축제
## 맛집 추천
## 1일 일정 제안 (오전/오후/저녁)
## 오류 요약(errors)

{empty_restaurants_rule}
{empty_errors_rule}
1일 일정은 오전/오후/저녁으로 나눠 제안하라.
"""


def generate_report(date: str, rec_json, restaurants, errors) -> str:
    """Gemini로 마크다운 여행 리포트를 만들고, 실패하면 fallback 텍스트를 반환한다."""
    restaurants = restaurants or []
    errors = errors if errors is not None else []

    try:
        # 리포트용 Gemini 호출 (실패해도 프로그램은 계속)
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key or api_key == "your_gemini_api_key_here":
            raise RuntimeError("GEMINI_API_KEY가 없어 리포트 LLM 호출을 건너뜁니다.")

        client = genai.Client(api_key=api_key)
        prompt = _build_report_prompt(date, rec_json, restaurants, errors)
        markdown = _generate_text(client, prompt)
        if not markdown:
            raise RuntimeError("리포트 LLM 응답이 비어 있습니다.")
        return markdown
    except Exception as exc:
        # LLM 실패 시 파이썬으로 최소 리포트를 조립한다
        if isinstance(errors, list):
            errors.append(
                {
                    "step": "report",
                    "type": "LLM_ERROR",
                    "message": str(exc),
                }
            )
        return _fallback_report(date, rec_json, restaurants, errors)


def _plan_json_path(date: str) -> Path:
    """날짜별 JSON 결과 파일 경로를 반환한다."""
    return RESULTS_DIR / f"{date}_travel_plan.json"


def _plan_md_path(date: str) -> Path:
    """날짜별 마크다운 리포트 파일 경로를 반환한다."""
    return RESULTS_DIR / f"{date}_travel_plan.md"


def load_cached_plan(date: str) -> dict | None:
    """기존 JSON 캐시가 있으면 rec_json/restaurants/errors를 복원한다."""
    path = _plan_json_path(date)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "recommendation": data.get("recommendation"),
        "restaurants": data.get("restaurants") or [],
        "errors": data.get("errors") or [],
    }


def save_plan(date: str, rec_json, restaurants, errors, markdown: str) -> None:
    """results 폴더에 JSON과 마크다운 리포트를 저장한다."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "recommendation": rec_json,
        "restaurants": restaurants,
        "errors": errors,
    }
    _plan_json_path(date).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _plan_md_path(date).write_text(markdown, encoding="utf-8")


def parse_args(argv=None) -> argparse.Namespace:
    """커맨드라인 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="여행 계획 CLI")
    parser.add_argument(
        "--date",
        required=True,
        type=parse_date,
        help="여행 날짜 (YYYY-MM-DD)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    date = args.date
    errors: list = []

    # 캐시 확인: 같은 날짜 JSON이 있으면 추천/맛집 API는 건너뛴다
    cached = load_cached_plan(date)
    if cached is not None:
        print("[캐시 발견] API 호출 없이 기존 데이터로 리포트만 재생성합니다")
        rec_json = cached["recommendation"]
        restaurants = cached["restaurants"]
        errors = cached["errors"]
    else:
        # [1/3] LLM으로 1차 여행 추천 생성
        print("[1/3] 1차 추천 생성 중(LLM)...")
        rec_json = get_recommendation(date, errors=errors)
        city = rec_json.get("recommended_city") if isinstance(rec_json, dict) else None

        # [2/3] 지도/장소 API로 맛집 검색
        print("[2/3] 맛집 검색 중(지도/장소 API)...")
        restaurants = search_restaurants(city, errors=errors)

    # [3/3] LLM으로 최종 리포트 생성 (캐시가 있어도 항상 다시 만든다)
    print("[3/3] 최종 리포트 생성 중(LLM)...")
    markdown = generate_report(date, rec_json, restaurants, errors)

    # JSON(원본 데이터)과 MD(리포트)를 results/에 저장한다
    save_plan(date, rec_json, restaurants, errors, markdown)

    print(f"완료! results/{date}_travel_plan.md 를 확인하세요.")
    if errors:
        print("오류:", file=sys.stderr)
        for item in errors:
            if isinstance(item, dict):
                print(f"- {json.dumps(item, ensure_ascii=False)}", file=sys.stderr)
            else:
                print(f"- {item}", file=sys.stderr)


if __name__ == "__main__":
    main()
