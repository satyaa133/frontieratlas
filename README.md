# FrontierAtlas Intelligence Graph — Ingestion Pipeline

An enterprise-grade, async, fault-tolerant ingestion pipeline for the **GraphOne / FrontierAtlas** AI & venture intelligence graph. The pipeline continuously monitors, extracts, resolves, and normalizes AI startups, products, research papers, job postings, and news across thousands of web sources.

Full architecture documentation covering 500k+ scale, distributed deduplication, rate limit resilience, and hybrid database design is available in [`architecture.pdf`](./architecture.pdf).

---

## Key Highlights

- **Zero Hallucinated Data**: 100% of records originate from live, verified web endpoints and trace directly to valid source URLs.
- **Strict Schema Adherence**: All data structures validate against Pydantic v2 schemas before insertion; invalid entries are rejected and logged.
- **Horizontal Scalability**: Stateless async worker architecture capable of scaling to 500,000+ records purely through infrastructure concurrency.
- **Fault-Tolerant LLM Orchestrator**: Multi-tier fallback (`Gemini Flash (gemini-3.6-flash)` → `OpenAI ChatGPT (GPT-4o-mini)`) with context-aware semantic chunking (preventing 413s) and exponential backoff with randomized jitter (handling 429s).
- **Deterministic Entity Resolution**: 4-tier resolution engine (Exact alias → Normalized name → `rapidfuzz` string similarity at 87% → Passthrough) with 100% provenance audit logging.
- **Strict 24-Hour Freshness**: UTC normalization and sliding-window verification for news and job boards, backed by `SeenBeforeHeuristic` state deduplication.

---

## 6-Phase Architecture

```
                     ┌────────────────────────┐
                     │ Work Queue / Ingestion │
                     └───────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
   ┌───────────┐          ┌─────────────┐         ┌─────────────┐
   │ Research  │          │  Startups   │         │    Jobs     │
   │  Papers   │          │ & Products  │         │   & News    │
   │ (Arxiv +  │          │ (HF Spaces, │         │ (5 Boards,  │
   │  GitHub)  │          │  Orgs, APIs)│         │ 24h Fresh)  │
   └─────┬─────┘          └──────┬──────┘         └──────┬──────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 ▼
         ┌────────────────────────────────────────────────┐
         │     LLM Orchestrator (Phase III)               │
         │ Gemini 3.6 Flash → OpenAI ChatGPT (GPT-4o-mini)│
         │ Semantic Chunking (No 413) · Backoff+Jitter    │
         └───────────────────────┬────────────────────────┘
                                 ▼
         ┌────────────────────────────────────────────────┐
         │     Entity Resolution Engine (Phase IV)        │
         │ Exact → Normalized → RapidFuzz (87% threshold) │
         │ Maps aliases to 50 canonical AI startups       │
         └───────────────────────┬────────────────────────┘
                                 ▼
         ┌────────────────────────────────────────────────┐
         │   Pydantic Schema Validation & Audit Logging   │
         │ Emits EntityMappingLogRecord for provenance    │
         └───────────────────────┬────────────────────────┘
                                 ▼
         ┌────────────────────────────────────────────────┐
         │   CSV Writer → Google Sheets (6 Tabs)          │
         │ + Production: Postgres / Neo4j / pgvector / S3 │
         └────────────────────────────────────────────────┘
```

| Phase | Component | Key Implementation Details |
|---|---|---|
| **I** | **Bulk Extraction** | Async scrapers for Startups (`≥1,000`), Products (`≥1,000`), and Research Papers (`≥1,000`). Correlates Arxiv papers with live GitHub repository star counts. |
| **II** | **Freshness Engine** | Monitors 5 AI job boards (Arbeitnow, RemoteOK, Remotive, WeWorkRemotely, Hacker News Jobs) and 5 AI news feeds (TechCrunch, VentureBeat, HN AI, ScienceDaily, AI News). Strictly enforces `< 24h` publishing dates. |
| **III** | **LLM Extraction Engine** | Primary: `Gemini Flash (gemini-3.6-flash)`, Fallback: `OpenAI ChatGPT (GPT-4o-mini)` with context-budgeted semantic chunking and jittered exponential backoff. |
| **IV** | **Entity Resolution** | 4-tier resolution engine deduplicating entities against 50 canonical AI startups, logging all decisions to `entity_mapping_log.csv`. |
| **V** | **Anti-Bot & JS Crawling** | Async Playwright crawler stripping `navigator.webdriver` flags with realistic headers and concurrency limits. |
| **VI** | **Production Architecture** | 2-page ReportLab PDF detailing 500k scale, 413/429 handling, distributed deduplication, and PostgreSQL + Neo4j + pgvector + S3 architecture. |

---

## Setup & Installation

### 1. Prerequisites
- Python 3.10+
- macOS, Linux, or Windows (WSL recommended)

### 2. Environment Setup
```bash
# Clone repository and navigate to root
cd frontieratlas

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser binary
playwright install chromium
```

### 3. Environment Variables (Optional)
Set API keys if calling live LLM providers or raising GitHub API rate limits:
```bash
export GEMINI_API_KEY="your_gemini_api_key_here"
export OPENAI_API_KEY="your_openai_api_key_here"
export GITHUB_TOKEN="your_github_token_here"     # Raises GitHub rate limits from 60/hr to 5,000/hr
```

