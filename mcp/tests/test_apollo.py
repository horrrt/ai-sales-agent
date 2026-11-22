"""Apollo lead source: query building, mapping to the Lead schema, and find_leads with Apollo switched on.

No network: apollo._post is replaced by a fake that answers like the documented API.
"""
import pytest

from sales_tools import apollo, store
from sales_tools.tools import research

ICP = {"countries": ["SE", "FI"], "employees_min": 10, "employees_max": 50, "titles": ["CEO", "Head of Sales"]}


def _enriched(pid, n):
    return {"id": pid, "first_name": f"First{n}", "last_name": f"Last{n}", "name": f"First{n} Last{n}", "title": "CEO",
            "email": f"p{n}@co{n}.example", "linkedin_url": f"https://linkedin.com/in/p{n}",
            "organization": {"name": f"Co{n} AB", "website_url": f"https://www.co{n}.example", "primary_domain": None,
                             "industry": "computer software", "estimated_num_employees": 20, "country": "Sweden"}}


class FakeApollo:
    def __init__(self, ids):
        self.ids, self.calls = ids, []

    def __call__(self, path, query=None, body=None):
        self.calls.append((path, query, body))
        if path == "mixed_people/api_search":
            q = dict(query)
            page, per = int(q["page"]), int(q["per_page"])
            chunk = self.ids[(page - 1) * per: page * per]
            return {"people": [{"id": i, "first_name": "X", "last_name_obfuscated": "Y***z"} for i in chunk],
                    "total_entries": len(self.ids)}
        if path == "people/bulk_match":
            assert len(body["details"]) <= 10
            return {"matches": [_enriched(d["id"], self.ids.index(d["id"])) for d in body["details"]]}
        raise AssertionError(path)


def test_search_query_from_icp():
    q = apollo.search_query(ICP, page=2, per_page=500)
    assert ("page", "2") in q and ("per_page", "100") in q
    assert ("person_titles[]", "CEO") in q and ("person_titles[]", "Head of Sales") in q
    assert ("organization_locations[]", "Sweden") in q and ("organization_locations[]", "Finland") in q
    assert ("organization_num_employees_ranges[]", "10,50") in q


def test_to_lead_maps_the_schema():
    lead = apollo.to_lead(_enriched("abc", 1), "C-demo")
    assert lead["lead_id"] == "AP-abc" and lead["source"] == "apollo" and lead["source_id"] == "abc"
    assert lead["person"] == {"name": "First1 Last1", "title": "CEO", "email": "p1@co1.example",
                              "linkedin_url": "https://linkedin.com/in/p1"}
    assert lead["company"]["domain"] == "co1.example"  # from website_url when primary_domain is missing
    assert lead["company"]["country"] == "SE" and lead["company"]["employees"] == 20
    assert lead["stage"] == "new" and lead["status"] == "active"


def test_no_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    with pytest.raises(apollo.ApolloError, match="APOLLO_API_KEY"):
        apollo._post("mixed_people/api_search")


def test_find_leads_pulls_from_apollo_and_skips_known(monkeypatch):
    fake = FakeApollo([f"id{i}" for i in range(12)])
    monkeypatch.setattr(apollo, "_post", fake)
    monkeypatch.setattr(apollo, "ENABLED", True)
    monkeypatch.setattr(store, "SOURCE", {}, raising=False)
    monkeypatch.setattr(research, "SOURCE", store.SOURCE)
    monkeypatch.setattr(research, "LEADS", {})

    first = research.find_leads("C-demo", limit=5)
    assert [c["lead_id"] for c in first] == [f"AP-id{i}" for i in range(5)]
    assert all(c["source"] == "apollo" for c in first)
    enriched = [d["id"] for p, _, b in fake.calls if p == "people/bulk_match" for d in b["details"]]
    assert enriched == [f"id{i}" for i in range(5)]  # only as many as asked for: enrichment costs credits

    fake.calls.clear()
    assert [c["lead_id"] for c in research.find_leads("C-demo", limit=5)] == [f"AP-id{i}" for i in range(5)]
    assert not fake.calls  # five candidates are still waiting, so nothing new is bought

    # After a restart the lead source is empty, but people already in the lead sheet aren't bought again.
    research.LEADS.update({c["lead_id"]: c for c in first})
    store.SOURCE.clear()
    research.find_leads("C-demo", limit=5)
    enriched = [d["id"] for p, _, b in fake.calls if p == "people/bulk_match" for d in b["details"]]
    assert enriched == [f"id{i}" for i in range(5, 10)]


def test_people_without_an_email_are_not_bought(monkeypatch):
    fake = FakeApollo(["a", "b", "c"])
    base = fake.__call__

    def answer(path, query=None, body=None):
        out = base(path, query, body)
        if path == "mixed_people/api_search":
            out["people"][1]["has_email"] = False
        if path == "people/bulk_match":
            for m in out["matches"]:
                if m["id"] == "c":
                    m["email"] = None
        return out

    monkeypatch.setattr(apollo, "_post", answer)
    leads = apollo.find(ICP, "C-demo", limit=5)
    assert [l["source_id"] for l in leads] == ["a"]
    enriched = [d["id"] for p, _, b in fake.calls if p == "people/bulk_match" for d in b["details"]]
    assert enriched == ["a", "c"]
