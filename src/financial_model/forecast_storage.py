"""Immutable forecast runs and portable copies of their frozen inputs."""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any

from financial_model.forecast_models import SavedForecast
from financial_model.storage import _connect

MAX_FORECAST_BYTES = 10 * 1024 * 1024


def create_forecast(dsn: str, document: SavedForecast) -> SavedForecast:
    """Insert a run once; an existing run's inputs and results cannot be replaced."""
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO forecast_runs (id, document, created_at) VALUES (?, ?, ?)",
            (
                str(document.id),
                document.model_dump_json(),
                document.created_at.astimezone(UTC).isoformat(),
            ),
        )
    return document


def list_forecasts(dsn: str) -> tuple[SavedForecast, ...]:
    """Read current-format runs in reverse creation order."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM forecast_runs "
            "WHERE document ->> 'schema_version' = ? ORDER BY created_at DESC, id",
            (SavedForecast.model_fields["schema_version"].default,),
        ).fetchall()
    return tuple(_read_document(json.loads(row["document"])) for row in rows)


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
            f"this app reads version {current}. "
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