---

## Running the Ingestion Pipeline

### Full Pipeline Run
To run all vertical scrapers and generate the full dataset (≥1,000 startups, ≥1,000 products, ≥1,000 research papers, fresh jobs, and fresh news):
```bash
python -m src.main --all
```

### Targeted Vertical Runs
You can also run specific verticals or configure custom record limits:
```bash
# Run specific verticals
python -m src.main --papers 1000 --github-token $GITHUB_TOKEN
python -m src.main --startups 1000 --products 1000
python -m src.main --jobs 100 --news 50

# Specify custom output directory or seed list
python -m src.main --all --output-dir custom_output/ --seed-path data/canonical_seed.json
```

---

## Output Datasets

All outputs are saved as CSV files in `output/` with dot-notation flattened schemas ready for direct Google Sheets import:

1. **`output/startups.csv`** — `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.entityName`, `content.data.employeeCount`, `collectedAt`.
2. **`output/products.csv`** — `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.startupName`, `content.pricingModel`, `collectedAt`.
3. **`output/research_papers.csv`** — `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.title`, `content.authors`, `content.paper_url`, `content.github_url`, `content.github_stars`, `content.published_date`, `collectedAt`.
4. **`output/jobs.csv`** — `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.company`, `content.date`, `content.is_remote`, `content.role_family`, `content.title`, `content.url`, `collectedAt`.
5. **`output/news.csv`** — `schemaVersion`, `recordType`, `source.name`, `source.url`, `content.title`, `content.url`, `content.published_date`, `content.full_text`, `content.summary`, `collectedAt`.
6. **`output/entity_mapping_log.csv`** — `raw_name`, `canonical_name`, `entity_type`, `method`, `confidence`, `source_url`, `resolvedAt`.

---

## Interactive Visual Dashboard

Open [`frontend/index.html`](./frontend/index.html) directly in any modern browser (or serve via `python3 -m http.server 8000 --directory frontend`):

Features:
- **Minimalist Obsidian Dark Aesthetic**: Glassmorphism panels, Plus Jakarta Sans typography, and JetBrains Mono metadata formatting.
- **100% Dynamic CSV-Driven**: Upload or drag-and-drop any `output/*.csv` file directly onto the dashboard.
- **Smooth 50-item Pagination & Instant Filtering**: Fast, stutter-free viewing of 1,000+ records.
- **Dynamic Entity Graph**: Pulsating live connection map between AI companies, models, research papers, and jobs.

---

## Running Tests

All 22 unit and integration tests execute completely offline without network dependencies in < 0.5s:

```bash
pytest
```

Test Coverage:
- `tests/test_dates.py` — Relative date parsing ("2 hours ago", "yesterday"), ISO normalization, and 24h freshness boundaries.
- `tests/test_entity_resolver.py` — Alias mapping, case-folding, legal suffix stripping, and RapidFuzz confidence scores.
- `tests/test_chunking.py` — Context window token budgeting, sentence/paragraph boundary preserving, and oversized text splitting.
- `tests/test_llm_orchestrator.py` — Provider fallback failover, 413 halving retry, and 429 rate limit backoff.
- `tests/test_scrapers.py` — Scraper schema validation, HTML text cleaning, and pricing model inference.

---

## Repository Layout

```
frontieratlas/
├── README.md                          # Project documentation and execution guide
├── architecture.pdf                   # Phase VI production architecture design document
├── requirements.txt                   # Python dependencies
├── .gitignore                         # Git ignore configuration
├── data/
│   └── canonical_seed.json            # 50 known AI startups & aliases for Entity Resolution
├── frontend/
│   └── index.html                     # Minimal visual intelligence graph dashboard
├── output/                            # Ingested production CSV datasets (all 6 tabs)
│   ├── startups.csv
│   ├── products.csv
│   ├── research_papers.csv
│   ├── jobs.csv
│   ├── news.csv
│   └── entity_mapping_log.csv
├── src/
│   ├── main.py                        # CLI entry point orchestrating ingestion pipeline
│   ├── models/
│   │   └── schemas.py                 # Canonical Pydantic v2 schemas
│   ├── resolver/
│   │   └── entity_resolver.py         # Phase IV 4-tier Entity Resolution engine
│   ├── llm/
│   │   ├── orchestrator.py            # Phase III fallback chain, chunking & backoff
│   │   └── providers.py               # Gemini Flash & OpenAI ChatGPT client adapters
│   ├── scrapers/
│   │   ├── research_papers.py         # Phase I Arxiv + GitHub stars scraper
│   │   ├── startups.py                # Phase I AI Startups scraper
│   │   ├── products.py                # Phase I AI Products scraper
│   │   ├── jobs.py                    # Phase II 24h Freshness Jobs scraper
│   │   ├── news.py                    # Phase II 24h Freshness News scraper
│   │   └── playwright_helper.py       # Phase V Async Playwright anti-bot crawler
│   └── utils/
│       ├── dates.py                   # Date normalization, 24h filter & SeenBeforeHeuristic
│       └── writer.py                  # Dot-notation CSV flattening and export
└── tests/
    ├── test_dates.py
    ├── test_entity_resolver.py
    ├── test_chunking.py
    ├── test_llm_orchestrator.py
    └── test_scrapers.py
```
