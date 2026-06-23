"""
HR Contacts Finder API — Lightweight FastAPI service for Render deployment.

Wraps the CompanyHRFinder logic using the `ddgs` Python library which
handles DuckDuckGo's anti-bot measures internally.

Deploy on Render as a free Web Service:
  - Build Command:  pip install -r requirements.txt
  - Start Command:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import time
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from duckduckgo_search import DDGS

# ── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    datefmt="%H:%M:%S",
)

# ── FastAPI App ──────────────────────────────────────────────────

app = FastAPI(
    title="HR Contacts Finder API",
    description="Search DuckDuckGo for HR/recruiter LinkedIn profiles at a company.",
    version="1.0.0",
)

# CORS — allow your Offizzy frontend to call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ───────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    company_name: str
    location: str
    max_results: int = 15


class HRProfile(BaseModel):
    relevance_score: int
    confidence: str
    display_title: str
    linkedin_url: str
    snippet: str
    search_query: str
    fetched_at: str


class SearchResponse(BaseModel):
    contacts: list[HRProfile]
    company: str
    location: str
    total: int


# ── Scoring ──────────────────────────────────────────────────────

HR_KEYWORDS = ["hr", "recruiter", "talent", "acquisition"]


def calculate_score(
    title: str, snippet: str, company: str, location: str
) -> dict:
    """
    40/40/20 scoring:
    - Company match:  40 pts title, 15 pts snippet-only
    - Role match:     40 pts title, 15 pts snippet-only
    - Location match: 20 pts title, 10 pts snippet-only
    """
    score = 0
    t_lower = title.lower()
    s_lower = snippet.lower()
    company_root = company.lower()
    loc = location.lower()

    # 1. Company Match (Max 40)
    if company_root in t_lower:
        score += 40
    elif company_root in s_lower:
        score += 15

    # 2. Role Match (Max 40)
    if any(k in t_lower for k in HR_KEYWORDS):
        score += 40
    elif any(k in s_lower for k in HR_KEYWORDS):
        score += 15

    # 3. Location Match (Max 20)
    if loc in t_lower:
        score += 20
    elif loc in s_lower:
        score += 10

    if score >= 80:
        confidence = "HIGH MATCH"
    elif score >= 50:
        confidence = "MEDIUM MATCH"
    else:
        confidence = "LOW MATCH (Likely Past Employee)"

    return {"score": score, "confidence": confidence}


# ── Search Logic ─────────────────────────────────────────────────


def execute_search(company: str, location: str, max_results: int = 15) -> list[dict]:
    """Run DuckDuckGo search via ddgs and return scored profiles."""
    query = f'"{company}" "{location}" (HR OR Recruiter OR "Talent Acquisition") site:linkedin.com/in/'
    logging.info(f"Searching: {query}")

    backends = ["auto", "lite", "html"]

    for backend in backends:
        logging.info(f"Trying '{backend}' backend...")
        try:
            with DDGS() as ddgs:
                raw = ddgs.text(query, max_results=max_results, backend=backend)

                if not raw:
                    logging.warning(f"No results from '{backend}'")
                    time.sleep(1)
                    continue

                profiles = []
                for item in raw:
                    title = (
                        item.get("title", "")
                        .replace(" | LinkedIn", "")
                        .replace(" - LinkedIn", "")
                        .strip()
                    )
                    snippet = item.get("body", "")
                    url = item.get("href", "")

                    grading = calculate_score(title, snippet, company, location)

                    profiles.append(
                        {
                            "relevance_score": grading["score"],
                            "confidence": grading["confidence"],
                            "display_title": title,
                            "linkedin_url": url,
                            "snippet": snippet,
                            "search_query": query,
                            "fetched_at": datetime.now().isoformat(),
                        }
                    )

                # Sort by score descending
                profiles.sort(key=lambda x: x["relevance_score"], reverse=True)
                logging.info(f"Found {len(profiles)} profiles via '{backend}'")
                return profiles

        except Exception as e:
            logging.warning(f"Backend '{backend}' failed: {e}")
            time.sleep(2)

    return []


# ── Routes ───────────────────────────────────────────────────────


@app.get("/")
def health():
    return {"status": "ok", "service": "hr-contacts-finder"}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    if not req.company_name or not req.location:
        raise HTTPException(status_code=400, detail="company_name and location required")

    results = execute_search(req.company_name, req.location, req.max_results)

    return SearchResponse(
        contacts=results,
        company=req.company_name,
        location=req.location,
        total=len(results),
    )
