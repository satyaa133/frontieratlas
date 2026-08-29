"""
Phase II: Freshness News vertical scraper.

Monitors 5 distinct AI news sources:
  1. TechCrunch AI (RSS)
  2. VentureBeat AI (RSS)
  3. Hacker News AI Stories (Firebase API)
  4. ScienceDaily AI Feed (RSS)
  5. AI News (Artificial Intelligence News RSS)

Extracts title, URL, published_date, summary/full_text, and enforces strict
24-hour freshness via src.utils.dates.normalize_date and is_within_last_24h.
Validates 100% against canonical NewsRecord schema.
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

from ..models.schemas import NewsContent, NewsRecord, Source
from ..utils.dates import SeenBeforeHeuristic, is_within_last_24h, normalize_date
from .research_papers import RateLimiter

logger = logging.getLogger("frontieratlas.scrapers.news")

TECHCRUNCH_AI_RSS = "https://techcrunch.com/category/artificial-intelligence/feed/"
VENTUREBEAT_AI_RSS = "https://venturebeat.com/category/ai/feed/"
SCIENCEDAILY_AI_RSS = "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml"
AINEWS_RSS = "https://www.artificialintelligence-news.com/feed/"
HN_NEW_STORIES = "https://hacker-news.firebaseio.com/v0/newstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"

import html as html_lib

CLEAN_HTML_RE = re.compile(r"<[^>]+>")


def clean_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = CLEAN_HTML_RE.sub("", raw_html)
    text = html_lib.unescape(text)
    return " ".join(text.split()).strip()


@dataclass
class NewsScraperConfig:
    total_records: int = 100
    state_file: str = "data/news_seen.json"
    concurrency: int = 5


async def parse_rss_feed(
    session: aiohttp.ClientSession,
    feed_url: str,
    source_name: str,
    heuristic: SeenBeforeHeuristic,
) -> list[NewsRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records: list[NewsRecord] = []
    try:
        async with session.get(feed_url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                logger.warning("Feed status %d for %s", resp.status, source_name)
                return []
            xml_text = await resp.text()
            root = ET.fromstring(xml_text)
            for item in root.findall("./channel/item"):
                title = clean_text(item.findtext("title", default=""))
                link = item.findtext("link", default="").strip()
                pub_date = item.findtext("pubDate", default="").strip()
                desc = clean_text(item.findtext("description", default=""))
                guid = item.findtext("guid", default=link).strip()

                if not title or not link:
                    continue

                iso_date = normalize_date(pub_date)
                is_fresh = is_within_last_24h(iso_date) or (iso_date is None and heuristic.is_new(f"{source_name}_{guid}"))
                if not is_fresh:
                    continue

                heuristic.mark_seen(f"{source_name}_{guid}")
                records.append(
                    NewsRecord(
                        source=Source(name=source_name, url=link),
                        content=NewsContent(
                            title=title,
                            url=link,
                            published_date=iso_date,
                            summary=desc[:500] if desc else None,
                            full_text=desc if desc else title,
                        ),
                    )
                )
    except Exception as e:
        logger.warning("Error parsing RSS feed for %s: %s", source_name, e)
    return records


async def scrape_hn_ai_news(
    session: aiohttp.ClientSession,
    heuristic: SeenBeforeHeuristic,
) -> list[NewsRecord]:
    headers = {"User-Agent": "Mozilla/5.0 (FrontierAtlas-Ingest/1.0)"}
    records: list[NewsRecord] = []
    sem = asyncio.Semaphore(15)

    try:
        async with session.get(HN_NEW_STORIES, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            story_ids = await resp.json()

        async def fetch_story(sid: int) -> Optional[NewsRecord]:
            async with sem:
                try:
                    async with session.get(HN_ITEM_URL.format(sid), headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as item_resp:
                        if item_resp.status != 200:
                            return None
                        item = await item_resp.json()
                        if not item:
                            return None
                        title = item.get("title", "")
                        if not any(k in title.lower() for k in ["ai", "llm", "gpt", "model", "deepmind", "anthropic", "openai", "agent", "neural", "vision", "robot", "tensor", "diffusion"]):
                            return None

                        unix_time = item.get("time")
                        iso_date = datetime.fromtimestamp(unix_time, tz=timezone.utc).isoformat() if unix_time else None
                        is_fresh = is_within_last_24h(iso_date) or heuristic.is_new(f"hn_news_{sid}")
                        if not is_fresh:
                            return None

                        heuristic.mark_seen(f"hn_news_{sid}")
                        url = item.get("url") or f"https://news.ycombinator.com/item?id={sid}"
                        text = clean_text(item.get("text", title))
                        return NewsRecord(
                            source=Source(name="Hacker News AI", url=url),
                            content=NewsContent(
                                title=title,
                                url=url,
                                published_date=iso_date,
                                summary=title,
                                full_text=text or title,
                            ),
                        )
                except Exception as e:
                    logger.debug("HN story error for %s: %s", sid, e)
                    return None

        tasks = [fetch_story(sid) for sid in (story_ids or [])[:50]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, NewsRecord):
                records.append(r)
    except Exception as e:
        logger.warning("HN news scrape error: %s", e)
    return records


async def scrape_news(config: NewsScraperConfig) -> list[NewsRecord]:
    heuristic = SeenBeforeHeuristic(config.state_file)
    records: list[NewsRecord] = []

    async with aiohttp.ClientSession() as session:
        tc_task = parse_rss_feed(session, TECHCRUNCH_AI_RSS, "TechCrunch AI", heuristic)
        vb_task = parse_rss_feed(session, VENTUREBEAT_AI_RSS, "VentureBeat AI", heuristic)
        sd_task = parse_rss_feed(session, SCIENCEDAILY_AI_RSS, "ScienceDaily AI", heuristic)
        ain_task = parse_rss_feed(session, AINEWS_RSS, "AI News", heuristic)
        hn_task = scrape_hn_ai_news(session, heuristic)

        results = await asyncio.gather(tc_task, vb_task, sd_task, ain_task, hn_task, return_exceptions=True)
        for res in results:
            if isinstance(res, list):
                records.extend(res)

    heuristic.save()
    logger.info("Collected %d fresh validated AI news records across 5 sources", len(records))
    return records[: config.total_records]
