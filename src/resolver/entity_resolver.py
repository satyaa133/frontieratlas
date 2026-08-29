"""
Phase IV: Deterministic Entity Resolution.

Canonicalizes messy startup/product name strings ("OpenAI, Inc.", "Open AI")
to a single canonical form ("OpenAI"), using a tiered strategy:

  1. Exact match (case-folded) against canonical list
  2. Normalized match (strip legal suffixes, punctuation, whitespace)
  3. Alias table lookup (curated known aliases)
  4. Fuzzy match (rapidfuzz) above a confidence threshold
  5. Unresolved -> pass through raw name, logged for manual review

Every resolution -- successful or not -- produces an EntityMappingLogRecord
so the "Entity Mapping Log" output tab has full raw->canonical provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover
    fuzz = None
    process = None

LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|co\.?|gmbh|plc|labs?|technologies|"
    r"technology|ai|the)\b",
    re.IGNORECASE,
)
PUNCT = re.compile(r"[^\w\s]")
MULTISPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Aggressive normalization used only for MATCHING, not for display."""
    s = name.lower().strip()
    s = PUNCT.sub(" ", s)
    s = LEGAL_SUFFIXES.sub(" ", s)
    s = MULTISPACE.sub(" ", s).strip()
    return s


@dataclass
class EntityResolver:
    """
    canonical_seed: {"OpenAI": ["Open AI", "OpenAI Inc.", "OpenAI, Inc."], ...}
    Seed this with your ~50 known AI startups (see data/canonical_seed.json).
    """
    canonical_seed: dict[str, list[str]]
    fuzzy_threshold: float = 87.0

    _canonical_names: list[str] = field(init=False, default_factory=list)
    _alias_to_canonical: dict[str, str] = field(init=False, default_factory=dict)
    _normalized_to_canonical: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self):
        for canonical, aliases in self.canonical_seed.items():
            self._canonical_names.append(canonical)
            self._normalized_to_canonical[normalize_name(canonical)] = canonical
            self._alias_to_canonical[canonical.lower()] = canonical
            for alias in aliases:
                self._alias_to_canonical[alias.lower()] = canonical
                self._normalized_to_canonical[normalize_name(alias)] = canonical

    def resolve(self, raw_name: str) -> tuple[str, str, float]:
        """
        Returns (canonical_name, method, confidence).
        If nothing matches confidently, canonical_name == raw_name (cleaned)
        and method == "unresolved" -- we never force a bad match.
        """
        if not raw_name or not raw_name.strip():
            return raw_name, "unresolved", 0.0

        raw_clean = raw_name.strip()

        # 1. Exact (case-insensitive) alias/canonical match
        if raw_clean.lower() in self._alias_to_canonical:
            return self._alias_to_canonical[raw_clean.lower()], "exact", 1.0

        # 2. Normalized match (strips Inc./LLC/punctuation)
        norm = normalize_name(raw_clean)
        if norm in self._normalized_to_canonical:
            return self._normalized_to_canonical[norm], "normalized", 0.95

        # 3. Fuzzy match against canonical names + all aliases
        if process is not None and self._alias_to_canonical:
            choices = list(self._alias_to_canonical.keys())
            match = process.extractOne(
                raw_clean.lower(), choices, scorer=fuzz.WRatio
            )
            if match and match[1] >= self.fuzzy_threshold:
                matched_alias, score, _ = match
                return (
                    self._alias_to_canonical[matched_alias],
                    "fuzzy",
                    round(score / 100, 3),
                )

        # 4. Unresolved -- pass through, flagged for review
        return raw_clean, "unresolved", 0.0

    def register_new_canonical(self, name: str) -> None:
        """Allow the seed list to grow dynamically as new entities are seen."""
        if name not in self.canonical_seed:
            self.canonical_seed[name] = []
            self._canonical_names.append(name)
            self._alias_to_canonical[name.lower()] = name
            self._normalized_to_canonical[normalize_name(name)] = name
