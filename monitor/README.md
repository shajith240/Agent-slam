# Match Monitor Dashboard

Real-time spectator UI for Agent SLAM 2026 matches.

## What is this?

A standalone HTML dashboard that connects to the same WebSocket server as the agent and displays live match data: turns, arguments, phase tracking, timer, and conversation feed.

It is **read-only** — it only listens to messages, never sends anything. Safe to run alongside the agent during a live match.

## How to use

1. Open `dashboard.html` in any modern browser
2. Enter the WebSocket URL (same one in your `.env`)
3. Enter your team name (e.g. `team1` or `team2`)
4. Click **Connect**

Settings are saved in localStorage and persist across refreshes.

## Features

- Live turn indicator with animated borders
- Scrolling message feed with color-coded bubbles
- Debate phase tracker (Opening → Rebuttal → Cross-Exam → Defense → Closing)
- Match countdown timer with warning at 60 seconds
- Connection status with auto-reconnect detection
- Match completion overlay with final stats
