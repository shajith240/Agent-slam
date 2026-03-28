import logging
import re
import time

import anthropic

from src.config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, SAFE_MESSAGE_CHARS
from src.state_machine import MatchState
from src.strategy import build_prompt

logger = logging.getLogger(__name__)

FALLBACK_ARGUMENTS = {
    "opening": (
        "The evidence overwhelmingly supports our position on this topic. "
        "First, the fundamental principles at stake here point clearly in our direction. "
        "The real-world data consistently demonstrates that our stance aligns with both "
        "expert consensus and practical outcomes. Second, the economic and social implications "
        "favor our position when examined rigorously. Third, the long-term trajectory of "
        "research and policy development reinforces what we are arguing today. We look "
        "forward to demonstrating this throughout the debate."
    ),
    "rebuttal_first": (
        "Our opponent has presented their case, but upon closer examination, their central "
        "claim rests on a flawed premise. They have conflated correlation with causation and "
        "ignored the broader context that undermines their position. The evidence they cited "
        "actually supports our argument when read in full. We maintain our position and "
        "challenge them to address the structural weaknesses in their reasoning."
    ),
    "cross_examination": (
        "Our opponent continues to avoid addressing the core issue. Their argument relies "
        "on selective evidence while ignoring the systemic factors that determine outcomes. "
        "The logical framework they are using cannot account for the complexity of this "
        "topic. We have presented multiple independent lines of evidence, each pointing to "
        "the same conclusion. We ask our opponent directly: how do they reconcile their "
        "position with the weight of evidence against it?"
    ),
    "defense": (
        "Our opponent's latest attack does not hold up under scrutiny. They have taken our "
        "argument out of context and responded to a claim we did not make. Our actual "
        "position, supported by the evidence we have cited, remains uncontested. We have "
        "consistently demonstrated stronger reasoning and more credible evidence throughout "
        "this debate. The fundamental question remains answered in our favor."
    ),
    "closing": (
        "In conclusion, we have demonstrated throughout this debate that our position is "
        "supported by stronger evidence, sounder logic, and a more comprehensive framework. "
        "Our opponent raised important points, but ultimately could not overcome the "
        "fundamental strength of our case. The evidence speaks clearly, and we are confident "
        "the judge will agree that our arguments have carried the day."
    ),
}


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

                text = self._strip_markdown(text)
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

        logger.critical(
            "All 3 API attempts failed. Last error: %s. Using fallback argument.",
            last_error,
        )
        return self._get_fallback(state.debate_phase)

    def _extract_text(self, response) -> str:
        text_parts = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
        return "\n".join(text_parts).strip()

    def _strip_markdown(self, text: str) -> str:
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        return text.strip()

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

    def _get_fallback(self, phase: str) -> str:
        return FALLBACK_ARGUMENTS.get(phase, FALLBACK_ARGUMENTS["defense"])

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
