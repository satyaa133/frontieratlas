"""
Pipeline entry point for the FrontierAtlas Intelligence Graph.

Orchestrates all 5 vertical scrapers and Phase IV entity resolution:
- Startups (>=1,000 records)
- Products (>=1,000 records)
- Research Papers (>=1,000 records, Arxiv + GitHub star counts)
- Jobs (5 sources, 24h freshness)
- News (5 sources, 24h freshness)
- Entity Mapping Log (Full raw -> canonical provenance)

Usage:
    python -m src.main --all
    python -m src.main --startups 1000 --products 1000 --papers 1000 --jobs 50 --news 50
    python -m src.main --papers 1000 --github-token $GITHUB_TOKEN
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    __package__ = "src"

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from .models.schemas import EntityMappingLogRecord
from .resolver.entity_resolver import EntityResolver
from .scrapers.jobs import JobScraperConfig, scrape_jobs
from .scrapers.news import NewsScraperConfig, scrape_news
from .scrapers.products import ProductScraperConfig, scrape_products
from .scrapers.research_papers import ScraperConfig as PaperScraperConfig, scrape_research_papers
from .scrapers.startups import StartupScraperConfig, scrape_startups
from .utils.writer import write_all_tabs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("frontieratlas.main")


def load_canonical_seed(seed_path: str = "data/canonical_seed.json") -> EntityResolver:
    seed_dict = {}
    if os.path.exists(seed_path):
        with open(seed_path, "r", encoding="utf-8") as f:
            seed_dict = json.load(f)
    return EntityResolver(canonical_seed=seed_dict)


async def run(args: argparse.Namespace) -> None:
    logger.info("Starting FrontierAtlas ingestion pipeline")
    resolver = load_canonical_seed(args.seed_path)
    mapping_logs: list[EntityMappingLogRecord] = []

    startups = []
    products = []
    papers = []
    jobs = []
    news = []

    # 1. Research Papers
    if args.papers > 0:
        logger.info("Phase I: scraping %d research papers from Arxiv + GitHub stars", args.papers)
        cfg = PaperScraperConfig(total_records=args.papers, github_token=args.github_token)
        papers = await scrape_research_papers(cfg)
        logger.info("Collected %d validated research paper records", len(papers))

    # 2. Startups
    if args.startups > 0:
        logger.info("Phase I: scraping %d startups from AI directories", args.startups)
        cfg = StartupScraperConfig(total_records=args.startups, github_token=args.github_token)
        startups = await scrape_startups(cfg, resolver=resolver, mapping_logs=mapping_logs)
        logger.info("Collected %d validated startup records", len(startups))

    # 3. Products
    if args.products > 0:
        logger.info("Phase I: scraping %d products from AI repos & spaces", args.products)
        cfg = ProductScraperConfig(total_records=args.products, github_token=args.github_token)
        products = await scrape_products(cfg, resolver=resolver, mapping_logs=mapping_logs)
        logger.info("Collected %d validated product records", len(products))

    # 4. Jobs (Freshness < 24h)
    if args.jobs > 0:
        logger.info("Phase II: monitoring 5 AI job boards for <24h fresh listings")
        cfg = JobScraperConfig(total_records=args.jobs)
        jobs = await scrape_jobs(cfg)
        logger.info("Collected %d fresh job records", len(jobs))

    # 5. News (Freshness < 24h)
    if args.news > 0:
        logger.info("Phase II: monitoring 5 AI news sources for <24h fresh articles")
        cfg = NewsScraperConfig(total_records=args.news)
        news = await scrape_news(cfg)
        logger.info("Collected %d fresh news records", len(news))

    # Write all output tabs to CSV
    write_all_tabs(
        args.output_dir,
        startups=startups,
        products=products,
        research_papers=papers,
        jobs=jobs,
        news=news,
        entity_mapping_log=mapping_logs,
    )
    logger.info("Wrote all 6 tabs to %s/", args.output_dir)


def main():
    parser = argparse.ArgumentParser(description="FrontierAtlas ingestion pipeline")
    parser.add_argument("--all", action="store_true", help="Run all scrapers to hit target counts")
    parser.add_argument("--startups", type=int, default=0, help="Number of startups to scrape")
    parser.add_argument("--products", type=int, default=0, help="Number of products to scrape")
    parser.add_argument("--papers", type=int, default=0, help="Number of research papers to scrape")
    parser.add_argument("--jobs", type=int, default=0, help="Number of fresh jobs to scrape")
    parser.add_argument("--news", type=int, default=0, help="Number of fresh news to scrape")
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token for higher rate limits")
    parser.add_argument("--seed-path", default="data/canonical_seed.json", help="Path to canonical seed list")
    parser.add_argument("--output-dir", default="output", help="Directory for output CSVs")

    args = parser.parse_args()
    if args.all:
        args.startups = 1000
        args.products = 1000
        args.papers = 1000
        args.jobs = 100
        args.news = 100

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
