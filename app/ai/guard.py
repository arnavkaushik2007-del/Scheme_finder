"""Hallucination guard: every sentence of an answer must cite a retrieved source and be supported by it.

Checks, per sentence:
1. Citation check - it cites at least one valid source id like [S2].
2. Number check - every number in the sentence (amounts, ages, percentages) appears in a cited source.
3. Support check - entailment by an LLM judge when a real model is configured; otherwise a lexical
   check (share of the sentence's content words found in the cited sources).
Unsupported sentences are removed. If too little survives, or confidence is low, the assistant abstains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.index import tokens
from app.ai.llm import get_llm

CITE_RE = re.compile(r"\[S(\d+)\]")
SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


# A full stop after a lone digit ("1. Visit ...") is list numbering, not a sentence end.
_SENT_RE = re.compile(r".+?(?<!\b\d)[.!?।](?:\s*\[S\d+\])*(?=\s|$)|.+?$", re.S)


def split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping citations that follow the full stop ("... rule. [S2]")."""
    out = []
    for line in re.split(r"\n+", text):
        out += [m.group(0).strip() for m in _SENT_RE.finditer(line.strip()) if m.group(0).strip()]
    return out


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM_RE.findall(CITE_RE.sub("", text))}


@dataclass
class SentenceCheck:
    text: str
    cites: list[int]
    has_citation: bool
    numbers_ok: bool
    support: float
    entailed: bool | None
    ok: bool


@dataclass
class GuardResult:
    sentences: list[SentenceCheck] = field(default_factory=list)
    supported_fraction: float = 0.0
    method: str = "lexical"

    @property
    def kept(self) -> list[SentenceCheck]:
        return [s for s in self.sentences if s.ok]

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "supported_fraction": round(self.supported_fraction, 3),
                "sentences": [{"text": s.text, "cites": s.cites, "has_citation": s.has_citation,
                               "numbers_ok": s.numbers_ok, "support": round(s.support, 3), "entailed": s.entailed,
                               "ok": s.ok} for s in self.sentences]}


NLI_SYSTEM = ("You check whether claims are fully supported by the given source passages. A claim is supported only "
              "if the passages state it (paraphrase allowed). Anything extra, guessed, or contradicted is "
              "unsupported. Return JSON.")


async def _llm_entailment(checks: list[SentenceCheck], sources: dict[int, str]) -> list[bool] | None:
    llm = get_llm()
    if llm.name == "mock":
        return None
    items = "\n".join(f"{i}. CLAIM: {c.text}\n   SOURCES: " + " | ".join(sources.get(n, "") for n in c.cites)
                      for i, c in enumerate(checks))
    schema = {"type": "object", "properties": {"supported": {"type": "array", "items": {"type": "boolean"}}},
              "required": ["supported"], "additionalProperties": False}
    res = await llm.complete_json(NLI_SYSTEM, f"For each numbered claim, is it supported?\n\n{items}", schema,
                                  max_tokens=800)
    flags = res.data.get("supported") if not res.error else None
    if not isinstance(flags, list) or len(flags) != len(checks):
        return None
    return [bool(f) for f in flags]


async def check(answer: str, sources: dict[int, str], *, lexical_min: float = 0.5) -> GuardResult:
    checks: list[SentenceCheck] = []
    for sent in split_sentences(answer):
        cites = [int(n) for n in CITE_RE.findall(sent) if int(n) in sources]
        bare = CITE_RE.sub("", sent).strip()
        if not bare:
            continue
        cited_text = " ".join(sources[n] for n in cites)
        nums_ok = _numbers(bare) <= _numbers(cited_text) if cites else False
        words = set(tokens(bare))
        support = len(words & set(tokens(cited_text))) / len(words) if words and cites else 0.0
        checks.append(SentenceCheck(bare, cites, bool(cites), nums_ok, support, None, False))

    result = GuardResult(checks)
    entailed = await _llm_entailment([c for c in checks if c.has_citation], sources) \
        if any(c.has_citation for c in checks) else None
    if entailed is not None:
        result.method = "llm-entailment+numbers"
        it = iter(entailed)
        for c in checks:
            if c.has_citation:
                c.entailed = next(it)
    for c in checks:
        if c.entailed is not None:
            c.ok = c.has_citation and c.numbers_ok and c.entailed
        else:
            c.ok = c.has_citation and c.numbers_ok and c.support >= lexical_min
    result.supported_fraction = (sum(c.ok for c in checks) / len(checks)) if checks else 0.0
    return result
