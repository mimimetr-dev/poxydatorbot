import json
import logging
import re
from typing import Any, Dict, List, Tuple

from bot.config import settings
from bot.services.llm import llm_service

logger = logging.getLogger(__name__)

NUTRITION_SYSTEM_PROMPT = """Ты персональный нутрициолог, который ведёт учёт калорий для похудения через дефицит калорий.

Задача: определить, что съел пользователь, и оценить калории и БЖУ.

Отвечай ТОЛЬКО валидным JSON без markdown-обёрток:
{
  "is_food": true,
  "dish_name": "Название блюда и порция",
  "calories": 450,
  "protein": 25.0,
  "fat": 15.0,
  "carbs": 50.0,
  "confidence": "high",
  "needs_search": false,
  "search_query": null,
  "comment": "Короткий ободряющий комментарий, 1-2 предложения",
  "answer": null
}

Правила:
- "is_food": false, если в сообщении НЕТ еды (вопрос, болтовня, просьба). Тогда заполни "answer" полезным кратким ответом (до 4 предложений), а калории оставь 0.
- "confidence": "high" — блюдо простое и хорошо известно; "medium" — порция или состав под вопросом; "low" — брендовый/готовый продукт, редкое блюдо или непонятный состав.
- "needs_search": true, если для точности нужны данные из интернета (брендовые продукты, кафе, магазинные позиции, сложный состав). Иначе false.
- "search_query": короткий точный запрос для Google (например "калорийность сырок Александров глазированный 50 г КБЖУ"), иначе null.
- Оценивай реалистично, по средним порциям, если размер не указан.
"""

SEARCH_REFINE_PROMPT = """Ты нутрициолог. Ниже данные о блюде, которые нужно уточнить с помощью Google Search.

Обязательно сделай поиск по запросу: {query}
Найди точную калорийность и БЖУ (КБЖУ) и верни ФИНАЛЬНЫЙ ответ.

Отвечай ТОЛЬКО валидным JSON той же схемы:
{{
  "is_food": true,
  "dish_name": "...",
  "calories": 0,
  "protein": 0.0,
  "fat": 0.0,
  "carbs": 0.0,
  "confidence": "high",
  "needs_search": false,
  "search_query": null,
  "comment": "...",
  "answer": null
}}
В "comment" укажи, что данные уточнены через поиск (например: "Нашёл точные данные по продукту").
"""


def parse_json(raw: str) -> Dict[str, Any]:
    cleaned = (raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _normalize(parsed: Dict[str, Any]) -> Dict[str, Any]:
    def as_int(key: str) -> int:
        try:
            return int(round(float(parsed.get(key) or 0)))
        except (TypeError, ValueError):
            return 0

    def as_float(key: str) -> float:
        try:
            return round(float(parsed.get(key) or 0), 1)
        except (TypeError, ValueError):
            return 0.0

    confidence = str(parsed.get("confidence") or "medium").lower()
    return {
        "is_food": bool(parsed.get("is_food", True)),
        "dish_name": str(parsed.get("dish_name") or "Блюдо"),
        "calories": as_int("calories"),
        "protein": as_float("protein"),
        "fat": as_float("fat"),
        "carbs": as_float("carbs"),
        "confidence": confidence if confidence in ("high", "medium", "low") else "medium",
        "needs_search": bool(parsed.get("needs_search", False)),
        "search_query": parsed.get("search_query"),
        "comment": str(parsed.get("comment") or ""),
        "answer": parsed.get("answer"),
    }


async def analyze(
    parts: List[Dict[str, Any]],
    action_type: str = "meal"
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Two-pass analysis:
      Pass 1 — cheap, no search.
      Pass 2 — web search, only if the model says it is unsure.

    Returns (parsed_result, list_of_usage_records).
    """
    usages: List[Dict[str, Any]] = []

    first = await llm_service.generate_response(
        contents_or_parts=parts,
        system_instruction=NUTRITION_SYSTEM_PROMPT,
        action_type=action_type,
        enable_web_search=False
    )
    usages.append({
        "model": first["model_used"],
        "tokens": first["tokens"],
        "action": f"{action_type}_analyze"
    })

    parsed = _normalize(parse_json(first["text"]))
    parsed["used_search"] = False

    needs_second_pass = parsed["is_food"] and (
        parsed["needs_search"] or parsed["confidence"] == "low"
    )
    if not settings.ENABLE_WEB_SEARCH or not needs_second_pass:
        return parsed, usages

    query = parsed["search_query"] or f"калорийность {parsed['dish_name']} КБЖУ"

    search_parts = list(parts) + [
        {
            "text": (
                f"Предварительная оценка: {parsed['dish_name']} — {parsed['calories']} ккал "
                f"(Б {parsed['protein']} / Ж {parsed['fat']} / У {parsed['carbs']})."
            )
        }
    ]

    try:
        second = await llm_service.generate_response(
            contents_or_parts=search_parts,
            system_instruction=SEARCH_REFINE_PROMPT.format(query=query),
            action_type=action_type,
            enable_web_search=True
        )
        usages.append({
            "model": second["model_used"],
            "tokens": second["tokens"],
            "action": f"{action_type}_search"
        })
        refined = _normalize(parse_json(second["text"]))
        # Keep the refined result only if it looks sane
        if refined["is_food"] and refined["calories"] > 0:
            refined["used_search"] = True
            return refined, usages
    except Exception as e:
        logger.warning(f"Web-search refinement failed, using first pass: {e}")

    return parsed, usages
