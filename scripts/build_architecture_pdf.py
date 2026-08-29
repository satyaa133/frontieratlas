"""Generates architecture.pdf from the content below using reportlab."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem
)

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="H1", fontSize=16, spaceAfter=10, spaceBefore=4, textColor=colors.HexColor("#1a1a2e"), fontName="Helvetica-Bold"))
styles.add(ParagraphStyle(name="H2", fontSize=12.5, spaceAfter=6, spaceBefore=12, textColor=colors.HexColor("#16213e"), fontName="Helvetica-Bold"))
styles.add(ParagraphStyle(name="BodyText2", fontSize=9.5, leading=13.5, spaceAfter=6, fontName="Helvetica"))
styles.add(ParagraphStyle(name="Small", fontSize=8.5, leading=12, textColor=colors.HexColor("#444444")))

import os

pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "architecture.pdf")

doc = SimpleDocTemplate(
    pdf_path,
    pagesize=letter,
    topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    leftMargin=0.65 * inch, rightMargin=0.65 * inch,
)

story = []

story.append(Paragraph("FrontierAtlas Intelligence Graph — Architecture & Production Design", styles["H1"]))
story.append(Paragraph(
    "AI Engineer trial task · Scale, LLM orchestration, freshness, and storage strategy",
    styles["Small"],
))
story.append(Spacer(1, 10))

# 1. Scale strategy
story.append(Paragraph("1. Scale Strategy — Collecting 500,000+ Records Without Manual Intervention", styles["H2"]))
story.append(Paragraph(
    "The pipeline is built as a set of stateless, horizontally-scalable async workers rather than a single "
    "long-running script. Each vertical (startups, products, papers, jobs, news) is driven by a work-queue "
    "of discrete units — an Arxiv page offset, a directory pagination URL, a company slug — pushed onto a "
    "queue (Redis/SQS in production; an in-process asyncio.Queue in this trial). Any number of worker "
    "processes can pull from the same queue concurrently, so going from 1,000 to 500,000 records is purely "
    "an infrastructure change (spin up more workers / raise concurrency) and never a code change, satisfying "
    "the assignment's core scale constraint.", styles["BodyText2"],
))
story.append(Paragraph(
    "Within a worker, concurrency is bounded by an asyncio.Semaphore per downstream dependency (source site, "
    "GitHub API, LLM provider) so we saturate available throughput without tripping rate limits or overwhelming "
    "a single host. A token-bucket RateLimiter (see src/scrapers/research_papers.py) enforces per-source courtesy "
    "intervals independently of how many workers are running. Discovery (finding new URLs/IDs to crawl) and "
    "extraction (fetching + parsing a known URL) are decoupled into separate queues, so a slow discovery phase "
    "never blocks extraction throughput, and vice versa.", styles["BodyText2"],
))
story.append(Paragraph(
    "For the research-papers vertical specifically, we lean on Arxiv's and GitHub's official REST/Atom APIs "
    "rather than HTML scraping — this is both more reliable at scale and avoids anti-bot friction entirely for "
    "a source that already offers structured, bulk-friendly access.", styles["BodyText2"],
))

# 2. 413/429 handling
story.append(Paragraph("2. Handling 413s and 429s Across Thousands of Concurrent Extractions", styles["H2"]))
story.append(Paragraph(
    "<b>429 (rate limit):</b> Every provider call goes through LLMOrchestrator._call_with_backoff, which retries "
    "with exponential backoff (base 1s, doubling, capped at 60s) plus randomized jitter (±50%) to avoid thundering-herd "
    "retries across concurrent workers hitting the same provider simultaneously. After max_retries is exhausted on a "
    "provider, the orchestrator does not keep hammering it — it falls through to the next tier in the fallback chain "
    "(Gemini Flash → OpenAI ChatGPT / GPT-4o) for the remainder of that document, since a provider that's rate-limited "
    "for one chunk is almost certainly rate-limited for the rest.", styles["BodyText2"],
))
story.append(Paragraph(
    "<b>413 (payload too large):</b> Before any provider call, raw text is chunked via chunk_text() to a budget "
    "derived from that provider's max_context_tokens (Gemini ~900k, OpenAI ChatGPT 128k), so the same "
    "document is chunked differently per tier automatically. Chunking prefers paragraph, then sentence boundaries "
    "to preserve semantic density; if a single chunk still triggers a 413 from a provider (context estimate was "
    "off, or provider-side limits are tighter than advertised), the orchestrator halves that specific chunk and "
    "retries up to twice before giving up on that chunk and moving to the next fallback tier. Chunks carry a small "
    "character overlap so entity/claim continuity isn't lost at chunk boundaries.", styles["BodyText2"],
))
story.append(Paragraph(
    "At the concurrency level, thousands of simultaneous extractions are bounded by a per-provider semaphore sized "
    "to that provider's published RPS/TPM limits, so we approach — but don't exceed — the rate ceiling, which keeps "
    "the 429 rate low in the first place rather than relying purely on retries to paper over it.", styles["BodyText2"],
))

story.append(Spacer(1, 2))

# 3. Freshness tracking
story.append(Paragraph("3. Freshness Tracking Across Distributed Crawler Nodes", styles["H2"]))
story.append(Paragraph(
    "Two layers guarantee both (a) no duplicate processing and (b) accurate 24-hour freshness:", styles["BodyText2"],
))
story.append(ListFlowable([
    ListItem(Paragraph(
        "<b>Date normalization</b> (src/utils/dates.py) converts every source's publish date — ISO, relative "
        "('2 hours ago'), or missing — into a canonical UTC ISO-8601 timestamp. Items are only accepted into the "
        "News/Jobs tabs if is_within_last_24h() returns true against a shared, synchronized clock.", styles["BodyText2"]
    ), leftIndent=8, bulletColor=colors.HexColor("#1a1a2e")),
    ListItem(Paragraph(
        "<b>Distributed dedupe state</b>: each item's dedupe key (canonical URL, or source+title hash where URLs "
        "are unstable) is checked against a shared, centralized store — Redis SET or a Postgres unique index in "
        "production — BEFORE any node commits it. This is the single source of truth across all worker nodes, so "
        "two nodes racing to crawl the same job posting can't both insert it; the second write is rejected at the "
        "database layer via an idempotent UPSERT keyed on the dedupe key. The in-repo SeenBeforeHeuristic class is "
        "the local-file version of this same contract for the trial-scale run.", styles["BodyText2"]
    ), leftIndent=8, bulletColor=colors.HexColor("#1a1a2e")),
], bulletType="bullet"))
story.append(Paragraph(
    "When a source gives no reliable date at all, we fall back to the seen-before heuristic: an item is treated "
    "as fresh only if its dedupe key was absent from the last run's persisted key set — never by assuming freshness "
    "by default, which would risk re-ingesting stale content as 'new.'", styles["BodyText2"],
))

# 4. Storage strategy
story.append(Paragraph("4. Storage Strategy", styles["H2"]))
story.append(Paragraph(
    "<b>Primary store — PostgreSQL:</b> the canonical schemas (Startup, Product, ResearchPaper, Job, News, Entity "
    "Mapping Log) are inherently relational — products belong to startups, papers link to repos, jobs link to "
    "companies — and need strong consistency guarantees (unique constraints for dedupe, transactional upserts). "
    "Postgres' native JSONB columns also let us store the full validated record payload alongside indexed relational "
    "columns (entity_id, source_url, collected_at), giving schema flexibility without giving up query performance "
    "or ACID guarantees, which a pure document store wouldn't.", styles["BodyText2"],
))
story.append(Paragraph(
    "<b>Graph layer — Neo4j (or Postgres + Apache AGE for a lighter footprint):</b> relationships like "
    "startup → product → paper → author → github_repo are naturally multi-hop graph queries ('show me all papers "
    "with code from startups founded by ex-DeepMind researchers'). Modeling this in a graph store avoids expensive "
    "recursive joins in Postgres as the relationship depth grows, and is where the 'Intelligence Graph' product "
    "concept — not just a records dump — actually gets realized.", styles["BodyText2"],
))
story.append(Paragraph(
    "<b>Vector layer — pgvector or a dedicated vector DB (Pinecone/Weaviate):</b> embeddings of paper abstracts, "
    "product descriptions, and news content power semantic search and entity-resolution fuzzy matching beyond what "
    "rapidfuzz string similarity alone can catch (e.g. two differently-worded descriptions of the same product). "
    "Starting with pgvector keeps the primary store's transactional guarantees for embeddings tied 1:1 to their "
    "source record; a dedicated vector DB is a later optimization once query volume justifies the operational cost.", styles["BodyText2"],
))
story.append(Paragraph(
    "<b>Object storage — S3-compatible bucket:</b> raw HTML/PDF snapshots are archived alongside the structured "
    "record they were extracted from, so every record is auditable back to its original source content, not just "
    "its source URL — directly supporting the assignment's 'no hallucinated data, every record traces to a valid "
    "source' requirement.", styles["BodyText2"],
))

doc.build(story)
print("Wrote architecture.pdf")
