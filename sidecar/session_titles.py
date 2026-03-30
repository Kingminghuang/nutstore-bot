from __future__ import annotations


def build_heuristic_title(text: str) -> str:
    words = text.strip().split()
    if not words:
        return "New session"
    title = " ".join(words[:8]).strip()
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    return title or "New session"


def build_first_user_message_fallback_title(text: str, *, max_chars: int = 50) -> str:
    if max_chars <= 0:
        return "New session"

    normalized = " ".join(text.split())
    if not normalized:
        return "New session"
    return normalized[:max_chars].strip() or "New session"
