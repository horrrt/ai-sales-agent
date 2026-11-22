"""Apollo.io as the lead source. Owner: Kyriakos.

On when SALES_LEADS=apollo and APOLLO_API_KEY is set; find_leads then asks Apollo instead of the sample leads.

Two calls per batch (docs.apollo.io, read 2026-11-22):
1. People API Search, POST /api/v1/mixed_people/api_search: filters from the brief's ICP (titles, countries,
   employee range). Costs no credits, but hides the last name, email and company details.
2. Bulk People Enrichment, POST /api/v1/people/bulk_match, at most 10 people per call: fills in name, email,
   LinkedIn URL and the company. Costs about 1 credit per person found, so `limit` caps the spend.

Apollo's industry names don't match ours ("B2B SaaS"), so industry is left to the d1 ICP check in add_lead.
Only people search marks has_email are enriched, and matches without an email are dropped.
The key needs the api_search and bulk_match scopes (or a master key), else Apollo answers 403.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.apollo.io/api/v1"
ENABLED = os.environ.get("SALES_LEADS") == "apollo"

# Apollo filters locations by name, our brief uses ISO-2 codes.
COUNTRY_NAMES = {
    "DK": "Denmark", "SE": "Sweden", "NO": "Norway", "FI": "Finland", "IS": "Iceland", "DE": "Germany",
    "AT": "Austria", "CH": "Switzerland", "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg",
    "FR": "France", "GB": "United Kingdom", "UK": "United Kingdom", "IE": "Ireland", "ES": "Spain",
    "PT": "Portugal", "IT": "Italy", "PL": "Poland", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "CZ": "Czechia", "US": "United States",
}
COUNTRY_CODES = {v.lower(): k for k, v in COUNTRY_NAMES.items() if k != "UK"}


class ApolloError(RuntimeError):
    pass


def _post(path: str, query: list[tuple[str, str]] | None = None, body: dict | None = None) -> dict:
    key = os.environ.get("APOLLO_API_KEY")
    if not key:
        raise ApolloError("APOLLO_API_KEY is not set")
    url = f"{BASE}/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    req = urllib.request.Request(url, data=json.dumps(body or {}).encode(), method="POST", headers={
        "x-api-key": key, "Content-Type": "application/json", "Cache-Control": "no-cache", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise ApolloError(f"Apollo {path} answered {e.code}: {detail}") from e


def search_query(icp: dict, page: int = 1, per_page: int = 10) -> list[tuple[str, str]]:
    """Query parameters for api_search from the brief's ICP."""
    q: list[tuple[str, str]] = [("page", str(page)), ("per_page", str(min(per_page, 100)))]
    q += [("person_titles[]", t) for t in icp.get("titles") or []]
    if icp.get("titles"):
        q.append(("include_similar_titles", "true"))
    q += [("organization_locations[]", COUNTRY_NAMES.get(c.upper(), c)) for c in icp.get("countries") or []]
    lo, hi = icp.get("employees_min"), icp.get("employees_max")
    if lo or hi:
        q.append(("organization_num_employees_ranges[]", f"{lo or 1},{hi or 1000000}"))
    return q


def to_lead(person: dict, customer_id: str) -> dict:
    """One enriched Apollo person in the Lead schema shape, as a candidate (stage new, no research)."""
    org = person.get("organization") or {}
    name = person.get("name") or " ".join(x for x in (person.get("first_name"), person.get("last_name")) if x)
    domain = org.get("primary_domain") or urllib.parse.urlparse(org.get("website_url") or "").netloc.removeprefix("www.")
    country = org.get("country") or person.get("country") or ""
    return {
        "lead_id": f"AP-{person['id']}",
        "customer_id": customer_id,
        "source": "apollo",
        "source_id": person["id"],
        "person": {"name": name, "title": person.get("title") or "", "email": person.get("email") or "",
                   "linkedin_url": person.get("linkedin_url") or ""},
        "company": {"name": org.get("name") or "", "domain": domain or "",
                    "country": COUNTRY_CODES.get(country.lower(), country), "employees": org.get("estimated_num_employees"),
                    "industry": org.get("industry") or ""},
        "stage": "new", "status": "active", "loss_reason": None,
    }


def find(icp: dict, customer_id: str, limit: int = 10, skip_ids: set[str] = frozenset()) -> list[dict]:
    """Search Apollo for people matching the ICP, enrich the new ones, and return them as candidate leads.

    skip_ids: Apollo ids we already have, so they aren't enriched (and charged) again.
    """
    found: list[dict] = []
    page = 1
    while len(found) < limit and page <= 5:
        res = _post("mixed_people/api_search", search_query(icp, page=page, per_page=max(limit, 10)))
        # has_email is free in search; enriching someone without one would pay for a lead we can't email
        people = [p for p in res.get("people") or [] if p.get("id") and p["id"] not in skip_ids and p.get("has_email") is not False]
        for i in range(0, len(people), 10):
            batch = people[i:i + 10][: limit - len(found)]
            if not batch:
                break
            out = _post("people/bulk_match", body={"details": [{"id": p["id"]} for p in batch]})
            found += [to_lead(m, customer_id) for m in out.get("matches") or [] if m and m.get("id") and m.get("email")]
            if len(found) >= limit:
                break
        if len(res.get("people") or []) < max(limit, 10):
            break  # last page
        page += 1
    return found[:limit]
