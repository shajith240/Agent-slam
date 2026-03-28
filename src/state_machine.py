import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MatchState:
    team1: str = ""
    team2: str = ""
    topic: str = ""
    description: str = ""
    round: str = ""
    finish_time: int = 0
    pros: str = ""
    cons: str = ""
    turn: str = ""
    status: str = "pending"
    remaining_time: int = 0
    conversation: list = field(default_factory=list)
    our_team: str = ""
    turn_start_time: float = 0.0
    message_count: int = 0

    @property
    def our_stance(self) -> str:
        if self.our_team == self.pros:
            return "PRO"
        return "CON"

    @property
    def is_our_turn(self) -> bool:
        return self.turn == self.our_team and self.status == "started"

    @property
    def seconds_remaining_in_match(self) -> int:
        if self.finish_time == 0:
            return 999
        now_ms = int(time.time() * 1000)
        remaining = (self.finish_time - now_ms) / 1000
        return max(0, int(remaining))

    @property
    def seconds_on_our_turn(self) -> float:
        if self.turn_start_time == 0.0:
            return 0.0
        return time.time() - self.turn_start_time

    @property
    def debate_phase(self) -> str:
        if self.message_count == 0:
            return "opening"
        if self.seconds_remaining_in_match < 180 and self.message_count >= 4:
            return "closing"
        if self.message_count == 1:
            return "rebuttal_first"
        if self.message_count <= 3:
            return "cross_examination"
        return "defense"

    def update_from_match_state(self, data: dict) -> None:
        self.team1 = data.get("team1", self.team1)
        self.team2 = data.get("team2", self.team2)
        self.topic = data.get("topic", self.topic)
        self.description = data.get("description", self.description)
        self.round = data.get("round", self.round)
        self.finish_time = data.get("finishTime", self.finish_time)
        self.pros = data.get("pros", self.pros)
        self.cons = data.get("cons", self.cons)
        self.status = data.get("status", self.status)
        self.remaining_time = data.get("remainingTime", self.remaining_time)

        old_turn = self.turn
        self.turn = data.get("turn", self.turn)

        if self.turn == self.our_team and old_turn != self.our_team:
            self.turn_start_time = time.time()

    def update_from_match_update(self, data: dict) -> None:
        self.finish_time = data.get("finishTime", self.finish_time)
        self.status = "started"

    def record_opponent_message(self, team: str, message: str, timestamp: str) -> None:
        self.conversation.append({
            "team": team,
            "message": message,
            "timestamp": timestamp,
            "is_ours": False,
        })

    def record_our_message(self, message: str) -> None:
        self.conversation.append({
            "team": self.our_team,
            "message": message,
            "timestamp": str(int(time.time() * 1000)),
            "is_ours": True,
        })
        self.message_count += 1
        self.turn_start_time = 0.0

    def load_previous_messages(self, conversations: list) -> None:
        for entry in conversations:
            is_ours = entry.get("team") == self.our_team
            self.conversation.append({
                "team": entry.get("team", ""),
                "message": entry.get("message", ""),
                "timestamp": entry.get("timestamp", ""),
                "is_ours": is_ours,
            })
        self.message_count = sum(1 for msg in self.conversation if msg["is_ours"])

    def last_opponent_message(self) -> Optional[str]:
        for msg in reversed(self.conversation):
            if not msg["is_ours"]:
                return msg["message"]
        return None

    def conversation_as_text(self, last_n: int = 8) -> str:
        recent = self.conversation[-last_n:]
        lines = []
        for msg in recent:
            label = "[US]" if msg["is_ours"] else "[OPPONENT]"
            lines.append(f"{label}: {msg['message']}")
        return "\n".join(lines)

    def reset(self) -> None:
        our_team = self.our_team
        self.team1 = ""
        self.team2 = ""
        self.topic = ""
        self.description = ""
        self.round = ""
        self.finish_time = 0
        self.pros = ""
        self.cons = ""
        self.turn = ""
        self.status = "pending"
        self.remaining_time = 0
        self.conversation = []
        self.our_team = our_team
        self.turn_start_time = 0.0
        self.message_count = 0
