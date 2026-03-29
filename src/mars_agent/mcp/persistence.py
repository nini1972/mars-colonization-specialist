"""Runtime persistence backends for MCP transport state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_CORRUPTION_MARKERS: Final[tuple[str, ...]] = (
    "database disk image is malformed",
    "malformed",
    "not a database",
)


@dataclass(slots=True)
class IdempotencyEntry:
    tool_name: str
    fingerprint: str
    response: dict[str, object]
    completed_at_monotonic: float


@dataclass(slots=True)
class PersistenceSnapshot:
    completed_requests: dict[str, IdempotencyEntry]
    in_flight_requests: dict[str, tuple[str, str]]
    plan_correlation_by_id: dict[str, str]
    simulation_correlation_by_id: dict[str, str]


class PersistenceUnavailableError(RuntimeError):
    """Raised when persistence backend cannot be reached."""


class PersistenceCorruptionError(RuntimeError):
    """Raised when persistence backend appears corrupted."""


class SQLitePersistenceBackend:
    """SQLite-backed persistence for runtime request/correlation state."""

    schema_version: Final[int] = 1

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self._db_path), timeout=2.0)
        except sqlite3.Error as exc:
            raise self._translate_sqlite_error(exc) from exc
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_schema(self) -> None:
        try:
            with self._connect() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS completed_requests (
                        request_id TEXT PRIMARY KEY,
                        tool_name TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        response_json TEXT NOT NULL,
                        completed_at_monotonic REAL NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS in_flight_requests (
                        request_id TEXT PRIMARY KEY,
                        tool_name TEXT NOT NULL,
                        fingerprint TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS plan_correlations (
                        plan_id TEXT PRIMARY KEY,
                        correlation_id TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS simulation_correlations (
                        simulation_id TEXT PRIMARY KEY,
                        correlation_id TEXT NOT NULL
                    )
                    """
                )

                cursor.execute(
                    "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
                    ("schema_version", str(self.schema_version)),
                )
                cursor.execute("SELECT value FROM metadata WHERE key = ?", ("schema_version",))
                row = cursor.fetchone()
                if row is None:
                    raise PersistenceUnavailableError("Missing persistence schema_version metadata")
                version = row["value"]
                if version != str(self.schema_version):
                    raise PersistenceUnavailableError(
                        "Unsupported persistence schema version "
                        f"'{version}'. Expected '{self.schema_version}'."
                    )
        except sqlite3.Error as exc:
            raise self._translate_sqlite_error(exc) from exc

    def load_snapshot(self) -> PersistenceSnapshot:
        try:
            with self._connect() as connection:
                cursor = connection.cursor()

                completed_requests: dict[str, IdempotencyEntry] = {}
                for row in cursor.execute(
                    """
                    SELECT request_id, tool_name, fingerprint, response_json, completed_at_monotonic
                    FROM completed_requests
                    ORDER BY completed_at_monotonic ASC, request_id ASC
                    """
                ):
                    request_id = str(row["request_id"])
                    response = json.loads(str(row["response_json"]))
                    if not isinstance(response, dict):
                        raise PersistenceCorruptionError(
                            f"Persisted response for request_id '{request_id}' is not an object"
                        )
                    completed_requests[request_id] = IdempotencyEntry(
                        tool_name=str(row["tool_name"]),
                        fingerprint=str(row["fingerprint"]),
                        response={str(key): value for key, value in response.items()},
                        completed_at_monotonic=float(row["completed_at_monotonic"]),
                    )

                in_flight_requests: dict[str, tuple[str, str]] = {}
                for row in cursor.execute(
                    """
                    SELECT request_id, tool_name, fingerprint
                    FROM in_flight_requests
                    ORDER BY request_id ASC
                    """
                ):
                    in_flight_requests[str(row["request_id"])] = (
                        str(row["tool_name"]),
                        str(row["fingerprint"]),
                    )

                plan_correlation_by_id: dict[str, str] = {}
                for row in cursor.execute(
                    """
                    SELECT plan_id, correlation_id
                    FROM plan_correlations
                    ORDER BY plan_id ASC
                    """
                ):
                    plan_correlation_by_id[str(row["plan_id"])] = str(row["correlation_id"])

                simulation_correlation_by_id: dict[str, str] = {}
                for row in cursor.execute(
                    """
                    SELECT simulation_id, correlation_id
                    FROM simulation_correlations
                    ORDER BY simulation_id ASC
                    """
                ):
                    simulation_correlation_by_id[str(row["simulation_id"])] = str(
                        row["correlation_id"]
                    )

                return PersistenceSnapshot(
                    completed_requests=completed_requests,
                    in_flight_requests=in_flight_requests,
                    plan_correlation_by_id=plan_correlation_by_id,
                    simulation_correlation_by_id=simulation_correlation_by_id,
                )
        except sqlite3.Error as exc:
            raise self._translate_sqlite_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise PersistenceCorruptionError("Persisted response JSON is invalid") from exc

    def save_snapshot(self, snapshot: PersistenceSnapshot) -> None:
        try:
            with self._connect() as connection:
                cursor = connection.cursor()
                cursor.execute("DELETE FROM completed_requests")
                cursor.execute("DELETE FROM in_flight_requests")
                cursor.execute("DELETE FROM plan_correlations")
                cursor.execute("DELETE FROM simulation_correlations")

                for request_id in sorted(snapshot.completed_requests):
                    entry = snapshot.completed_requests[request_id]
                    response_json = json.dumps(
                        entry.response,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    cursor.execute(
                        """
                        INSERT INTO completed_requests (
                            request_id,
                            tool_name,
                            fingerprint,
                            response_json,
                            completed_at_monotonic
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            request_id,
                            entry.tool_name,
                            entry.fingerprint,
                            response_json,
                            entry.completed_at_monotonic,
                        ),
                    )

                for request_id in sorted(snapshot.in_flight_requests):
                    tool_name, fingerprint = snapshot.in_flight_requests[request_id]
                    cursor.execute(
                        """
                        INSERT INTO in_flight_requests (request_id, tool_name, fingerprint)
                        VALUES (?, ?, ?)
                        """,
                        (request_id, tool_name, fingerprint),
                    )

                for plan_id in sorted(snapshot.plan_correlation_by_id):
                    cursor.execute(
                        """
                        INSERT INTO plan_correlations (plan_id, correlation_id)
                        VALUES (?, ?)
                        """,
                        (plan_id, snapshot.plan_correlation_by_id[plan_id]),
                    )

                for simulation_id in sorted(snapshot.simulation_correlation_by_id):
                    cursor.execute(
                        """
                        INSERT INTO simulation_correlations (simulation_id, correlation_id)
                        VALUES (?, ?)
                        """,
                        (simulation_id, snapshot.simulation_correlation_by_id[simulation_id]),
                    )
        except sqlite3.Error as exc:
            raise self._translate_sqlite_error(exc) from exc

    def _translate_sqlite_error(self, exc: sqlite3.Error) -> RuntimeError:
        message = str(exc).lower()
        if any(marker in message for marker in _CORRUPTION_MARKERS):
            return PersistenceCorruptionError(str(exc))
        return PersistenceUnavailableError(str(exc))
