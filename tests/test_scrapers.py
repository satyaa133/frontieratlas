"""
Unit tests for scrapers schema adherence, parsing logic, and pricing inference.
Network-free tests verifying data transformations and edge case handling.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models.schemas import (  # noqa: E402
    EntityMappingLogRecord,
    JobContent,
    JobRecord,
    NewsContent,
    NewsRecord,
    PricingModel,
    ProductContent,
    ProductRecord,
    Source,
    StartupContent,
    StartupRecord,
)
from src.scrapers.news import clean_text  # noqa: E402
from src.scrapers.products import infer_pricing_model  # noqa: E402


def test_pricing_model_inference():
    assert infer_pricing_model("Open source MIT licensed AI model") == PricingModel.FREE
    assert infer_pricing_model("SOC2 compliant enterprise tier custom quote") == PricingModel.ENTERPRISE
    assert infer_pricing_model("Cloud tier subscription and freemium plan") == PricingModel.FREEMIUM
    assert infer_pricing_model("Just a tool") == PricingModel.FREE


def test_news_clean_text():
    raw_html = "<p>OpenAI releases <strong>new model</strong> with <a href='https://example.com'>link</a>.</p>"
    cleaned = clean_text(raw_html)
    assert cleaned == "OpenAI releases new model with link."


def test_startup_record_schema():
    rec = StartupRecord(
        source=Source(name="github.com", url="https://github.com/openai"),
        content=StartupContent(entityName="OpenAI"),
    )
    dumped = rec.model_dump()
    assert dumped["recordType"] == "STARTUP"
    assert dumped["content"]["entityName"] == "OpenAI"
    assert dumped["schemaVersion"] == "1.0"


def test_product_record_schema():
    rec = ProductRecord(
        source=Source(name="Hugging Face", url="https://huggingface.co/spaces/test/demo"),
        content=ProductContent(startupName="OpenAI", pricingModel=PricingModel.FREEMIUM),
    )
    dumped = rec.model_dump()
    assert dumped["recordType"] == "PRODUCT"
    assert dumped["content"]["pricingModel"] == "FREEMIUM"


def test_job_record_schema():
    rec = JobRecord(
        source=Source(name="RemoteOK", url="https://remoteok.com/job/123"),
        content=JobContent(company="Anthropic", title="Research Scientist", is_remote=True),
    )
    dumped = rec.model_dump()
    assert dumped["recordType"] == "JOB"
    assert dumped["content"]["company"] == "Anthropic"


def test_news_record_schema():
    rec = NewsRecord(
        source=Source(name="TechCrunch", url="https://techcrunch.com/article"),
        content=NewsContent(title="Anthropic launches Claude 3.7", url="https://techcrunch.com/article"),
    )
    dumped = rec.model_dump()
    assert dumped["recordType"] == "NEWS"
    assert dumped["content"]["title"] == "Anthropic launches Claude 3.7"


def test_entity_mapping_log_record():
    log = EntityMappingLogRecord(
        raw_name="OpenAI, Inc.",
        canonical_name="OpenAI",
        entity_type="STARTUP",
        method="exact",
        confidence=1.0,
        source_url="https://github.com/openai",
    )
    assert log.canonical_name == "OpenAI"
    assert log.method == "exact"
