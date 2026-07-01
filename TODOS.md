# LinkedIn Networking CLI - TODOs

This file contains the list of features, improvements, and pending details for future versions.

---

## 🚀 Upcoming Features

### Advanced Search Filters

#### 1. Current Company Filter
**Priority:** High
**Description:** Allow searching for people who work at specific companies.

**Pending implementation:**
- Add `current_company_ids` field to the Campaign model
- Build a UI for company search
- Implementation options:
  - **A) Manual input:** User searches on LinkedIn and copies the company ID from the URL
  - **B) Integrated search:** Use LinkedIn's typeahead API to search companies
  - **C) Configuration file:** Keep a pre-configured list of common companies

**URL format:** `currentCompany=["1441","1586","1035"]` (multiple IDs)

**Usage example:**
- Search for employees of Google, Meta, Apple
- IDs: Google="1441", Meta="1586", Apple="162479"

---

#### 2. School/University Filter
**Priority:** Medium
**Description:** Filter by alumni of specific universities.

**Pending implementation:**
- Add `school_ids` field to the Campaign model
- Build a UI for university selection
- Format: `schoolFilter=["166622","1792","12297"]`

**Use cases:**
- Recruiting alumni from Stanford, MIT, Harvard
- Networking with people from the same university

---

#### 3. Past Company Filter
**Priority:** Medium
**Description:** Search for people who previously worked at certain companies.

**Pending implementation:**
- Add `past_company_ids` field to the Campaign model
- Similar to current_company but for previous jobs
- Format: `pastCompany=["1035","1441","1586"]`

**Use cases:**
- Ex-employees of FAANG companies
- Alumni of specific startups

---

#### 4. Profile Language Filter
**Priority:** Low
**Description:** Filter profiles by language.

**Pending implementation:**
- Add `profile_language` field to the Campaign model
- Simple dropdown: English, Spanish, French, German, etc.
- Format: `profileLanguage=["en"]`

**Mapping:**
```python
LANGUAGE_OPTIONS = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Chinese": "zh",
    "Japanese": "ja",
}
```

---

#### 5. Service Category Filter
**Priority:** Low
**Description:** Filter by service categories (for freelancers/consultants).

**Pending implementation:**
- Add `service_category_ids` field to the Campaign model
- Format: `serviceCategory=["220"]`
- Research the available category IDs

---

#### 6. Follower Of Filter
**Priority:** Low
**Description:** Search for followers of a specific profile.

**Pending implementation:**
- Add `follower_of_urn` field to the Campaign model
- Format: `followerOf=["ACoAACAX-bAB9B1BSm-SWFPe6HY5wzjtcMO06gE"]`
- Requires the URN of the target profile

---

## 🔍 Research Needed

### LinkedIn Location IDs (geoUrn)

**Status:** ✅ IMPLEMENTED - Hybrid Solution

**Current implementation:**
- ✅ Curated list with ~75+ major cities worldwide
- ✅ "Other" option to enter custom codes manually
- ✅ `LinkedInAutomation.search_location()` function for dynamic search (backend)

**Verified codes:**
- San Francisco Bay Area: `90000084` ✅
- Greater Boston Area: `105646813` ✅
- United States: `103644278` ✅

**Codes in the list (pending manual verification):**
- ~75 major cities across the US, Canada, Europe, Asia-Pacific, Latin America
- See the full list in `src/automation/linkedin_mappings.py`

**How to use:**
1. **Option A - Curated list**: Select from the expanded list in the UI
2. **Option B - Manual**: Select "Other" and enter a custom geoUrn
3. **Option C - Dynamic (future)**: Real-time search via the Voyager API

---

### 🔮 Dynamic Location Search (Future Phase)

**Status:** Backend implemented, UI pending
**Priority:** Medium

**Backend already available:**
```python
# In src/automation/linkedin.py
async def search_location(query: str) -> List[Dict[str, str]]:
    """Dynamic search using the Voyager API"""
    # Returns: [{"name": "San Francisco Bay Area", "geoUrn": "90000084"}]
```

**Pending implementation:**
- Make the campaign-creation UI async
- Add real-time autocomplete search
- Require prior authentication before creating a campaign

