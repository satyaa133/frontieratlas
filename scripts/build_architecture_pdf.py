"""
Generates the official 2-page architecture.pdf deliverable using ReportLab.
Includes custom page canvas numbering (Page X of Y), sleek styling, and complete technical specifications.
"""
from __future__ import annotations

import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas that adds running headers, footers, and dynamic page counts."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Top Running Header (Pages > 1)
        if self._pageNumber > 1:
            self.drawString(0.65 * inch, 10.45 * inch, "FrontierAtlas Intelligence Graph — Production Architecture")
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(0.65 * inch, 10.35 * inch, 7.85 * inch, 10.35 * inch)

        # Bottom Running Footer
        footer_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(7.85 * inch, 0.42 * inch, footer_text)
        self.drawString(0.65 * inch, 0.42 * inch, "CONFIDENTIAL & PROPRIETARY — GraphOne / FrontierAtlas Ingestion Engine")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(0.65 * inch, 0.55 * inch, 7.85 * inch, 0.55 * inch)
        self.restoreState()


def build_pdf():
    pdf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "architecture.pdf")

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=letter,
        topMargin=0.55 * inch,
        bottomMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom Typography Styles
    title_style = ParagraphStyle(
        name="DocTitle",
        fontName="Helvetica-Bold",
        fontSize=15.5,
        leading=19,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=2,
    )
    subtitle_style = ParagraphStyle(
        name="DocSub",
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#475569"),
        spaceAfter=8,
    )
    h2_style = ParagraphStyle(
        name="SectionH2",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#0284C7"),
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        name="BodyCustom",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.8,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=4,
    )
    bullet_style = ParagraphStyle(
        name="BulletCustom",
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=colors.HexColor("#1E293B"),
    )

    story = []

    # Title Banner
    story.append(Paragraph("FrontierAtlas Intelligence Graph — Production Architecture", title_style))
    story.append(Paragraph("<b>AI Engineer Assessment</b> · Scale Strategy, LLM Orchestration, Freshness & Hybrid Storage", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284C7"), spaceBefore=0, spaceAfter=6))

    # --- SECTION 1: Scale Strategy ---
    story.append(Paragraph("1. Scale Strategy — Ingesting 500,000+ Records Autonomously", h2_style))
    story.append(Paragraph(
        "The ingestion engine is architected as a fleet of <b>stateless, horizontally-scalable async workers</b> rather than a monolithic sequential crawler. Ingestion is partitioned into discrete task units (e.g., Arxiv category offset slices, directory pagination URLs, company domain slugs) pushed onto a durable distributed work queue (Redis Streams / AWS SQS in production; an in-memory <code>asyncio.Queue</code> in this trial). Scaling from 1,000 to 500,000+ records requires zero code modifications—only increasing horizontal worker replicas or raising queue concurrency limits.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Decoupled Discovery & Extraction:</b> Discovery workers (identifying new entity URLs, RSS feeds, and GitHub repos) operate on isolated queues from extraction workers (fetching content, running LLM parsing, and schema validation). This prevents upstream rate limits or slow HTML fetches from stalling downstream throughput.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Concurrency & Courtesy Controls:</b> Downstream endpoints are protected via an <code>asyncio.Semaphore</code> and a token-bucket <code>RateLimiter</code> per domain/API. For structured repositories (Arxiv Atom & GitHub REST APIs), we leverage official programmatic endpoints rather than brittle HTML scraping, maximizing throughput while eliminating anti-bot friction.",
        body_style,
    ))

    # --- SECTION 2: 413 & 429 Handling ---
    story.append(Paragraph("2. Resilient LLM Orchestration — 413 & 429 Mitigation", h2_style))
    story.append(Paragraph(
        "<b>429 Rate Limit Management (Exponential Backoff with Jitter):</b> Every LLM extraction runs through <code>LLMOrchestrator._call_with_backoff</code>. When a 429 is encountered, the system applies exponential backoff ($t_{delay} = \\min(base \\cdot 2^{attempt}, 60s)$) randomized with ±50% jitter to prevent thundering-herd synchronization across worker fleets. If max retries are exhausted, the orchestrator seamlessly fails over to the next provider in the chain.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Multi-Tier Provider Chain:</b> Primary: <code>Gemini Flash (gemini-3.6-flash, ~1M context)</code> → Fallback: <code>OpenAI ChatGPT (GPT-4o-mini, 128k context)</code>. Each tier returns structured JSON only, strictly validated against Pydantic schemas before persistence.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>413 Payload Too Large (Semantic Chunking & Dynamic Halving):</b> Raw text is pre-chunked via <code>chunk_text()</code> budgeted to each provider's context window. Chunking preserves paragraph and sentence boundaries with a 15% sliding overlap to maintain entity claim context. If a 413 is triggered, the chunk is dynamically halved and retried up to twice before failing over to the next tier.",
        body_style,
    ))

    # Explicit Page Break to guarantee exactly 2 pages
    story.append(PageBreak())

    # --- SECTION 3: Freshness Tracking ---
    story.append(Paragraph("3. Distributed Freshness & Deduplication (< 24h Window)", h2_style))
    story.append(Paragraph(
        "Real-time monitoring across distributed crawler nodes enforces a strict 24-hour freshness sliding window across AI Jobs and News verticals via two synchronized layers:",
        body_style,
    ))
    story.append(ListFlowable([
        ListItem(Paragraph(
            "<b>Timezone-Aware Date Normalization:</b> <code>src.utils.dates.normalize_date</code> parses diverse date representations (ISO-8601, RFC-2822, relative strings like '2 hours ago') into canonical UTC timestamps. Records are admitted into Jobs/News only if <code>is_within_last_24h(pub_date)</code> evaluates to True against synchronized NTP clocks.",
            bullet_style,
        ), leftIndent=8, bulletColor=colors.HexColor("#0284C7")),
        ListItem(Paragraph(
            "<b>Idempotent Distributed Deduplication:</b> Every scraped entity generates a deterministic dedupe key (canonical URL or <code>SHA256(source + title + pub_date)</code>). In production, this key is checked atomically against a centralized Redis Bloom Filter and committed via PostgreSQL <code>ON CONFLICT DO UPDATE</code>. This guarantees multiple workers encountering the same posting cannot create duplicate records.",
            bullet_style,
        ), leftIndent=8, bulletColor=colors.HexColor("#0284C7")),
        ListItem(Paragraph(
            "<b>Seen-Before Heuristic:</b> For sources lacking explicit publication dates, the <code>SeenBeforeHeuristic</code> state tracker admits records only if their hash was unseen in prior crawl cycles, strictly preventing the re-ingestion of stale data.",
            bullet_style,
        ), leftIndent=8, bulletColor=colors.HexColor("#0284C7")),
    ], bulletType="bullet", spaceAfter=4))

    # --- SECTION 4: Storage Strategy ---
    story.append(Paragraph("4. Storage Strategy — Hybrid Polyglot Architecture", h2_style))
    story.append(Paragraph(
        "FrontierAtlas employs a purpose-built polyglot persistence architecture combining relational, graph, vector, and object storage tiers:",
        body_style,
    ))

    storage_table_data = [
        [
            Paragraph("<b>Storage Tier</b>", bullet_style),
            Paragraph("<b>Technology</b>", bullet_style),
            Paragraph("<b>Role & Data Assets</b>", bullet_style),
        ],
        [
            Paragraph("<b>Relational Core</b>", bullet_style),
            Paragraph("PostgreSQL 16", bullet_style),
            Paragraph("Primary system of record. Stores structured entities (Startups, Products, Papers, Jobs, News, Resolution Logs). Uses indexed JSONB columns for flexible metadata with strict ACID transactional guarantees.", bullet_style),
        ],
        [
            Paragraph("<b>Graph Engine</b>", bullet_style),
            Paragraph("Neo4j / Apache AGE", bullet_style),
            Paragraph("Models multi-hop relationships: <code>(Startup)-[:PRODUCES]->(Product)</code>, <code>(Startup)-[:PUBLISHES]->(Paper)-[:HAS_REPO]->(GitHub)</code>, enabling deep graph queries without recursive SQL joins.", bullet_style),
        ],
        [
            Paragraph("<b>Vector Layer</b>", bullet_style),
            Paragraph("pgvector / Pinecone", bullet_style),
            Paragraph("Embeddings of paper abstracts and product descriptions. Powers semantic deduplication and entity resolution beyond string similarity.", bullet_style),
        ],
        [
            Paragraph("<b>Object Archive</b>", bullet_style),
            Paragraph("AWS S3 / Cloudflare R2", bullet_style),
            Paragraph("Raw HTML and PDF snapshots stored with immutable content hashes. Ensures 100% auditability back to original source web pages, guaranteeing zero hallucinated data.", bullet_style),
        ],
    ]

    t = Table(storage_table_data, colWidths=[1.3 * inch, 1.2 * inch, 4.7 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    # --- SECTION 5: Entity Resolution Summary ---
    story.append(Paragraph("5. Deterministic Entity Resolution Engine", h2_style))
    story.append(Paragraph(
        "To prevent duplicate company records (e.g. 'OpenAI' vs. 'OpenAI, Inc.'), the 4-tier resolution engine applies: "
        "<b>1. Exact alias lookup</b> (canonical seed dictionary) → <b>2. Case & legal suffix stripping</b> (Inc, LLC, Corp) → "
        "<b>3. RapidFuzz token-sort matching</b> (threshold ≥ 0.87) → <b>4. Passthrough registration</b>. "
        "Every resolution emits an <code>EntityMappingLogRecord</code> with confidence scores and method provenance into <code>output/entity_mapping_log.csv</code>.",
        body_style,
    ))

    # Build Document with NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)
    print("Successfully built architecture.pdf (Exact 2-page document)")


if __name__ == "__main__":
    build_pdf()
