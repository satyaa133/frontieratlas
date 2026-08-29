"""
Uploads pipeline output CSVs directly to a public/shared Google Sheet using gspread.

Required 6 tabs:
  1. Startups
  2. Products
  3. Research Papers
  4. Jobs
  5. News
  6. Entity Mapping Log

Usage:
    python scripts/upload_to_sheets.py --sheet-name "FrontierAtlas Intelligence Graph" --creds service_account.json
    python scripts/upload_to_sheets.py --sheet-id <SPREADSHEET_ID>
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("upload_to_sheets")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TAB_FILES = {
    "Startups": "output/startups.csv",
    "Products": "output/products.csv",
    "Research Papers": "output/research_papers.csv",
    "Jobs": "output/jobs.csv",
    "News": "output/news.csv",
    "Entity Mapping Log": "output/entity_mapping_log.csv",
}


def authenticate(creds_path: str | None) -> gspread.Client:
    creds_file = creds_path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_file and os.path.exists(creds_file):
        credentials = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        return gspread.authorize(credentials)
    # Attempt gspread default auth / local auth
    try:
        return gspread.oauth()
    except Exception as e:
        logger.error(
            "Authentication failed. Please provide a Google Service Account JSON via "
            "--creds or GOOGLE_APPLICATION_CREDENTIALS: %s", e
        )
        sys.exit(1)


def upload_csvs_to_sheet(client: gspread.Client, sheet_name: str, sheet_id: str | None, make_public: bool):
    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
    else:
        try:
            spreadsheet = client.open(sheet_name)
        except gspread.SpreadsheetNotFound:
            logger.info("Creating new Google Spreadsheet: %s", sheet_name)
            spreadsheet = client.create(sheet_name)

    if make_public:
        spreadsheet.share(None, perm_type="anyone", role="reader")
        logger.info("Set spreadsheet permissions to public reader")

    existing_worksheets = {ws.title: ws for ws in spreadsheet.worksheets()}

    for tab_title, csv_path in TAB_FILES.items():
        if not os.path.exists(csv_path):
            logger.warning("CSV file not found: %s — skipping tab %s", csv_path, tab_title)
            continue

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        if not rows:
            logger.info("No rows in %s, writing empty header", csv_path)
            rows = [["(empty)"]]

        if tab_title in existing_worksheets:
            ws = existing_worksheets[tab_title]
            ws.clear()
        else:
            ws = spreadsheet.add_worksheet(title=tab_title, rows=max(len(rows) + 10, 100), cols=max(len(rows[0]) + 5, 10))

        ws.update(rows)
        logger.info("Uploaded %d rows to tab '%s'", len(rows), tab_title)

    # Remove default 'Sheet1' if other tabs exist and Sheet1 wasn't in our list
    if "Sheet1" in existing_worksheets and len(spreadsheet.worksheets()) > 1:
        try:
            spreadsheet.del_worksheet(existing_worksheets["Sheet1"])
        except Exception:
            pass

    print(f"\nSuccessfully updated Google Sheet!")
    print(f"Spreadsheet URL: {spreadsheet.url}\n")


def main():
    parser = argparse.ArgumentParser(description="Upload FrontierAtlas CSVs to Google Sheets")
    parser.add_argument("--sheet-name", default="FrontierAtlas Intelligence Graph", help="Title of Google Sheet")
    parser.add_argument("--sheet-id", default=None, help="Existing Google Sheet ID")
    parser.add_argument("--creds", default=None, help="Path to service account JSON credentials")
    parser.add_argument("--public", action="store_true", default=True, help="Make spreadsheet publicly readable")
    args = parser.parse_args()

    client = authenticate(args.creds)
    upload_csvs_to_sheet(client, args.sheet_name, args.sheet_id, args.public)


if __name__ == "__main__":
    main()
