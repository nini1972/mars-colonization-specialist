"""Persistent negotiation memory store for the central planner."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mars_agent.orchestration.models import CrossDomainConflict, KnowledgeContext, MissionGoal

_SCHEMA_VERSION = 2

_DDL_METADATA = """
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_DDL_OUTCOMES = """
CREATE TABLE IF NOT EXISTS negotiation_outcomes (
    conflict_fingerprint        TEXT PRIMARY KEY,
    knowledge_fingerprint       TEXT    NOT NULL DEFAULT '',
    isru_reduction_fraction     REAL    NOT NULL,
    crew_reduction              INTEGER NOT NULL,
    dust_degradation_adjustment REAL    NOT NULL,
    rationale                   TEXT    NOT NULL,
    accepted                    INTEGER NOT NULL,
    is_fallback                 INTEGER NOT NULL DEFAULT 0,
    stored_at                   TEXT    NOT NULL,
    hit_count                   INTEGER NOT NULL DEFAULT 0
)
"""


def _conflict_fingerprint(
    goal: MissionGoal,
    conflicts: tuple[CrossDomainConflict, ...],
    knowledge_context: KnowledgeContext | None = None,
) -> str:
    """Return the SHA-256 hex digest of a canonical conflict-situation representation.

    The fingerprint deliberately excludes ``mission_id`` and ``current_reduction``
    so that structurally identical situations share the same cache entry.
    """
    payload: dict[str, object] = {
        "phase": str(goal.current_phase),
        "crew_size": goal.crew_size,
        "solar_kw": round(goal.solar_generation_kw, 1),
        "battery_kwh": round(goal.battery_capacity_kwh, 1),
        "dust": round(goal.dust_degradation_fraction, 3),
        "hours_without_sun": round(goal.hours_without_sun, 1),
        "conflicts": sorted(c.conflict_id for c in conflicts),
    }
    if knowledge_context is not None:
        payload["knowledge"] = {
            "evidence_doc_ids": list(knowledge_context.evidence_doc_ids),
            "ontology_hints": list(knowledge_context.ontology_hints),
            "retrieval_doc_ids": [hit.doc_id for hit in knowledge_context.retrieval_hits],
            "trust_weight": round(knowledge_context.trust_weight, 3),
        }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NegotiationOutcome:
    """Persisted result of one negotiation resolution."""

    conflict_fingerprint: str
    isru_reduction_fraction: float
    crew_reduction: int
    dust_degradation_adjustment: float
    rationale: str
    accepted: bool
    is_fallback: bool  # True = deterministic fallback; False = LLM-accepted
    stored_at: str     # ISO-8601 datetime string
    hit_count: int
    knowledge_fingerprint: str = ""


class NegotiationMemoryStore:
    """Thread-safe in-memory (+ optional SQLite) negotiation outcome cache.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  When ``None`` (default) the store
        operates purely in-memory and nothing is persisted across process
        restarts.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, NegotiationOutcome] = {}
        self._db_path = db_path
        if db_path is not None:
            self._initialize_schema(db_path)
            self._load_from_sqlite(db_path)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _initialize_schema(self, db_path: Path) -> None:
        with sqlite3.connect(str(db_path), timeout=2.0) as conn:
            conn.execute(_DDL_METADATA)
            conn.execute(_DDL_OUTCOMES)
            row = conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES ('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
                conn.commit()
            else:
                stored = int(row[0])
                if stored != _SCHEMA_VERSION:
                    raise ValueError(
                        f"NegotiationMemoryStore schema version mismatch: "
                        f"expected {_SCHEMA_VERSION}, found {stored}"
                    )

    def _load_from_sqlite(self, db_path: Path) -> None:
        with sqlite3.connect(str(db_path), timeout=2.0) as conn:
            rows = conn.execute(
                "SELECT conflict_fingerprint, knowledge_fingerprint, "
                "isru_reduction_fraction, crew_reduction, "
                "dust_degradation_adjustment, rationale, accepted, is_fallback, "
                "stored_at, hit_count "
                "FROM negotiation_outcomes"
            ).fetchall()
        for row in rows:
            outcome = NegotiationOutcome(
                conflict_fingerprint=row[0],
                knowledge_fingerprint=row[1],
                isru_reduction_fraction=row[2],
                crew_reduction=row[3],
                dust_degradation_adjustment=row[4],
                rationale=row[5],
                accepted=bool(row[6]),
                is_fallback=bool(row[7]),
                stored_at=row[8],
                hit_count=row[9],
            )
            self._cache[outcome.conflict_fingerprint] = outcome

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, fingerprint: str) -> NegotiationOutcome | None:
        """Return the cached outcome for *fingerprint*, or ``None`` if absent."""
        with self._lock:
            return self._cache.get(fingerprint)

    def store(self, outcome: NegotiationOutcome) -> None:
        """Persist *outcome* in-memory and, if configured, in SQLite."""
        with self._lock:
            self._cache[outcome.conflict_fingerprint] = outcome
            if self._db_path is not None:
                self._upsert_sqlite(outcome)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upsert_sqlite(self, outcome: NegotiationOutcome) -> None:
        with sqlite3.connect(str(self._db_path), timeout=2.0) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO negotiation_outcomes "
                "(conflict_fingerprint, knowledge_fingerprint, isru_reduction_fraction, crew_reduction, "
                "dust_degradation_adjustment, rationale, accepted, is_fallback, "
                "stored_at, hit_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outcome.conflict_fingerprint,
                    outcome.knowledge_fingerprint,
                    outcome.isru_reduction_fraction,
                    outcome.crew_reduction,
                    outcome.dust_degradation_adjustment,
                    outcome.rationale,
                    int(outcome.accepted),
                    int(outcome.is_fallback),
                    outcome.stored_at,
                    outcome.hit_count,
                ),
            )
            conn.commit()


def now_utc_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (used by planner)."""
    return datetime.now(UTC).isoformat()
