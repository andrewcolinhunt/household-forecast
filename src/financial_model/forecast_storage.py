"""Immutable forecast runs and portable copies of their frozen inputs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from financial_model.forecast_models import SavedForecast
from financial_model.storage import _connect

MAX_FORECAST_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class HistoricalForecast:
    """Original historical JSON for inspection/export, never current-model execution."""

    id: UUID
    created_at: datetime
    schema_version: int
    model_version: str
    names: tuple[str, ...]
    document: dict[str, Any]

    def to_json(self) -> str:
        """Export the original stored values without injecting current defaults."""
        return json.dumps(self.document, indent=2)


def list_historical_forecasts(dsn: str) -> tuple[HistoricalForecast, ...]:
    """Read old immutable runs without parsing or upgrading their embedded Plans."""
    current = SavedForecast.model_fields["schema_version"].default
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM forecast_runs "
            "WHERE (document ->> 'schema_version')::integer < %s "
            "ORDER BY created_at DESC, id",
            (current,),
        ).fetchall()
    records = []
    for row in rows:
        raw = row["document"]
        if (
            raw.get("document_type") != "forecast"
            or type(raw.get("schema_version")) is not int
        ):
            msg = "Historical forecast has an invalid document envelope."
            raise ValueError(msg)
        records.append(
            HistoricalForecast(
                id=UUID(raw["id"]),
                created_at=datetime.fromisoformat(raw["created_at"]),
                schema_version=raw["schema_version"],
                model_version=raw["model_version"],
                names=tuple(p["name"] for p in raw["request"]["plans"]),
                document=raw,
            )
        )
    return tuple(records)


def create_forecast(dsn: str, document: SavedForecast) -> SavedForecast:
    """Insert a run once; an existing run's inputs and results cannot be replaced."""
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO forecast_runs (id, document, created_at) VALUES (%s, %s, %s)",
            (document.id, Jsonb(document.model_dump(mode="json")), document.created_at),
        )
    return document


def list_forecasts(dsn: str) -> tuple[SavedForecast, ...]:
    """Read supported runs in creation order, leaving older formats stored intact."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM forecast_runs "
            "WHERE document ->> 'schema_version' = %s ORDER BY created_at DESC, id",
            (str(SavedForecast.model_fields["schema_version"].default),),
        ).fetchall()
    return tuple(_read_document(row["document"]) for row in rows)


def _read_document(raw: Any) -> SavedForecast:
    """Reject unsupported frozen formats explicitly, without rewriting old runs."""
    if (
        not isinstance(raw, dict)
        or raw.get("document_type") != "forecast"
        or type(raw.get("schema_version")) is not int
    ):
        msg = "A forecast backup must declare its document type and schema version."
        raise ValueError(msg)
    current = SavedForecast.model_fields["schema_version"].default
    if raw["schema_version"] != current:
        msg = (
            f"Unsupported forecast schema version {raw['schema_version']}; "
            f"this app reads version {current}. The saved run is unchanged. "
            "Create a new forecast from updated Plans."
        )
        raise ValueError(msg)
    return SavedForecast.model_validate(raw)


def import_forecast_backup(data: bytes) -> SavedForecast:
    """Validate a portable run, preserving its identity, assumptions, and results.

    Args:
        data: An exported forecast JSON document, at most 10 MiB.

    Returns:
        The saved run; rerunning its request creates a separate run.

    Raises:
        ValueError: If the document exceeds the limit or fails validation.
    """
    if len(data) > MAX_FORECAST_BYTES:
        msg = "Forecast backup exceeds the 10 MB limit."
        raise ValueError(msg)
    return _read_document(json.loads(data))
