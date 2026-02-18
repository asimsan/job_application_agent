# AI Job Application Agent (Werkstudent focus)

AI-assisted job search automation for **Werkstudent / Working Student** roles in Germany. The current implementation:

- reads a PDF CV
- uses **Google Gemini** to suggest suitable Werkstudent role titles
- scrapes jobs from **LinkedIn** and **StepStone** via **Playwright (Chromium)**
- filters + deduplicates results
- exports a combined JSON file to `output/scrape_results/`

> Note: The “apply to jobs on company sites” flow exists as prototype modules, but the end-to-end runner currently exits after scraping (application phase is commented out in `run_agent.py`).

## What is implemented (today)

### 1) Resume ingestion (PDF)
- Extracts text from a local PDF using `pypdf`.
- Default resume path used by the runner: `data/ann.pdf` (you provide this locally; it’s intentionally ignored by git).

### 2) LLM role suggestion (Gemini)
- Uses `GEMINI_API_KEY` and `google.generativeai`.
- Prompts Gemini to output a **comma-separated** list of **Werkstudent/Working Student role types** based on the CV text (e.g., “Werkstudent Data Science, Werkstudent Softwareentwicklung Python, …”).

### 3) Job scraping (LinkedIn + StepStone)
- **LinkedIn**: builds a search URL, scrolls results, extracts cards (title/company/location/url), then fetches per-job description HTML.
- **StepStone**: paginates search results, extracts cards, then fetches per-job description and cleans it using **BeautifulSoup**.

### 4) Filtering + deduplication + export
- Keeps only titles that contain `Werkstudent` or `Working Student`.
- Excludes senior/intern/apprenticeship keywords (e.g. senior/lead, internship/praktikum, ausbildung).
- Deduplicates globally by URL.
- Writes a combined JSON: `output/scrape_results/combined_jobs_<timestamp>.json`

### 5) Prototype modules (implemented, but not wired into the default runner)
- **Career page / ATS URL discovery** (`src/application_engine/find_career_page.py`)
  - Uses Google Custom Search (`GOOGLE_SEARCH_API_KEY`, `GOOGLE_CSE_ID`) and Gemini to select the most likely official posting URL.
- **On-site application automation** (`src/application_engine/apply_on_site.py`)
  - Playwright-based navigation and form-filling heuristics (cookie handling, apply button detection, field filling, file uploads).
  - Can generate placeholder PDFs for required uploads using **Gemini + reportlab**.

## Tech stack (as implemented)

- **Python**
- **Playwright (Chromium)** for scraping and browser automation
- **Google Gemini API** (`google.generativeai`)
- **BeautifulSoup4** for HTML cleaning (StepStone job descriptions)
- **pypdf** for CV text extraction from PDF
- **requests** for Google Custom Search API calls (career-page discovery)
- **reportlab** for generating placeholder PDFs (prototype apply-flow)
- **JSON** outputs under `output/`

### Not implemented (yet)
- **Selenium** (not used anywhere; Playwright only)
- **Indeed** scraper
- **LangChain / GPT-4 API** integration
- **Scrapy**-based scraping
- **Vector DB (Pinecone/FAISS)**, A/B testing, email tracking
- **Notifications** (email/Slack/etc.)
- **Dockerization**

## Setup

### 1) Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\\Scripts\\activate  # Windows PowerShell
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
python -m playwright install
```

### 3) Configure environment variables

Required:
- `GEMINI_API_KEY`

Optional (only needed for career-page discovery in `find_career_page.py`):
- `GOOGLE_SEARCH_API_KEY`
- `GOOGLE_CSE_ID`

## Usage

### Run the scraping pipeline (CV → suggested Werkstudent roles → LinkedIn/StepStone scrape)

1) Place your CV locally (PDF) at:
- `data/ann.pdf`

2) Run:

```bash
python run_agent.py --headless
```

Useful flags:
- `--locations Köln Düsseldorf Bonn`
- `--max-results-per-combination 5`
- `--num-titles-to-suggest 3`

### (Optional) Run resume tailoring for one scraped job

`src/personalizer/personalize.py` can generate a tailored resume text file from a scraped job description + your base CV PDF.

### (Optional) Career page discovery and apply-flow prototypes

See:
- `src/application_engine/find_career_page.py`
- `src/application_engine/apply_on_site.py`

## Output format

Combined scrape output is a list of job objects similar to:
- `title`, `company`, `location`, `url`, `source`, `description`

Saved under:
- `output/scrape_results/combined_jobs_<timestamp>.json`

## Roadmap (ideas; not all implemented)

If you’re reading older bullets in this repo history: treat them as a wishlist. The authoritative source of truth is the code in:
- `run_agent.py`
- `src/scrapers/`
- `src/personalizer/`
- `src/application_engine/`