# New Taipei Public Kindergarten Google Review Intelligence Pipeline

A production-grade data pipeline for collecting, validating, and analyzing
Google Maps reviews of **New Taipei City (新北市) public kindergartens**.

## Project Purpose

Build a clean, auditable dataset of Google Maps reviews for all New Taipei City
public kindergartens, suitable for NLP analysis, sentiment scoring, and risk detection.

**Core principle: government data determines public status. Google Maps is only used for Place matching and review collection.**

---

## Architecture

```
Government Open Data
         |
         v
Official Kindergarten Master Dataset (kindergartens table)
         |
         v
Validation (analysis/public_kindergarten_validator.py)
    -- city == New Taipei City?
    -- public_type IN (public, public_attached)?
         |
         v
Google Place Matching (collectors/google_places.py)
    -- name similarity
    -- address similarity
    -- geo distance
    -- match score >= 0.85 → AUTO_ACCEPTED
         |
         v
Google Place Metadata (google_places table)
         |
         v
Google Review Sample (reviews table)
    -- SHA-256 deduplication
    -- partial dataset (API limitation)
         |
         v
NLP / Risk Analysis
```

---

## Installation

```bash
# Python 3.11+ required
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env
# Edit .env: add GOOGLE_MAPS_API_KEY
```

---

## Google Places API Setup

1. Go to https://console.cloud.google.com/apis/credentials
2. Create a project
3. Enable **Places API (New)**
4. Create an API Key
5. Add key to `.env`:
   ```
   GOOGLE_MAPS_API_KEY=your_key_here
   ```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GOOGLE_MAPS_API_KEY` | For steps 6-7 | - | Google Places API (New) key |
| `DATABASE_URL` | No | SQLite | Override DB (e.g. PostgreSQL) |
| `GOOGLE_REQUEST_DELAY` | No | 0.5 | Seconds between API calls |
| `GOOGLE_MAX_RETRIES` | No | 5 | Max retry attempts |
| `GOOGLE_TIMEOUT` | No | 30 | HTTP timeout (seconds) |
| `LOG_LEVEL` | No | INFO | Logging level |

---

## Database Schema

### kindergartens
Primary master table. Source of truth for public status.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| official_name | TEXT | Full official name from government data |
| school_name | TEXT | Parent school name (for attached KGs) |
| public_type | ENUM | public / public_attached / private / ... |
| is_public | BOOLEAN | **Determined by govt data only. Never from Google.** |
| district | TEXT | Administrative district (行政區) |
| address | TEXT | Official address |
| google_match_status | ENUM | unmatched / auto_accepted / needs_review / rejected |
| google_match_score | FLOAT | 0.0-1.0 matching confidence |
| place_fetch_status | ENUM | pending / running / success / failed |
| review_fetch_status | ENUM | pending / running / success / failed |

### google_places
Google Place metadata. One row per matched kindergarten.

### reviews
Google reviews collected via Places API (New).

**Important**: `google_review_count` (from Place) ≠ `collected_review_count` (our DB).
The Places API returns a limited subset of reviews. See "Known Limitations" below.

### rejected_kindergartens
Audit log of all records excluded from the pipeline, with reason codes.

---

## CLI Commands

```bash
# STEP 3: Import official data (seed data or CSV)
python main.py import-kindergartens
python main.py import-kindergartens --csv-file data/public_kindergartens.csv
python main.py import-kindergartens --try-moe-api

# STEP 4: Validate (filter to NTC public only)
python main.py validate-kindergartens

# STEP 5: Check pipeline status
python main.py status

# STEP 6: Google Place matching (requires API key)
python main.py match-places
python main.py match-places --limit 20

# STEP 7: Fetch metadata and reviews (requires API key)
python main.py fetch-reviews
python main.py fetch-reviews --limit 20
python main.py fetch-reviews --district Banqiao

# Retry failed operations
python main.py retry-failed

# Export data
python main.py export
```

---

## Public Kindergarten Filtering Logic

Public status is determined **exclusively** from government source data fields:

| Source field value | Normalized type | is_public |
|-------------------|-----------------|-----------|
| 公立, 市立, 區立, 國立 | PUBLIC | **TRUE** |
| 公立附設, 國民小學附設 | PUBLIC_ATTACHED | **TRUE** |
| 私立 | PRIVATE | FALSE |
| 準公共 | QUASI_PUBLIC | FALSE |
| 非營利 | NONPROFIT | FALSE |
| 公設民營 | PUBLIC_PRIVATE_PARTNERSHIP | FALSE |
| (empty or unknown) | UNKNOWN | FALSE (needs_review) |

**Google Maps is never consulted for public/private determination.**

---

## Google Place Matching Logic

```
score = name_similarity * 0.50
      + address_similarity * 0.30
      + geo_similarity * 0.20
```

Using `rapidfuzz` for string similarity.

| Score | Status |
|-------|--------|
| >= 0.85 | AUTO_ACCEPTED |
| 0.70 - 0.85 | NEEDS_REVIEW |
| < 0.70 | REJECTED |

Manual overrides: edit `config/place_overrides.csv`.

---

## Review Collection Limitation

> **Google Places API (New) currently returns only a limited subset of reviews for a Place.**
>
> Therefore the collected review dataset must not be interpreted as the complete historical Google Maps review corpus.

The database explicitly tracks:
- `google_review_count` — total reviews shown on Google Maps (from Place metadata)
- `collected_review_count` — actual rows we collected via API

These two values will differ significantly for popular places.

---

## Data Export

```
exports/
├── public_kindergartens.csv
├── google_places.csv
├── reviews.csv
├── review_stats.csv
├── rejected_kindergartens.csv
├── manual_review_places.csv
└── reviews.jsonl    # one review per line
```

---

## Raw Data Storage

All API responses are stored as-is for debugging:

```
data/raw/google_places/YYYY-MM-DD/place_<place_id>.json
```

---

## Seed Data

The built-in seed covers ~116 New Taipei City public kindergartens across
all 29 administrative districts, curated from the official government source:
https://www.ece.moe.edu.tw/ch/query-preschool/

To use a fresh government CSV:
```bash
python main.py import-kindergartens --csv-file /path/to/moe_kindergartens.csv
```

---

## Known Limitations

1. **Review coverage**: Places API (New) returns ~5 reviews per Place. Not the full corpus.
2. **Seed data staleness**: The built-in seed may miss newly established kindergartens. Import fresh CSV for current data.
3. **Place matching edge cases**: Some attached kindergartens (國小附設) may match the primary school's Place, not a separate Place entry. Use `config/place_overrides.csv` to correct.
4. **Rate limits**: Google Places API has per-day quotas. Use `--limit` to batch operations.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

44 tests covering:
- Public type normalization
- Private/quasi-public/nonprofit rejection
- Not-New-Taipei-City rejection
- Missing address rejection
- Address district extraction
- Review SHA-256 deduplication
- Repository upsert and resume behavior
- Validator batch processing and CSV output
- Seed data integrity
- Full import → validate pipeline
