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

EMERGENCY_CLOSING_PRO = (
    "In conclusion, our PRO position has been demonstrated through consistent reasoning "
    "across every phase of this debate. Our opponent's arguments relied on speculation and "
    "selective framing, while ours were grounded in logical structure and coherent evidence. "
    "The burden of proof favored our position from the start, and nothing in this debate "
    "has shifted that. We stand firm: the resolution holds, and the judge should affirm."
)

EMERGENCY_CLOSING_CON = (
    "In conclusion, our CON position has dismantled the case for the resolution at every "
    "turn. Our opponent failed to meet their burden of proof — they could not demonstrate "
    "that the proposed change is necessary, beneficial, or feasible. The status quo "
    "arguments we advanced remain unanswered. The judge should reject the resolution and "
    "affirm the CON."
)


class DebateEngine:

    def __init__(self, use_web_search: bool = True):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.use_web_search = use_web_search
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def research_topic(self, topic: str, stance: str) -> str:
        """
        One-time pre-match research call using web_search.
        Called when the topic first arrives, before the opening argument.
        Returns structured facts + URLs capped at 3000 chars.
        """
        prompt = (
            f'Research the debate topic: "{topic}"\n\n'
            f"We are arguing {stance}. Provide:\n\n"
            f"SUPPORTING FACTS (for {stance} side):\n"
            f"- [1-2 sentence fact with specific data]. (Source: https://real-url)\n"
            f"[6-8 such facts]\n\n"
            f"OPPONENT FACTS (what they will likely argue):\n"
            f"- [1-2 sentence fact]. (Source: https://real-url)\n"
            f"[4-5 such facts]\n\n"
            f"Keep each fact to 2 sentences max. Use only 2022-2025 sources. "
            f"Total: 10-13 facts with unique real URLs."
        )
        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1200,
                system=(
                    "You are a research assistant for competitive debate. "
                    "Search the web and return structured facts with real verified URLs. Be concise."
                ),
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 2,
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            self.call_count += 1
            if response.usage:
                self.total_input_tokens += response.usage.input_tokens
                self.total_output_tokens += response.usage.output_tokens
                logger.info(
                    "Research call — input: %d tokens, output: %d tokens",
                    response.usage.input_tokens, response.usage.output_tokens,
                )
            text = self._extract_text(response)
            # Cap at 3000 chars to keep downstream prompts manageable
            if len(text) > 3000:
                text = text[:3000]
            logger.info("Research complete: %d chars", len(text))
            return text
        except Exception as e:
            logger.warning("Research call failed: %s — will argue without pre-fetched data", e)
            return ""

    def fetch_opponent_url(self, url: str) -> str:
        """
        Fetch a specific URL cited by the opponent, via Jina Reader (free, no API key).
        Returns clean text capped at 2000 chars, or empty string on failure.
        """
        import requests as _requests
        try:
            jina_url = f"https://r.jina.ai/{url}"
            resp = _requests.get(jina_url, timeout=8, headers={"Accept": "text/plain"})
            if resp.status_code == 200:
                content = resp.text[:2000]
                logger.info("Fetched opponent URL via Jina: %d chars from %s", len(content), url)
                return content
            logger.warning("Jina returned status %d for %s", resp.status_code, url)
            return ""
        except Exception as e:
            logger.warning("Jina fetch failed for %s: %s", url, e)
            return ""

    def generate_argument(self, state: MatchState) -> str:
        """Argument generation using pre-fetched research_data. No live web_search."""
        system_prompt, user_prompt = build_prompt(state, search_results_text=state.research_data)

        last_error = None
        for attempt in range(3):
            try:
                logger.info(
                    "Generating argument attempt %d, phase=%s, call_mode=%s",
                    attempt + 1, state.debate_phase, state.call_mode,
                )
                start_time = time.time()

                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
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
                logger.error("Attempt %d failed: %s", attempt + 1, str(e))
                if attempt < 2:
                    # Rate limit: wait for the 1-minute window to reset
                    if "rate_limit" in str(e).lower() or "429" in str(e):
                        logger.warning("Rate limit hit — waiting 65s for window reset...")
                        time.sleep(65)
                    else:
                        time.sleep(2)

        logger.critical(
            "All 3 API attempts failed. Last error: %s. Using fallback argument.",
            last_error,
        )
        return self._get_fallback(state.debate_phase)

    def generate_caution_argument(self, state: MatchState) -> str:
        """
        Caution mode: NO web search tool — pure reasoning from conversation context.
        Faster and cheaper. Used when time is 120-240s or avg response > 45s.
        """
        system_prompt, user_prompt = build_prompt(state, search_results_text=state.research_data)

        # Append a note telling Claude to reason from context only
        user_prompt += (
            "\n\nIMPORTANT: You are in CAUTION mode due to time constraints. "
            "Do NOT attempt web searches. Argue from logical reasoning, "
            "the conversation history above, and well-known principles. "
            "Cite general knowledge only (e.g., 'According to established economic theory...'). "
            "Prioritize SPEED — respond in under 20 seconds."
        )

        last_error = None
        for attempt in range(2):  # Only 2 attempts in caution mode
            try:
                logger.info(
                    "CAUTION mode argument attempt %d, phase=%s",
                    attempt + 1, state.debate_phase,
                )
                start_time = time.time()

                # No tools parameter — pure text generation
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )

                elapsed = time.time() - start_time
                logger.info("CAUTION API responded in %.1fs", elapsed)

                self.call_count += 1
                if response.usage:
                    self.total_input_tokens += response.usage.input_tokens
                    self.total_output_tokens += response.usage.output_tokens

                text = self._extract_text(response)
                if not text:
                    raise ValueError("Empty response from API")

                text = self._strip_markdown(text)
                text = self._trim_to_limit(text)
                logger.info("CAUTION argument ready: %d chars", len(text))
                return text

            except Exception as e:
                last_error = e
                logger.error("CAUTION attempt %d failed: %s", attempt + 1, str(e))
                if attempt < 1:
                    if "rate_limit" in str(e).lower() or "429" in str(e):
                        logger.warning("Rate limit hit — waiting 65s...")
                        time.sleep(65)
                    else:
                        time.sleep(1)

        logger.critical("CAUTION mode failed. Using fallback. Last error: %s", last_error)
        return self._get_fallback(state.debate_phase)

    def generate_emergency_argument(self, state: MatchState) -> str:
        """
        Emergency mode: synthesis-only prompt, very short, instant.
        Used when time is 60-120s or avg response > 60s.
        No API tools, minimal prompt, targets <10s response.
        """
        opponent_msg = state.last_opponent_message() or "They made an argument."
        our_stance = state.our_stance
        topic = state.topic or "the topic"
        phase = state.debate_phase

        # Determine emergency closing text
        if phase == "closing":
            closing = EMERGENCY_CLOSING_PRO if our_stance == "PRO" else EMERGENCY_CLOSING_CON
            logger.warning("EMERGENCY closing fired for stance=%s", our_stance)
            return closing

        # Synthesize from last 3 of our own messages
        our_messages = [
            msg["message"] for msg in state.conversation
            if msg["is_ours"]
        ][-3:]
        our_context = " ".join(our_messages)[:600] if our_messages else "We have argued our position clearly."

        emergency_prompt = (
            f"You are a debate agent arguing {our_stance} on: '{topic}'.\n"
            f"Time is critically short. Write ONE punchy paragraph (under 400 chars) that:\n"
            f"1. Rebuts this opponent claim: '{opponent_msg[:200]}'\n"
            f"2. Reinforces our core position using: '{our_context[:300]}'\n"
            f"No sources needed. Plain text. Assertive tone. Under 400 characters. Output ONLY the argument."
        )

        try:
            logger.warning(
                "EMERGENCY mode argument, phase=%s, remaining=%ds",
                phase, state.seconds_remaining_in_match,
            )
            start_time = time.time()

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=300,  # Force short output
                system="You are a competitive debate agent. Respond concisely and assertively.",
                messages=[{"role": "user", "content": emergency_prompt}],
            )

            elapsed = time.time() - start_time
            logger.warning("EMERGENCY API responded in %.1fs", elapsed)

            self.call_count += 1
            if response.usage:
                self.total_input_tokens += response.usage.input_tokens
                self.total_output_tokens += response.usage.output_tokens

            text = self._extract_text(response)
            if not text:
                return self._get_fallback(state.debate_phase)

            text = self._strip_markdown(text)
            text = self._trim_to_limit(text)
            logger.warning("EMERGENCY argument ready: %d chars", len(text))
            return text

        except Exception as e:
            logger.critical("EMERGENCY mode API failed: %s — using hardcoded fallback", e)
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