**API endpoint used:**
```
GET /voyager/api/typeahead/hitsV2?
    keywords={query}&
    origin=OTHER&
    q=type&
    queryContext=List(geoVersion->3,bingGeoSubTypeFilters->MARKET_AREA|COUNTRY_REGION|ADMIN_DIVISION_1|CITY)&
    type=GEO
```

**Advantages:**
- Access to ALL LinkedIn locations (not just the curated list)
- Always up-to-date data
- Typo-tolerant search

**Disadvantages:**
- Requires an authenticated session
- More complex UI
- Network latency

**Future implementation decision:** Add as an optional advanced mode

---

### LinkedIn Industry IDs

**Status:** Initial research
**Partial codes:**
- Computer Software: `1594` ✅
- Internet: `6` ✅
- Technology: `4` ❓

**Pending:**
- Obtain the full list of industry IDs
- Verify each code against real searches
- Resources: LinkedIn API docs, reverse engineering of searches

---

### LinkedIn Company/School IDs

**Status:** Not implemented

**How to obtain IDs:**
1. **Manual:**
   - Search for the company/university on LinkedIn
   - Look at the profile URL: `/company/1441/` → ID is `1441`

2. **Programmatic:**
   - LinkedIn Typeahead API: `/voyager/api/typeahead/...`
   - Requires authentication
   - Implement a helper function

**Common companies to pre-configure:**
```
Google: 1441
Meta: 1586
Apple: 162479
Microsoft: 1035
Amazon: 1586
Netflix: 165158
Tesla: 15564
```

---

## 🎨 UX/UI Improvements

### Campaign Creation Flow

**Option 1: Linear flow (current)**
- Simple, step by step
- ✅ Easy to understand
- ❌ Can be long with advanced filters

**Option 2: Sectioned flow**
```
1. Basic Info (name, description)
2. Core Filters (keywords, location, industry, network)
3. Advanced Filters? (Yes/No)
   └─ If Yes → show advanced filters
4. Settings (daily limit, message)
5. Review & Create
```

**Option 3: Interactive form**
- A single form with all options
- Advanced filters collapsed
- Faster but more complex

**Pending decision:** User feedback

---

### Campaign Editing

**Status:** ✅ IMPLEMENTED (basic CRUD)
**Priority:** High

Implemented in "Manage Campaigns" (wired to SQLite via `DatabaseManager`):
- ✅ Edit name, description, daily limit, and message template
- ✅ Pause/resume campaigns (toggle active/inactive)
- ✅ Delete a campaign and its associated contacts

**Pending (future):**
- Edit targeting filters (keywords/location/industry) without recreating the campaign

---

### Campaign Templates

**Status:** Idea
**Priority:** Medium

Save common configurations as templates:
- "FAANG Recruiters"
- "Local Startup Founders"
- "Remote Developers"
- "Sales Professionals in SF"

---

## 🐛 Known Bugs

### 1. Location Filter Not Working
**Status:** ✅ RESOLVED in Phase 1
**Problem:** Sent text instead of the numeric geoUrn
**Solution:** Implemented location mapping

---

### 2. Incorrect Search URL
**Status:** ✅ RESOLVED in Phase 1
**Problem:** Used `/search/people/` instead of `/search/results/people/`
**Solution:** Fixed in linkedin.py

---

## 📊 Analytics & Reporting

### Improved Dashboard
- Acceptance-rate charts per campaign
- Timeline of sent connections
- Heatmap of best days/hours
- A/B testing of message templates

### Export Features
- Export contacts to CSV
- CRM integration (HubSpot, Salesforce)
- Webhook notifications

---

## 🔒 Security & Rate Limiting

### LinkedIn Detection Avoidance
- Random delays between actions (✅ already implemented)
- Respect daily limits (✅ already implemented)
- Human-like browsing patterns
- Session management (✅ already implemented)

### Pending improvements:
- Detect CAPTCHAs and pause
- Notifications if the account is restricted
- Automatic backoff on errors

---

## 🧪 Testing

### Unit Tests
- Tests for the URL builder
- Tests for mappings
- Tests for database operations

