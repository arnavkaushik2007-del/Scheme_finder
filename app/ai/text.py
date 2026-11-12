"""Shared tokenisation for retrieval and the hallucination guard (English + Hindi)."""

from __future__ import annotations

import re

STOP = {"the", "and", "for", "what", "how", "can", "who", "are", "is", "a", "an", "of", "to", "in", "on", "me", "my",
         "i", "do", "does", "get", "scheme", "yojana", "क्या", "है", "हैं", "के", "की", "का", "में", "से", "को", "और",
         "मैं", "मुझे", "कैसे", "योजना", "लिए", "कौन", "पर", "भी"}


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\wऀ-ॿ]+", text.lower()) if t not in STOP and len(t) > 1]
