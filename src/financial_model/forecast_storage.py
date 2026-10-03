"""Immutable forecast runs and portable copies of their frozen inputs."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from financial_model.forecast_models import SavedForecast
from financial_model.storage import _connect

MAX_FORECAST_BYTES = 10 * 1024 * 1024


def create_forecast(dsn: str, document: SavedForecast) -> SavedForecast:
    """Insert a run once; an existing run's inputs and results cannot be replaced."""
    with _connect(dsn) as connection:
        connection.execute(
            "INSERT INTO forecast_runs (id, document, created_at) VALUES (%s, %s, %s)",
            (document.id, Jsonb(document.model_dump(mode="json")), document.created_at),
        )
    return document


def list_forecasts(dsn: str) -> tuple[SavedForecast, ...]:
    """Read saved runs in creation order without changing any source Plans."""
    with _connect(dsn) as connection:
        rows = connection.execute(
            "SELECT document FROM forecast_runs ORDER BY created_at DESC, id"
        ).fetchall()
    return tuple(SavedForecast.model_validate(row["document"]) for row in rows)


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
    raw = json.loads(data)
    if (
        not isinstance(raw, dict)
        or raw.get("document_type") != "forecast"
        or type(raw.get("schema_version")) is not int
    ):
        msg = "A forecast backup must declare its document type and schema version."
        raise ValueError(msg)
    return SavedForecast.model_validate(raw)