### Integration Tests
- End-to-end campaign flow
- LinkedIn authentication
- Error handling

### Manual Testing Checklist
- [ ] Create a campaign with all filters
- [ ] Verify the generated URL
- [ ] Run a real search on LinkedIn
- [ ] Verify results match the filters
- [ ] Test with different combinations

---

## 📝 Documentation

### User Guide
- Quick-start guide
- How to find geoUrn codes
- How to find company/school IDs
- Best practices for messages
- Tips to avoid restrictions

### Developer Guide
- Project architecture
- How to add new filters
- How to extend mappings
- LinkedIn API (unofficial)

---

## 🌐 Internationalization

### Multi-language Support
- UI in Spanish
- UI in other languages
- Localized message templates

---

## 🚀 Performance

### Optimizations
- Search caching
- Batch processing of connections
- Async operations (✅ already implemented)
- Database indexing (✅ already implemented)

---

## 📦 Deployment

### Packaging
- PyPI package
- Docker container
- Standalone executables (PyInstaller)

### Distribution
- Homebrew formula (macOS)
- APT/DNF packages (Linux)
- Chocolatey package (Windows)

---

**Last updated:** 2026-06-10
**Current version:** 0.1.0
**Next planned version:** 0.2.0 (Phase 1 completed)

---

## ✅ Completed in this iteration

- Wired "Manage Campaigns" to the database: toggle activate/deactivate, edit, and delete
  a campaign — previously these were stubs that only printed "In real app: Would...".
- `edit_campaign` now also edits the **targeting filters** (keywords, location,
  industry, connection degree), not just name/limit/template.
- New **contact export to CSV** per campaign (Manage Campaigns → "Export contacts to CSV").
- New **"Look up location code (online)"** utility in Settings: uses the Voyager API
  (`search_location`) to find the geoUrn of any location, authenticating the session.
- **Critical async conversion:** `interactions.py` and `scraping.py` were written in a
  synchronous style over an async page, so CAPTCHA/limit detection and profile/contact
  extraction never worked. Converted to async/await and updated the call sites in
  `linkedin.py` and `checker.py`.
- Fixed `record_daily_analytics`: `DetachedInstanceError` on update (missing `refresh`).
- Fixed duplicate IDs in mappings: `LOCATION_CHOICES` (Atlanta/Phoenix) and `INDUSTRY_CHOICES`
  (Design).
- Updated the login tests to the URL-based detection scheme.
- Added tests for `interactions` and `scraping` (coverage of those modules 15%/0% → 46%/31%).
- Added **CI** (GitHub Actions: `uv sync` + `pytest`), a **Dockerfile**, and `.dockerignore`.
- Rewrote the README (no mojibake, with a ToS disclaimer, CSV export, Docker, testing).
- Removed the "File Editor Demo" (scaffolding unrelated to the product).
- Test suite green: **269 passed**.

### Completed (second batch of pending items)
- **Dynamic location search integrated into the campaign creation AND editing flow**
  ("🔎 Search location online" option), in addition to the utility in Settings. Shared helper
  `_run_location_search` / `_search_location_online`.
- **Exponential backoff on a restricted account** in `send_connection_requests`: it stops on a
  CAPTCHA or the weekly invitation limit and applies exponential backoff (5s→…→300s) after
  consecutive failures.
- **Coverage raised**: `session.py` 0%→97%, `checker.py` 0%→41% (new tests). Total 49%→58%,
  **294 tests**.
- **Packaging to PyPI**: added `[build-system]` (hatchling) + metadata (license, classifiers,
  keywords, urls); `uv build` generates the wheel/sdist and the wheel installs and imports
  correctly in a clean Python 3.13 environment (the `linkedin-cli` entry point works).

### Actually pending (requires a LinkedIn account / outside this environment)
- Verify the geoUrn/industry IDs marked with `❓` against real searches (mitigated: use the
  integrated online lookup or a custom geoUrn; the `❓` ones are clearly marked as unverified).
- End-to-end integration tests against the real LinkedIn (login, search, send) — these require
  credentials and a browser; the rest of the logic is already covered with mocks.
