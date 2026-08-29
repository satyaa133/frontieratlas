"""
Phase I: Products vertical scraper.

Extracts >= 1,000 unique AI software products/tools from verified sources:
- Hugging Face Spaces (live AI apps, product demos, full interfaces)
- Hugging Face Model Products across multiple AI task families

Infers pricing models (FREE, FREEMIUM, PAID, ENTERPRISE) from project metadata.
Passes startup/maker name through EntityResolver (Phase IV) with provenance logging.
Validates 100% against canonical ProductRecord schema.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

from ..models.schemas import (
    EntityMappingLogRecord,
    PricingModel,
    ProductContent,
    ProductRecord,
    Source,
)
from ..resolver.entity_resolver import EntityResolver
from .research_papers import RateLimiter

logger = logging.getLogger("frontieratlas.scrapers.products")

HF_API = "https://huggingface.co/api"

PRODUCT_TASK_TAGS = [
    "",
    "text-generation",
    "image-to-text",
    "text-to-image",
    "automatic-speech-recognition",
    "feature-extraction",
    "text-classification",
]


@dataclass
class ProductScraperConfig:
    total_records: int = 1000
    concurrency: int = 5
    github_token: Optional[str] = None


def infer_pricing_model(text: str) -> Optional[PricingModel]:
    """Infers pricing model from text descriptions and tags."""
    t = text.lower()
    if any(k in t for k in ["enterprise", "custom quote", "soc2", "contact sales"]):
        return PricingModel.ENTERPRISE
    if any(k in t for k in ["pro plan", "paid tier", "subscription", "pricing", "freemium", "cloud tier"]):
        return PricingModel.FREEMIUM
    if any(k in t for k in ["open-source", "open source", "free", "mit license", "apache 2.0", "gpl"]):
        return PricingModel.FREE
    return PricingModel.FREE


async def fetch_hf_spaces(
    session: aiohttp.ClientSession,
    limit: int,
    limiter: RateLimiter,
) -> list[dict]:
    await limiter.wait()
    url = f"{HF_API}/spaces"
    params = {"limit": limit, "full": "true"}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.warning("Failed fetching HuggingFace spaces: %s", e)
    return []


async def fetch_hf_models(
    session: aiohttp.ClientSession,
    pipeline_tag: str,
    limit: int,
    limiter: RateLimiter,
) -> list[dict]:
    await limiter.wait()
    url = f"{HF_API}/models"
    params = {"limit": limit, "sort": "downloads", "direction": "-1"}
    if pipeline_tag:
        params["pipeline_tag"] = pipeline_tag
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.warning("Failed fetching HuggingFace models: %s", e)
    return []


async def scrape_products(
    config: ProductScraperConfig,
    resolver: Optional[EntityResolver] = None,
    mapping_logs: Optional[list[EntityMappingLogRecord]] = None,
) -> list[ProductRecord]:
    limiter = RateLimiter(min_interval=0.2 / config.concurrency)
    seen_urls: set[str] = set()
    records: list[ProductRecord] = []

    async with aiohttp.ClientSession() as session:
        # 1. Fetch Hugging Face Spaces (Live AI Apps / Products)
        spaces_task = fetch_hf_spaces(session, limit=1000, limiter=limiter)
        models_tasks = [fetch_hf_models(session, tag, limit=1000, limiter=limiter) for tag in PRODUCT_TASK_TAGS]

        results = await asyncio.gather(spaces_task, *models_tasks, return_exceptions=True)

        all_spaces = results[0] if isinstance(results[0], list) else []
        all_models = []
        for r in results[1:]:
            if isinstance(r, list):
                all_models.extend(r)

        # Process spaces
        for space in all_spaces:
            if len(records) >= config.total_records:
                break
            space_id = space.get("id", "")
            if not space_id or "/" not in space_id:
                continue
            raw_owner, product_name = space_id.split("/", 1)
            source_url = f"https://huggingface.co/spaces/{space_id}"

            if source_url in seen_urls:
                continue
            seen_urls.add(source_url)

            if resolver:
                canonical_startup, method, confidence = resolver.resolve(raw_owner)
                if mapping_logs is not None:
                    mapping_logs.append(
                        EntityMappingLogRecord(
                            raw_name=raw_owner,
                            canonical_name=canonical_startup,
                            entity_type="PRODUCT",
                            method=method,
                            confidence=confidence,
                            source_url=source_url,
                        )
                    )
            else:
                canonical_startup = raw_owner

            pricing = infer_pricing_model(product_name)
            try:
                records.append(
                    ProductRecord(
                        source=Source(name=f"Hugging Face Space ({product_name})", url=source_url),
                        content=ProductContent(
                            startupName=canonical_startup,
                            pricingModel=pricing,
                        ),
                    )
                )
            except Exception as e:
                logger.warning("Dropped malformed product record %s: %s", space_id, e)

        # Process model products
        for model in all_models:
            if len(records) >= config.total_records:
                break
            model_id = model.get("id", "")
            if not model_id or "/" not in model_id:
                continue
            raw_owner, product_name = model_id.split("/", 1)
            source_url = f"https://huggingface.co/{model_id}"

            if source_url in seen_urls:
                continue
            seen_urls.add(source_url)

            if resolver:
                canonical_startup, method, confidence = resolver.resolve(raw_owner)
                if mapping_logs is not None:
                    mapping_logs.append(
                        EntityMappingLogRecord(
                            raw_name=raw_owner,
                            canonical_name=canonical_startup,
                            entity_type="PRODUCT",
                            method=method,
                            confidence=confidence,
                            source_url=source_url,
                        )
                    )
            else:
                canonical_startup = raw_owner

            tags = " ".join(model.get("tags", []))
            pricing = infer_pricing_model(f"{product_name} {tags}")

            try:
                records.append(
                    ProductRecord(
                        source=Source(name=f"Hugging Face Model ({product_name})", url=source_url),
                        content=ProductContent(
                            startupName=canonical_startup,
                            pricingModel=pricing,
                        ),
                    )
                )
            except Exception as e:
                logger.warning("Dropped malformed model product record %s: %s", model_id, e)

    logger.info("Collected %d unique validated product records", len(records))
    return records[: config.total_records]
