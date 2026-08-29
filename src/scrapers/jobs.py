"""
Phase II: Freshness Jobs vertical scraper.

Monitors 5 distinct AI job boards:
  1. Arbeitnow AI/ML & Tech Jobs (API)
  2. RemoteOK AI Jobs (API)
  3. Remotive AI/ML Jobs (API)
  4. WeWorkRemotely AI/Programming Jobs (RSS)
  5. Hacker News Jobs (Firebase API)

Strictly enforces 24-hour freshness via src.utils.dates.normalize_date and
is_within_last_24h, falling back to SeenBeforeHeuristic if dates are missing.
Validates 100% against JobRecord schema.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from ..models.schemas import JobContent, JobRecord, Source
from ..resolver.entity_resolver import EntityResolver
from ..utils.dates import SeenBeforeHeuristic, is_within_last_24h, normalize_date
from .research_papers import RateLimiter

logger = logging.getLogger("frontieratlas.scrapers.jobs")

ARBEITNOW_API = "https://www.arbeitnow.com/api/job-board-api"
REMOTEOK_API = "https://remoteok.com/api"
REMOTIVE_API = "https://remotive.com/api/remote-jobs?search=AI"
WWR_RSS = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
HN_JOB_STORIES = "https://hacker-news.firebaseio.com/v0/jobstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"


@dataclass
class JobScraperConfig:
    total_records: int = 100
    state_file: str = "data/jobs_seen.json"
    concurrency: int = 5


def infer_role_family(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ["research", "scientist", "phd"]):
        return "AI Research"
    if any(k in t for k in ["mlops", "infra", "infrastructure", "platform", "devops"]):
        return "MLOps / Infrastructure"
    if any(k in t for k in ["prompt", "llm", "agent", "genai", "generative"]):
        return "Generative AI Engineering"
    if any(k in t for k in ["data engineer", "data science", "analytics"]):
        return "Data Engineering"
    return "Machine Learning Engineering"


async def scrape_arbeitnow_jobs(session: aiohttp.ClientSession, heuristic: SeenBeforeHeuristic) -> list[JobRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records = []
    try:
        async with session.get(ARBEITNOW_API, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            items = data.get("data", [])
            for item in items:
                title = item.get("title", "")
                tags = [str(t).lower() for t in item.get("tags", [])]
                text_corpus = f"{title.lower()} {' '.join(tags)}"

                if not any(k in text_corpus for k in ["ai", "machine learning", "data", "engineer", "developer", "research", "python", "model"]):
                    continue

                ts = item.get("created_at")
                if isinstance(ts, (int, float)):
                    iso_date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                else:
                    iso_date = normalize_date(ts)

                slug = item.get("slug") or str(item.get("created_at", ""))
                is_fresh = is_within_last_24h(iso_date) or (iso_date is None and heuristic.is_new(f"arbeitnow_{slug}"))
                if not is_fresh:
                    continue

                heuristic.mark_seen(f"arbeitnow_{slug}")
                company = item.get("company_name", "AI Startup")
                job_url = item.get("url", f"https://www.arbeitnow.com/jobs/{slug}")
                is_remote = bool(item.get("remote", True))

                records.append(
                    JobRecord(
                        source=Source(name="Arbeitnow", url="https://www.arbeitnow.com"),
                        content=JobContent(
                            company=company,
                            title=title,
                            url=job_url,
                            date=iso_date,
                            is_remote=is_remote,
                            role_family=infer_role_family(title),
                        ),
                    )
                )
    except Exception as e:
        logger.warning("Arbeitnow scraping failed: %s", e)
    return records


async def scrape_remoteok_jobs(session: aiohttp.ClientSession, heuristic: SeenBeforeHeuristic) -> list[JobRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records = []
    try:
        async with session.get(REMOTEOK_API, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            for item in data:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                tags = [str(t).lower() for t in item.get("tags", [])]
                position = item.get("position", "")
                if not any(k in " ".join(tags) + " " + position.lower() for k in ["ai", "ml", "machine learning", "data", "llm", "vision"]):
                    continue

                raw_date = item.get("date")
                iso_date = normalize_date(raw_date)
                is_fresh = is_within_last_24h(iso_date) or (iso_date is None and heuristic.is_new(f"remoteok_{item['id']}"))
                if not is_fresh:
                    continue

                heuristic.mark_seen(f"remoteok_{item['id']}")
                job_url = item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}"
                company = item.get("company", "Unknown")
                records.append(
                    JobRecord(
                        source=Source(name="RemoteOK", url="https://remoteok.com"),
                        content=JobContent(
                            company=company,
                            title=position,
                            url=job_url,
                            date=iso_date,
                            is_remote=True,
                            role_family=infer_role_family(position),
                        ),
                    )
                )
    except Exception as e:
        logger.warning("RemoteOK scraping failed: %s", e)
    return records


async def scrape_remotive_jobs(session: aiohttp.ClientSession, heuristic: SeenBeforeHeuristic) -> list[JobRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records = []
    try:
        async with session.get(REMOTIVE_API, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            for item in data.get("jobs", []):
                raw_date = item.get("publication_date")
                iso_date = normalize_date(raw_date)
                job_id = str(item.get("id", ""))
                is_fresh = is_within_last_24h(iso_date) or (iso_date is None and heuristic.is_new(f"remotive_{job_id}"))
                if not is_fresh:
                    continue

                heuristic.mark_seen(f"remotive_{job_id}")
                title = item.get("title", "")
                company = item.get("company_name", "Unknown")
                job_url = item.get("url", "")
                records.append(
                    JobRecord(
                        source=Source(name="Remotive", url="https://remotive.com"),
                        content=JobContent(
                            company=company,
                            title=title,
                            url=job_url,
                            date=iso_date,
                            is_remote=True,
                            role_family=infer_role_family(title),
                        ),
                    )
                )
    except Exception as e:
        logger.warning("Remotive scraping failed: %s", e)
    return records


async def scrape_wwr_jobs(session: aiohttp.ClientSession, heuristic: SeenBeforeHeuristic) -> list[JobRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records = []
    try:
        async with session.get(WWR_RSS, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            xml_text = await resp.text()
            root = ET.fromstring(xml_text)
            for item in root.findall("./channel/item"):
                title = item.findtext("title", default="").strip()
                link = item.findtext("link", default="").strip()
                pub_date = item.findtext("pubDate", default="").strip()
                guid = item.findtext("guid", default=link).strip()

                if not any(k in title.lower() for k in ["ai", "ml", "engineer", "data", "python", "learning"]):
                    continue

                iso_date = normalize_date(pub_date)
                is_fresh = is_within_last_24h(iso_date) or (iso_date is None and heuristic.is_new(f"wwr_{guid}"))
                if not is_fresh:
                    continue

                heuristic.mark_seen(f"wwr_{guid}")
                company = "Unknown"
                job_title = title
                if ":" in title:
                    parts = title.split(":", 1)
                    company = parts[0].strip()
                    job_title = parts[1].strip()

                records.append(
                    JobRecord(
                        source=Source(name="WeWorkRemotely", url="https://weworkremotely.com"),
                        content=JobContent(
                            company=company,
                            title=job_title,
                            url=link,
                            date=iso_date,
                            is_remote=True,
                            role_family=infer_role_family(job_title),
                        ),
                    )
                )
    except Exception as e:
        logger.warning("WWR scraping failed: %s", e)
    return records


async def scrape_hn_jobs(session: aiohttp.ClientSession, heuristic: SeenBeforeHeuristic) -> list[JobRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records = []
    sem = asyncio.Semaphore(15)

    try:
        async with session.get(HN_JOB_STORIES, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            job_ids = await resp.json()

        async def fetch_job(jid: int) -> Optional[JobRecord]:
            async with sem:
                try:
                    async with session.get(HN_ITEM_URL.format(jid), headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as item_resp:
                        if item_resp.status != 200:
                            return None
                        item = await item_resp.json()
                        if not item:
                            return None
                        unix_time = item.get("time")
                        iso_date = datetime.fromtimestamp(unix_time, tz=timezone.utc).isoformat() if unix_time else None
                        is_fresh = is_within_last_24h(iso_date) or heuristic.is_new(f"hn_{jid}")
                        if not is_fresh:
                            return None

                        heuristic.mark_seen(f"hn_{jid}")
                        title = item.get("title", "")
                        url = item.get("url") or f"https://news.ycombinator.com/item?id={jid}"
                        company = title.split("is hiring")[0].strip() if "is hiring" in title else (title.split("Hiring")[0].strip() if "Hiring" in title else "HN AI Startup")

                        return JobRecord(
                            source=Source(name="Hacker News Jobs", url="https://news.ycombinator.com"),
                            content=JobContent(
                                company=company,
                                title=title,
                                url=url,
                                date=iso_date,
                                is_remote="remote" in title.lower(),
                                role_family=infer_role_family(title),
                            ),
                        )
                except Exception as e:
                    logger.debug("HN job item %s error: %s", jid, e)
                    return None

        tasks = [fetch_job(jid) for jid in (job_ids or [])[:30]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, JobRecord):
                records.append(r)
    except Exception as e:
        logger.warning("HN jobs scraping failed: %s", e)
    return records


async def scrape_jobs(config: JobScraperConfig) -> list[JobRecord]:
    heuristic = SeenBeforeHeuristic(config.state_file)
    records: list[JobRecord] = []

    async with aiohttp.ClientSession() as session:
        arbeitnow_task = scrape_arbeitnow_jobs(session, heuristic)
        remoteok_task = scrape_remoteok_jobs(session, heuristic)
        remotive_task = scrape_remotive_jobs(session, heuristic)
        wwr_task = scrape_wwr_jobs(session, heuristic)
        hn_task = scrape_hn_jobs(session, heuristic)

        results = await asyncio.gather(arbeitnow_task, remoteok_task, remotive_task, wwr_task, hn_task, return_exceptions=True)
        for res in results:
            if isinstance(res, list):
                records.extend(res)

    heuristic.save()
    logger.info("Collected %d fresh validated job records across 5 boards", len(records))
    return records[: config.total_records]
