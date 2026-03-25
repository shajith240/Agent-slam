import logging
import time

import anthropic

from src.config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, SAFE_MESSAGE_CHARS
from src.state_machine import MatchState
from src.strategy import build_prompt

logger = logging.getLogger(__name__)


class DebateEngine:

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def generate_argument(self, state: MatchState) -> str:
        system_prompt, user_prompt = build_prompt(state)

        last_error = None
        for attempt in range(3):
            try:
                logger.info(
                    "Generating argument attempt %d, phase=%s",
                    attempt + 1, state.debate_phase,
                )
                start_time = time.time()

                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    tools=[{"type": "web_search_20250305", "name": "web_search"}],
                    messages=[{"role": "user", "content": user_prompt}],
                )

                elapsed = time.time() - start_time
                logger.info("API responded in %.1fs", elapsed)

                self.call_count += 1
                if response.usage:
                    self.total_input_tokens += response.usage.input_tokens
                    self.total_output_tokens += response.usage.output_tokens
                    logger.info(
                        "Tokens — input: %d, output: %d (cumulative in: %d, out: %d)",
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                        self.total_input_tokens,
                        self.total_output_tokens,
                    )

                text = self._extract_text(response)
                if not text:
                    raise ValueError("Empty response from API")

                text = self._trim_to_limit(text)
                logger.info(
                    "Argument ready: %d chars, phase=%s",
                    len(text), state.debate_phase,
                )
                return text

            except Exception as e:
                last_error = e
                logger.error(
                    "Attempt %d failed: %s", attempt + 1, str(e),
                )
                if attempt < 2:
                    time.sleep(2)

        raise RuntimeError(
            f"All 3 API attempts failed. Last error: {last_error}"
        )

    def _extract_text(self, response) -> str:
        text_parts = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        return "\n".join(text_parts).strip()

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
        input_cost = (self.total_input_tokens / 1_000_000) * 3.00
        output_cost = (self.total_output_tokens / 1_000_000) * 15.00
        search_cost = self.call_count * 0.01
        total = input_cost + output_cost + search_cost
        return (
            f"API calls: {self.call_count} | "
            f"Input tokens: {self.total_input_tokens} | "
            f"Output tokens: {self.total_output_tokens} | "
            f"Est. cost: ${total:.4f}"
        )
