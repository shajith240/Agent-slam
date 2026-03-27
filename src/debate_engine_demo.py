import logging
import time

import requests
from urllib.parse import quote

from src.config import SAFE_MESSAGE_CHARS
from src.state_machine import MatchState
from src.strategy import build_prompt

logger = logging.getLogger(__name__)

POLLINATIONS_URL = "https://text.pollinations.ai/openai"
POLLINATIONS_MODEL = "openai"  # GPT-4o equivalent, free, no key needed


class DebateEngine:

    def __init__(self):
        self.call_count = 0
        self.total_chars_out = 0
        logger.info("DebateEngine ready — using Pollinations.ai (free, no key)")

    def generate_argument(self, state: MatchState) -> str:
        system_prompt, user_prompt = build_prompt(state)

        payload = {
            "model": POLLINATIONS_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.8,
            "max_tokens": 1500,
        }

        last_error = None
        for attempt in range(3):
            try:
                logger.info(
                    "Generating argument attempt %d, phase=%s",
                    attempt + 1, state.debate_phase,
                )
                start_time = time.time()

                response = requests.post(
                    POLLINATIONS_URL,
                    json=payload,
                    timeout=60,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()

                elapsed = time.time() - start_time
                logger.info("Pollinations responded in %.1fs", elapsed)

                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()

                if not text:
                    raise ValueError("Empty response from Pollinations")

                self.call_count += 1
                text = self._trim_to_limit(text)
                self.total_chars_out += len(text)

                logger.info(
                    "Argument ready: %d chars, phase=%s",
                    len(text), state.debate_phase,
                )
                return text

            except Exception as e:
                last_error = e
                logger.error("Attempt %d failed: %s", attempt + 1, str(e))
                if attempt < 2:
                    time.sleep(5)  # wait 5s before retry (respects rate limit)

        raise RuntimeError(
            f"All 3 Pollinations attempts failed. Last error: {last_error}"
        )

    def _trim_to_limit(self, text: str) -> str:
        if len(text) <= SAFE_MESSAGE_CHARS:
            return text

        logger.warning(
            "Response too long (%d chars), trimming to %d",
            len(text), SAFE_MESSAGE_CHARS,
        )
        trimmed = text[:SAFE_MESSAGE_CHARS]
        last_period = trimmed.rfind(".")
        last_newline = trimmed.rfind("\n")
        cut_point = max(last_period, last_newline)

        if cut_point > SAFE_MESSAGE_CHARS // 2:
            trimmed = trimmed[:cut_point + 1]

        if not trimmed.endswith((".", "!", "?", '"')):
            trimmed += "."

        return trimmed.strip()

    def usage_summary(self) -> str:
        return (
            f"API calls: {self.call_count} | "
            f"Total output chars: {self.total_chars_out} | "
            f"Est. cost: $0.00 (Pollinations.ai free)"
        )