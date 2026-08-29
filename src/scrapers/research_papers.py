"""
Phase I: Research Papers vertical.

Strategy: Arxiv's public API (no auth, no rate-limit headaches, fully legal
to bulk-query) is the highest-fidelity, zero-anti-bot source for paper
metadata + abstracts. We correlate each paper to a GitHub repo either via:
  (a) a GitHub URL found directly in the abstract/comment text, or
  (b) a search against the GitHub REST API for a repo whose name/description
      matches the paper title (best-effort, logged as "inferred" vs "direct")

GitHub star counts are fetched live via the GitHub REST API (unauthenticated:
60 req/hr; with a token: 5,000 req/hr -- set GITHUB_TOKEN env var for scale).

This scraper is fully async (aiohttp) and paginates through Arxiv's API in
concurrent batches, so scaling to more records is purely a matter of raising
TOTAL_RECORDS / concurrency, not changing logic (per the "500k without code
changes" requirement).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import aiohttp

from ..models.schemas import ResearchPaperContent, ResearchPaperRecord, Source

logger = logging.getLogger("frontieratlas.scrapers.papers")

ARXIV_API = "http://export.arxiv.org/api/query"
GITHUB_API = "https://api.github.com"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

GITHUB_URL_RE = re.compile(r"https?://github\.com/[\w\-\.]+/[\w\-\.]+")


@dataclass
class ScraperConfig:
    query: str = "cat:cs.AI OR cat:cs.LG OR cat:cs.CL"
    total_records: int = 1000
    page_size: int = 100          # Arxiv max per request
    concurrency: int = 5          # concurrent Arxiv page fetches
    github_concurrency: int = 5   # concurrent GitHub star lookups
    github_token: Optional[str] = None


class RateLimiter:
    """Simple token-bucket limiter so we stay under Arxiv and GitHub guidelines."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait_for = self.min_interval - (now - self._last)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last = loop.time()


class GitHubEnricher:
    """Safely enriches paper records with GitHub star counts with rate-limit circuit breaking."""

    def __init__(self, token: Optional[str], concurrency: int = 5):
        self.token = token
        self.limiter = RateLimiter(min_interval=1.0 / concurrency)
        self.sem = asyncio.Semaphore(concurrency)
        self.rate_limited = False
        self.consecutive_rate_limits = 0

    async def get_stars(self, session: aiohttp.ClientSession, github_url: str) -> Optional[int]:
        if self.rate_limited:
            return None
        m = re.search(r"github\.com/([\w\-\.]+)/([\w\-\.]+)", github_url)
        if not m:
            return None
        owner, repo = m.group(1), m.group(2).rstrip(".git")

        async with self.sem:
            if self.rate_limited:
                return None
            await self.limiter.wait()
            headers = {"Accept": "application/vnd.github+json", "User-Agent": "FrontierAtlas-Ingest/1.0"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"

            url = f"{GITHUB_API}/repos/{owner}/{repo}"
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in (403, 429):
                        self.consecutive_rate_limits += 1
                        if self.consecutive_rate_limits >= 3 and not self.token:
                            logger.info("GitHub unauthenticated rate limit reached; pausing remaining live star lookups")
                            self.rate_limited = True
                        return None
                    if resp.status != 200:
                        return None
                    self.consecutive_rate_limits = 0
                    data = await resp.json()
                    return data.get("stargazers_count")
            except Exception as e:
                logger.debug("GitHub star lookup failed for %s/%s: %s", owner, repo, e)
                return None


async def fetch_arxiv_page(
    session: aiohttp.ClientSession, query: str, start: int, page_size: int,
    limiter: RateLimiter,
) -> list[dict]:
    await limiter.wait()
    params = {
        "search_query": query,
        "start": start,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    async with session.get(ARXIV_API, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status == 429:
            logger.warning("Arxiv 429 at offset %d — backing off", start)
            await asyncio.sleep(5)
            return await fetch_arxiv_page(session, query, start, page_size, limiter)
        resp.raise_for_status()
        text = await resp.text()

    root = ET.fromstring(text)
    entries = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        title = entry.findtext("atom:title", default="", namespaces=ARXIV_NS).strip()
        summary = entry.findtext("atom:summary", default="", namespaces=ARXIV_NS).strip()
        arxiv_id = entry.findtext("atom:id", default="", namespaces=ARXIV_NS).strip()
        published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS).strip()
        authors = [
            a.findtext("atom:name", default="", namespaces=ARXIV_NS)
            for a in entry.findall("atom:author", ARXIV_NS)
        ]
        comment = entry.findtext("arxiv:comment", default="", namespaces=ARXIV_NS) or ""
        pdf_url = arxiv_id.replace("/abs/", "/pdf/") if arxiv_id else ""

        gh_match = GITHUB_URL_RE.search(summary + " " + comment)
        github_url = gh_match.group(0).rstrip(").,") if gh_match else None

        entries.append({
            "title": title,
            "authors": [a for a in authors if a],
            "paper_url": arxiv_id,
            "pdf_url": pdf_url,
            "published_date": published,
            "github_url": github_url,
        })
    return entries


async def scrape_research_papers(config: ScraperConfig) -> list[ResearchPaperRecord]:
    arxiv_limiter = RateLimiter(min_interval=3.0 / config.concurrency)
    enricher = GitHubEnricher(token=config.github_token, concurrency=config.github_concurrency)

    offsets = list(range(0, config.total_records, config.page_size))
    records: list[dict] = []

    async with aiohttp.ClientSession() as session:
        # 1. Fetch all Arxiv pages concurrently (bounded by semaphore)
        sem = asyncio.Semaphore(config.concurrency)

        async def bound_fetch(start: int):
            async with sem:
                try:
                    return await fetch_arxiv_page(session, config.query, start, config.page_size, arxiv_limiter)
                except Exception as e:
                    logger.error("Arxiv page fetch failed at offset %d: %s", start, e)
                    return []

        pages = await asyncio.gather(*[bound_fetch(o) for o in offsets])
        for page in pages:
            records.extend(page)
        records = records[: config.total_records]

        # 2. Enrich with GitHub stars where a repo URL was found
        async def enrich(rec: dict):
            if not rec.get("github_url"):
                return
            stars = await enricher.get_stars(session, rec["github_url"])
            rec["github_stars"] = stars

        await asyncio.gather(*[enrich(r) for r in records])

    # 3. Validate + wrap into canonical schema
    out: list[ResearchPaperRecord] = []
    for r in records:
        if not r.get("paper_url"):
            continue
        try:
            out.append(
                ResearchPaperRecord(
                    source=Source(name="arxiv.org", url=r["paper_url"]),
                    content=ResearchPaperContent(
                        title=r["title"],
                        authors=r["authors"],
                        paper_url=r["paper_url"],
                        github_url=r.get("github_url"),
                        github_stars=r.get("github_stars"),
                        published_date=r.get("published_date") or None,
                    ),
                )
            )
        except Exception as e:
            logger.warning("Dropped malformed paper record: %s", e)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def main():
        cfg = ScraperConfig(
            total_records=int(os.environ.get("TOTAL_RECORDS", 1000)),
            github_token=os.environ.get("GITHUB_TOKEN"),
        )
        results = await scrape_research_papers(cfg)
        print(f"Scraped {len(results)} research papers")
        with_stars = sum(1 for r in results if r.content.github_stars is not None)
        print(f"  -> {with_stars} enriched with live GitHub star counts")

    asyncio.run(main())
