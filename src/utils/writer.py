"""
Writes pipeline output to per-tab CSV files, one per required Google Sheet
tab: Startups, Products, Research Papers, Jobs, News, Entity Mapping Log.

Each CSV is flattened from the nested Pydantic schema (dot-notation columns,
e.g. content.entityName) so it pastes cleanly into Google Sheets or can be
imported with File > Import in Sheets. A small script (upload_to_sheets.py)
using gspread + a service account can push these directly via the Sheets API
if you want the pipeline to publish automatically rather than manual import.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any

from pydantic import BaseModel


def _flatten(obj: Any, prefix: str = "") -> dict:
    """Flatten a (possibly nested) dict/model into dot-notation keys."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump()
    flat = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        elif isinstance(v, list):
            flat[key] = json.dumps(v, default=str)
        else:
            flat[key] = v
    return flat


def write_csv(records: list[BaseModel], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not records:
        # Still create an empty file with no rows so the tab exists.
        with open(path, "w") as f:
            f.write("")
        return

    flat_rows = [_flatten(r) for r in records]
    fieldnames: list[str] = []
    for row in flat_rows:
        for k in row.keys():
            if k not in fieldnames:
                fieldnames.append(k)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)


def write_all_tabs(
    output_dir: str,
    *,
    startups: list = None,
    products: list = None,
    research_papers: list = None,
    jobs: list = None,
    news: list = None,
    entity_mapping_log: list = None,
) -> None:
    tabs = {
        "startups.csv": startups or [],
        "products.csv": products or [],
        "research_papers.csv": research_papers or [],
        "jobs.csv": jobs or [],
        "news.csv": news or [],
        "entity_mapping_log.csv": entity_mapping_log or [],
    }
    for filename, records in tabs.items():
        write_csv(records, os.path.join(output_dir, filename))
