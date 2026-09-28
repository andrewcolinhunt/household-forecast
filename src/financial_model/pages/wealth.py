"""Saved wealth library page."""

from __future__ import annotations

from financial_model.app import _database_url
from financial_model.wealth_pages import wealth_page

wealth_page(_database_url())
