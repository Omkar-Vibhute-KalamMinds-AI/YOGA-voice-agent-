"""Checks around the main model. Secrets stay out of the prompt, and untrusted text is data."""

from __future__ import annotations

import os
import re

REFUSAL = "I can't follow that request. Ask me something else and I'll help."
SAFE_REPLY = "I can't share that. Ask me something else and I'll help."

_JAILBREAK = re.compile(
    r"("
    r"ignore\s+(all\s+|any\s+|your\s+)?(previous|prior|above)\s+(instructions|rules|prompts)"
    r"|disregard\s+(your|the)\s+(system|previous|developer)"
    r"|reveal\s+(your|the)\s+(system\s+prompt|hidden\s+prompt|instructions|rules)"
    r"|print\s+(your|the)\s+system\s+prompt"
    r"|what\s+is\s+your\s+system\s+prompt"
    r"|you\s+are\s+now\s+(dan|developer\s+mode|unrestricted|jailbroken)"
    r"|(enter|enable|activate)\s+(dan|developer)\s+mode"
    r"|bypass\s+(your|the)\s+(safety|rules|guard|filter)"
    r"|do\s+not\s+follow\s+(your|the)\s+(rules|system|instructions)"
    r")",
    re.IGNORECASE,
)

_PARTIAL_SECRET = re.compile(
    r"(sk-[A-Za-z0-9_\-]{0,48}|bearer\s+[A-Za-z0-9._\-]{0,48}|api[_-]?key\s*[:=]\s*\S{0,24})$",
    re.IGNORECASE,
)

_SECRET = re.compile(
    r"("
    r"sk-[A-Za-z0-9_\-]{12,}"
    r"|api[_-]?key\s*[:=]\s*\S+"
    r"|bearer\s+[A-Za-z0-9._\-]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")",
    re.IGNORECASE,
)

_EXFIL = re.compile(
    r"!?\[[^\]]*\]\(\s*(?:https?:|data:)[^)]+\)"
    r"|data:[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+;base64,"
    r"|https?://\S*(?:token|key|secret|password)=",
    re.IGNORECASE,
)

_PROMPT_ECHO = (
    "you are yoga, a helpful spoken voice agent",
    "anything inside <untrusted_document>",
    "anything inside <untrusted_tool_output>",
)


def screen_input(text: str) -> str | None:
    """Return a refusal when the user message is an instruction attack. Otherwise None."""
    if _JAILBREAK.search(text or ""):
        return REFUSAL
    return None


def isolate(text: str, tag: str) -> str:
    """Mark retrieved or tool text as data the model must not obey."""
    body = (text or "").replace(f"</{tag}>", "").replace(f"<{tag}>", "")
    body = _SECRET.sub("[redacted]", body)
    return f"<{tag}>\n{body}\n</{tag}>"


def _redact(text: str) -> str:
    cleaned = _SECRET.sub("[redacted]", text)
    cleaned = _EXFIL.sub("[removed link]", cleaned)
    lowered = cleaned.lower()
    if any(fragment in lowered for fragment in _PROMPT_ECHO):
        return SAFE_REPLY
    return cleaned


class OutputGuard:
    """Hold back a short tail so a secret split across tokens can still be removed."""

    def __init__(self, hold: int = 160) -> None:
        self.hold = hold
        self._pending = ""
        self._all = ""
        self._blocked = False
        self._emitted = False

    def feed(self, text: str) -> str:
        piece = text or ""
        self._all += piece
        if self._blocked:
            return ""
        if any(fragment in self._all.lower() for fragment in _PROMPT_ECHO):
            self._blocked = True
            self._pending = ""
            return ""
        self._pending += piece
        if len(self._pending) <= self.hold:
            return ""
        ready = self._pending[:-self.hold]
        self._pending = self._pending[-self.hold:]
        partial = _PARTIAL_SECRET.search(ready)
        if partial:
            self._pending = ready[partial.start():] + self._pending
            ready = ready[:partial.start()]
        ready = _redact(ready)
        if ready:
            self._emitted = True
        return ready

    def finish(self) -> str:
        if self._blocked:
            return "" if self._emitted else SAFE_REPLY
        tail = _redact(self._pending)
        self._pending = ""
        return tail


def prompt_has_secret(prompt: str) -> bool:
    """The system prompt must not carry credentials, even from memory."""
    if _SECRET.search(prompt or ""):
        return True
    for name in ("NVIDIA_API_KEY", "SARVAM_API_KEY", "GEMINI_API_KEY_3", "OPENAI_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if len(value) >= 8 and value in prompt:
            return True
    return False
