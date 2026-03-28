# 🛠️ Agent SLAM 2026 — Full Improvement & Audit Report

> Compiled from full codebase review + mock debate analysis | March 28, 2026  
> Read this top to bottom before every match.

---

## 📋 Table of Contents
1. [Bugs Fixed](#1-bugs-fixed)
2. [Code Flaws (Fix Before Match)](#2-code-flaws-fix-before-match)
3. [Prompt Improvements (strategy.py)](#3-prompt-improvements-strategypy)
4. [Scoring Strategy & Mock Debate Analysis](#4-scoring-strategy--mock-debate-analysis)
5. [Pre-Match Testing Plan](#5-pre-match-testing-plan)
6. [Cost & Credits Summary](#6-cost--credits-summary)
7. [Files Reference](#7-files-reference)
8. [Priority Checklist](#8-priority-checklist)

---

## 1. Bugs Fixed

### ✅ BUG 1 — Team names reversed on dashboard
**Commit:** `7cbb861`  
**File:** `monitor/dashboard.html`

**Problem:**
- Left panel (cyan) was showing the **opponent's** arguments
- Right panel (pink) was showing **our** arguments — completely flipped
- Root cause: all team comparisons used strict `===` equality
- Server sends `"team2"` but user types `"Team2"` in the setup screen
- `"team2" === "Team2"` evaluates to `false` → `isOurs` always returned the wrong value for every message

**Fix applied:**
- Added `isSameTeam(a, b)` helper function that trims + lowercases both sides before comparing
- Replaced **all** strict `===` team comparisons with `isSameTeam()` calls
- Files/locations changed:
  - `handleDebateMessage()` — `isOurs` check
  - `handlePreviousMessages()` — `isOurs` check
  - `updateTurnIndicators()` — turn highlight logic
  - `getOurStance()` — PRO/CON determination
  - `getOpponentName()` — opponent name resolution

---

## 2. Code Flaws (Fix Before Match)

### ⚠️ FLAW 1 — Response trimming silently cuts arguments
**File:** `src/debate_engine.py` → `_trim_to_limit()`  
**Priority: HIGH — directly impacts score**

**Problem:**
- If `MAX_TOKENS` is set too high, Claude writes 4000+ characters
- `_trim_to_limit()` silently cuts the response to `SAFE_MESSAGE_CHARS` (2800)
- Even though it finds the last full sentence, your **closing line or final point gets deleted without warning**
- The judge never sees your strongest closing statement

**Fix:**
```
1. Lower MAX_TOKENS in your .env to 900
   → Claude naturally stops before 2800 chars
   → Trimming should NEVER fire during a match

2. Also add a hard character limit reminder in strategy.py
   (see Prompt Improvement #1 in Section 3)

3. During testing, watch the logs:
   If you see "Response too long... trimming to XXXX"
   → MAX_TOKENS is still too high, lower it further
```

---

### ⚠️ FLAW 2 — Phase detection is time-based, not message-based
**File:** `src/state_machine.py` → phase logic  
**Priority: MEDIUM**

**Problem:**
- Phase detection uses `seconds_remaining` and `message_count`
- If the opponent responds slowly, you may enter the **"closing" phase too early** just because time ran out
- Closing phase disallows new arguments — you lose scoring opportunities in the middle of the debate
- Example: match is 4 messages in, 3 min remaining → you skip to closing → judge scores you low on Logic and Agility for the remaining turns

**Fix:**
```python
# In state_machine.py, change closing phase trigger to require BOTH:
# (a) seconds_remaining < 180  AND
# (b) message_count >= 6

# This ensures you never close early in a slow-paced match
```

---

### ⚠️ FLAW 3 — Turn detection race condition (partially guarded)
**File:** `src/ws_client.py` → `handle_debate_message()`  
**Priority: MEDIUM**

**Problem:**
- Server sends **two signals** per turn: `debate-message` + `match-state` (both within milliseconds)
- Both signals call `take_turn()` independently
- `_turn_in_progress` flag guards this — BUT only if both signals arrive **before** `generate_argument()` completes
- If the Claude API call takes > 5 seconds, the `match-state` arrives **after** the flag is already reset
- Result: a second `take_turn()` fires → agent sends a duplicate argument → server returns `"It's not your turn!"` error

**Fix:**
```python
# Quick fix: after sending, add a 10-second cooldown before
# accepting another turn signal

# Better fix: assign a per-turn UUID when turn starts
# Only process a turn if the current turn_id matches the one we began
# This is immune to timing issues entirely
```

---

### ⚠️ FLAW 4 — No fallback if Claude API fails all 3 retries
**File:** `src/debate_engine.py` → `generate_argument()`  
**Priority: HIGH — affects API Robustness score (20%)**

**Problem:**
- If all 3 retry attempts fail (network issue, rate limit, API outage)
- `RuntimeError` is raised and caught in `ws_client.py` → `take_turn()`
- Your agent **sends nothing** for that turn
- Server may penalize you for a missed turn under API Robustness (20% of score)
- One missed turn can cost you the match in a 71-72 scenario

**Fix:**
```python
# Add hardcoded fallback strings for each phase in debate_engine.py
# If all 3 retries fail, send the fallback instead of silence

FALLBACKS = {
    "opening": (
        "The evidence overwhelmingly supports our position. "
        "First, empirical research demonstrates clear benefits. "
        "Second, logical analysis confirms our stance is sound. "
        "Third, real-world outcomes validate this approach. "
        "We stand ready to defend this position rigorously."
    ),
    "rebuttal_first": (
        "The opponent's argument lacks empirical grounding. "
        "The sources they cite do not support the conclusions they draw. "
        "Our position remains well-supported and unrefuted."
    ),
    "cross_examination": (
        "The logical flaw in the opponent's reasoning is clear: "
        "they confuse correlation with causation. "
        "Our evidence directly contradicts their central claim."
    ),
    "defense": (
        "Our position stands firm. The opponent has failed to "
        "provide sufficient counter-evidence. "
        "Every factual claim we made remains uncontested."
    ),
    "closing": (
        "In conclusion, we have demonstrated our position clearly "
        "across every phase of this debate. "
        "The evidence, logic, and real-world outcomes all support our stance. "
        "The correct position is ours."
    ),
}
```

---

## 3. Prompt Improvements (strategy.py)

> All changes go in `src/strategy.py`. No other file needs editing.  
> These are the highest-impact improvements you can make.

---

### 🔴 IMPROVEMENT 1 — Prevent source URL recycling
**Impact: HIGH — Logic score (30%)**

**Problem observed in mock debate:**
- Both teams cited the **same 4–5 URLs** across all 10 rounds
- The JAMA article and McKinsey report were cited **6–7 times each**
- The judge almost certainly penalizes this under Logic (30%) — it signals the agent has no new evidence and is going in circles
- Fresh sources each round = higher credibility + higher Logic score

**Add to `SYSTEM_BASE` after the CRITICAL RULES section:**
```
6. NEVER cite a URL you have already used in this debate.
   Before citing any source, scan the CONVERSATION HISTORY above.
   If a URL already appears there, use web_search to find a
   completely fresh source instead. Reusing the same URL twice
   signals to the judge that you have no new evidence.
```

---

### 🔴 IMPROVEMENT 2 — Ban defensive opening phrases
**Impact: HIGH — Persuasiveness score (40%)**

**Problem observed in mock debate:**
- PRO team (User6) **lost 71–72** despite arguing the PRO side
- Root cause: repeatedly used phrases like:
  - `"While it's true that..."`
  - `"You're right that..."`
  - `"I agree that..."`
- These phrases **signal concession** to the judge before making a point
- Persuasiveness (40%) is the single largest score factor — sounding defensive tanks it
- CON team (User17) was more assertive = won by 1 point

**Add to `SYSTEM_BASE` under CRITICAL RULES:**
```
7. NEVER begin a sentence or paragraph with any of these phrases:
   - "While it's true that"
   - "You're right that"
   - "I agree that"
   - "That's a valid point"
   - "You make a fair point"
   These phrases signal weakness and reduce your Persuasiveness score.
   Instead: directly challenge, reframe, or redirect the opponent's claim.
   Stay on offense at all times. Never retreat — even when cornered,
   reframe confidently and push forward.
```

---

### 🔴 IMPROVEMENT 3 — Force argument escalation each round
**Impact: HIGH — Agility (10%) + Logic (30%)**

**Problem observed in mock debate:**
- Both teams repeated the **same 3 arguments** (jobs, healthcare, regulation) for all 10 rounds with zero new content
- Agility score (10%) penalizes repeating yourself instead of responding to what the opponent specifically said
- Logic score (30%) also drops if no new evidence or reasoning appears after round 3
- A real debate should **escalate** — each rebuttal goes deeper, opens new fronts

**Add to `cross_examination` and `defense` phase instructions:**
```
CRITICAL ESCALATION RULE:
You MUST introduce at least ONE completely new argument, angle,
or piece of evidence that does NOT appear anywhere in the
CONVERSATION HISTORY above. Do not re-argue what was already said.
Escalate the debate by opening a new front the opponent has not
addressed yet. Force them to fight on unfamiliar ground.
Repeating old points scores zero on Agility.
```

---

### 🟡 IMPROVEMENT 4 — Strengthen closing argument instruction
**Impact: HIGH — Persuasiveness (40%) — judge's final impression**

**Problem observed in mock debate:**
- Conversations ended mid-argument with no proper closing statement
- No `"In conclusion..."` synthesis was ever delivered
- The judge's final impression was a mid-debate rebuttal, not a decisive finish
- Closing carries **disproportionate weight** — it's the last thing the judge reads

**Reinforce the existing closing phase instruction in `PHASE_INSTRUCTIONS["closing"]`:**
```
FINAL WARNING — READ THIS CAREFULLY:
The judge forms their FINAL IMPRESSION right now from what you write.
A weak closing loses the match even after a perfect debate.

Your closing MUST:
  (a) Begin with "In conclusion," — this signals finality to the judge
  (b) Name 2-3 specific exchanges from this debate where you clearly won
      — reference what YOU said and what the OPPONENT failed to counter
  (c) Acknowledge the opponent's single strongest point, then explain
      in one sentence why it does not change the overall outcome
  (d) End with ONE short, punchy, quotable, memorable sentence about
      why your stance on {topic} is the correct position
      — make it the kind of line a judge remembers and quotes back

DO NOT introduce new arguments or new evidence here.
Synthesize everything. Make it count. Win the room.
```

---

### 🟡 IMPROVEMENT 5 — Enforce argument consistency across turns
**Impact: MEDIUM — Logic score (30%)**

**Problem:**
- Claude can **contradict its own earlier claims** across turns if not explicitly reminded
- Conversation history is passed in the prompt but Claude may not actively cross-check it
- Contradictions are penalized heavily under Logic (30%) — the judge notices
- Example: claiming in turn 3 that "AI creates jobs" then in turn 7 that "AI displaces workers net negative" = contradiction

**Add to `SYSTEM_BASE` under CRITICAL RULES:**
```
8. Before writing your argument, silently scan the full
   CONVERSATION HISTORY to check what positions you have already
   taken. NEVER contradict a claim or position you made in an
   earlier turn. Internal consistency across all turns is
   evaluated under Logic (30%). Contradictions are heavily penalized.
```

---

### 🟡 IMPROVEMENT 6 — Force direct opponent quoting in rebuttals
**Impact: MEDIUM — Agility score (10%)**

**Problem:**
- Generic rebuttals like `"the opponent is wrong"` score **zero** on Agility
- Agility requires directly addressing the opponent's **specific words**, not just their topic
- The judge checks whether you actually read what the opponent wrote

**Add to `rebuttal_first` and `cross_examination` phase instructions:**
```
QUOTING REQUIREMENT:
You MUST copy-paste at least ONE exact sentence from the
OPPONENT'S LAST MESSAGE (shown above) into your response.
Then dismantle it word by word — explain exactly why that
specific sentence is wrong, misleading, or unsupported.
If you do not quote the opponent directly, you score zero on
Agility (10%). The judge checks this explicitly.
```

---

## 4. Scoring Strategy & Mock Debate Analysis

### Score Weights (from rulebook)
| Criterion | Weight | What the judge evaluates |
|---|---|---|
| Persuasiveness | **40%** | Rhetoric quality, strength of evidence, compelling narrative |
| Logic | **30%** | No fallacies, internal consistency, valid deductions |
| API Robustness | **20%** | Correct message format, fast response speed, no crashes |
| Agility | **10%** | Directly address opponent's specific words |

> Persuasiveness + Logic = **70% of your score.** Argument quality wins tournaments.

---

### Mock Debate Observations (User6 PRO vs User17 CON | Score: 71–72)

**Match facts:**
- Duration: exactly 10 minutes (8:24 PM → 8:34 PM)
- Messages: ~10 per team, one every ~22 seconds
- Winner: CON (User17) by 1 point
- Topic: *AI Agents: Human Augmentation or Autonomous Risk?*

**Why PRO lost despite arguing the easier side:**
1. **Defensive tone** — Used "While it's true that" and "You're right that" in nearly every turn before countering. This conceded ground before attacking.
2. **Source recycling** — JAMA article and McKinsey report cited 6–7 times each with no new sources introduced after round 3.
3. **No escalation** — Same 3 arguments (jobs, healthcare, regulation) repeated for all 10 rounds. No new fronts opened.
4. **Weak endings** — No memorable closing line. Last impression was a bland mid-debate rebuttal.

**Why CON won:**
1. More assertive tone — attacked directly without conceding first
2. Slightly more varied evidence in later rounds
3. Better final statements

**Key takeaway:**
> **Tone matters MORE than facts alone.**  
> Being assertive and offensive scores higher than being thorough and polite.  
> One strong memorable sentence at the end of each argument matters as much as three paragraphs of evidence.

---

## 5. Pre-Match Testing Plan

> Goal: Run a real live test with a friend using the sandbox endpoint.  
> This is NOT mock data — it's the actual WebSocket connection.

### Step 1 — Setup
```bash
# You run:
python agent.py --sandbox

# Your friend runs a simple WebSocket client:
python -c "
import asyncio, websockets, json
URL = 'wss://agent-slam-server.fly.dev/ws-sandbox?payload=YOUR_TOKEN'
async def test():
    async with websockets.connect(URL) as ws:
        await ws.recv()  # welcome
        while True:
            msg = json.dumps({'type': 'sandbox-message', 'data': {'message': 'Your test argument here'}})
            await ws.send(msg)
            reply = await ws.recv()
            print('Reply:', reply)
asyncio.run(test())
"
```

### Step 2 — What to check in logs

| Check | Target | Danger Zone | Log line to look for |
|---|---|---|---|
| **Latency** | < 30 seconds | > 90 seconds | `IT IS OUR TURN` → `Sent argument` timestamps |
| **Character count** | 1800–2800 chars | < 1500 or trimming fires | `Sent argument [debate-message]: XXX chars` |
| **Web search** | 2 searches per turn | 0 searches (tool not working) | `web_search_requests` in usage log |
| **Phase transitions** | Opening→Rebuttal→Cross→Defense→Closing | Closing too early | `phase=` in log lines |
| **No crashes** | Clean run end to end | Any uncaught exception | Check for `ERROR` lines in log |

### Step 3 — Manually score your outputs
After the test, read every argument your agent produced and ask:
- [ ] Did it cite **different URLs** each turn? (no recycling)
- [ ] Did it **directly quote** the opponent's exact words?
- [ ] Did each round bring a **new argument** or just repeat old ones?
- [ ] Was the tone **assertive** or defensive? (grep for "while it's true")
- [ ] Did the **closing** feel final, synthesized, and memorable?
- [ ] Was character count consistently **above 1800**? (not too short)

### Step 4 — Iterate fast
```
1. Edit src/strategy.py based on what you found
2. Re-run the sandbox test
3. Read the new outputs
4. Repeat until arguments feel sharp, escalating, and assertive
5. The prompts are your ONLY competitive advantage — spend time here
```

---

## 6. Cost & Credits Summary

### Model recommendation
- **Use:** Claude Sonnet 4.6 (already in your config)
- **Buy:** Anthropic API credits at [console.anthropic.com](https://console.anthropic.com)
- **Amount:** $10 = ~₹835 (more than enough)
- **Do NOT switch to Perplexity** — your code is hardcoded for Anthropic SDK and the `web_search_20250305` tool. Switching requires a full engine rewrite the night before the match.

### Pricing breakdown (Claude Sonnet 4.6)
| Token Type | Rate |
|---|---|
| Input tokens | $3 / MTok |
| Output tokens | $15 / MTok |
| Web search | $10 / 1000 searches = **$0.01 per search** |
| Tool use overhead | +346 input tokens per call |

### Cost per match
| Component | Tokens/calls | Cost |
|---|---|---|
| Input (system + history + search results) | ~29,000 tokens | $0.087 |
| Output (~2600 chars × 10 turns) | ~6,500 tokens | $0.098 |
| Web search (2 searches × 10 turns) | 20 searches | $0.200 |
| **Total per match (realistic)** | | **~$0.38 = ₹32** |

### Full tournament scenarios
| Scenario | Cost (USD) | Cost (INR) |
|---|---|---|
| 3 matches (early exit) | $1.15 | ₹96 |
| 6 matches (mid run) | $2.31 | ₹193 |
| 12 matches (full round-robin) | $4.61 | ₹385 |
| Absolute worst (20 turns, 3 searches, 12 matches) | $12.27 | ₹1,025 |

> $10 credits covers the entire tournament in all realistic scenarios.  
> Only the absolute worst case (12 maxed-out matches) slightly exceeds $10.

---

## 7. Files Reference

| File | Purpose | Edit? |
|---|---|---|
| `agent.py` | Entry point. Run this. Use `--sandbox` for testing. | No |
| `src/ws_client.py` | WebSocket manager. Connect, auth, listen, reconnect, turn guard. | Only for Flaw 3 fix |
| `src/debate_engine.py` | Claude API caller. Retries, web search, trimming, cost tracking. | Yes — add fallback args, lower MAX_TOKENS |
| `src/strategy.py` | **THE most important file.** All prompt engineering lives here. | **YES — make all Section 3 changes** |
| `src/state_machine.py` | Match memory. Topic, stance, turn, history, phase, seconds. | Yes — fix phase detection (Flaw 2) |
| `src/config.py` | Reads `.env` variables. MAX_TOKENS lives here. | Yes — set MAX_TOKENS=900 |
| `src/debate_engine_demo.py` | Offline test version. Quick prompt testing without WebSocket. | No |
| `logs/` | Auto-generated per run. Use for latency, char counts, error debug. | No (read only) |
| `monitor/dashboard.html` | Browser live view. Open in Chrome during match. Bug fixed ✅ | No |
| `00_Project Guide/` | Rulebook, User Manual, mock_debate, this file. | No |

---

## 8. Priority Checklist

### 🔴 Before anything else
- [ ] Buy Anthropic API credits ($10) at [console.anthropic.com](https://console.anthropic.com)
- [ ] Set `ANTHROPIC_API_KEY` in `.env`
- [ ] Verify web search tool is enabled for your account (check Console)

### 🔴 Tonight — code changes (in order)
- [ ] Set `MAX_TOKENS = 900` in `.env` / `src/config.py`
- [ ] **Improvement 1:** Add "never reuse a URL" rule to `SYSTEM_BASE`
- [ ] **Improvement 2:** Add "never say while it's true" rule to `SYSTEM_BASE`
- [ ] **Improvement 3:** Add "escalate each round" to cross_examination + defense phases
- [ ] **Improvement 4:** Strengthen closing phase instruction with FINAL WARNING block
- [ ] **Improvement 5:** Add consistency check rule to `SYSTEM_BASE`
- [ ] **Improvement 6:** Add quoting requirement to rebuttal + cross_examination phases
- [ ] **Flaw 2:** Add `message_count >= 6` guard to closing phase trigger in `state_machine.py`
- [ ] **Flaw 4:** Add fallback argument strings to `debate_engine.py`

### 🟡 Testing (with friend)
- [ ] Run sandbox test — check latency in logs (target < 30s)
- [ ] Verify character counts (1800–2800 target)
- [ ] Confirm web search is firing (look for `web_search_requests` in logs)
- [ ] Read every output manually and score it
- [ ] Check for defensive phrases (`"while it's true"`, `"you're right that"`)
- [ ] Tweak `strategy.py`, re-run, repeat until sharp

### 🟢 Match day
- [ ] Open `monitor/dashboard.html` in Chrome before match starts
- [ ] Enter WebSocket URL + your team name in dashboard setup screen
- [ ] Run: `python agent.py` (no `--sandbox` flag for live match)
- [ ] Watch logs terminal + dashboard side by side
- [ ] **Do NOT touch the terminal during the match**
- [ ] After match: check `logs/` for usage summary and cost

---

*Last updated: March 28, 2026*
