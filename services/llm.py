import httpx
import json
import logging
from typing import List, Dict, Any, Optional
from bot.config import settings

logger = logging.getLogger(__name__)

class LLMService:
    def __init__(self):
        self.base_url = settings.COMETAPI_BASE_URL.rstrip("/")
        self.api_key = settings.COMETAPI_KEY
        self.primary_model = settings.PRIMARY_MODEL
        self.fallback_model = settings.FALLBACK_MODEL

    async def _call_gemini_format(
        self,
        model: str,
        contents: List[Dict[str, Any]],
        system_instruction: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Calls Comet API using Gemini format: /v1beta/models/{model}:generateContent"""
        url = f"{self.base_url}/v1beta/models/{model}:generateContent"
        headers = {
            "Authorization": f"Bearer {self.api_key}" if not self.api_key.startswith("Bearer ") else self.api_key,
            "Content-Type": "application/json"
        }
        
        payload: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Comet Gemini API error HTTP {resp.status_code}: {resp.text}")
            return resp.json()

    async def _call_openai_format(
        self,
        model: str,
        messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Calls OpenAI-compatible fallback endpoint: /v1/chat/completions"""
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}" if not self.api_key.startswith("Bearer ") else self.api_key,
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.4
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Comet OpenAI API error HTTP {resp.status_code}: {resp.text}")
            return resp.json()

    async def generate_response(
        self,
        contents_or_parts: List[Dict[str, Any]],
        system_instruction: str,
        action_type: str = "general",
        enable_web_search: bool = False
    ) -> Dict[str, Any]:
        """
        Unified generator: tries Primary model (gemini-3.8-flash) first.
        If it fails, tries Fallback model (gpt-6-luna).
        Returns: {
            "text": str,
            "tokens": {"prompt": int, "completion": int, "total": int},
            "model_used": str
        }
        """
        tools = [{"googleSearch": {}}] if enable_web_search else None

        # Format contents for Gemini
        gemini_contents = [{"parts": contents_or_parts}]

        # 1. Try Primary
        try:
            res = await self._call_gemini_format(
                model=self.primary_model,
                contents=gemini_contents,
                system_instruction=system_instruction,
                tools=tools
            )
            candidate = res.get("candidates", [{}])[0]
            text = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
            usage = res.get("usageMetadata", {})
            p_tokens = usage.get("promptTokenCount", 0)
            c_tokens = usage.get("candidatesTokenCount", 0)

            return {
                "text": text,
                "tokens": {
                    "prompt": p_tokens,
                    "completion": c_tokens,
                    "total": p_tokens + c_tokens
                },
                "model_used": self.primary_model
            }
        except Exception as e:
            logger.warning(f"Primary model {self.primary_model} failed: {e}. Attempting fallback {self.fallback_model}")

        # 2. Try Fallback (gpt-6-luna via OpenAI format)
        try:
            # Flatten textual parts for OpenAI format
            flattened_text = ""
            for part in contents_or_parts:
                if "text" in part:
                    flattened_text += part["text"] + "\n"
                elif "inlineData" in part:
                    flattened_text += "[Внимание: прикреплен медиафайл]\n"

            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": flattened_text.strip()}
            ]
            res = await self._call_openai_format(model=self.fallback_model, messages=messages)
            text = res["choices"][0]["message"]["content"]
            usage = res.get("usage", {})
            p_tokens = usage.get("prompt_tokens", 0)
            c_tokens = usage.get("completion_tokens", 0)

            return {
                "text": text,
                "tokens": {
                    "prompt": p_tokens,
                    "completion": c_tokens,
                    "total": p_tokens + c_tokens
                },
                "model_used": self.fallback_model
            }
        except Exception as e2:
            logger.error(f"Fallback model {self.fallback_model} also failed: {e2}")
            raise RuntimeError(f"Both primary and fallback models failed: {e2}")

llm_service = LLMService()
