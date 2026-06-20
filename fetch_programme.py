#!/usr/bin/env python3
import os
import json
import datetime
import urllib.request
import urllib.parse

OSM_BASE = "https://www.onlinescoutmanager.co.uk"
CLIENT_ID = os.environ["OSM_CLIENT_ID"]
CLIENT_SECRET = os.environ["OSM_CLIENT_SECRET"]
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "program.json")

SECTIONS = {
    "beavers": 17499,
    "cubs":    14841,
    "scouts":  14834,
}


def osm_post(path, data):
    req = urllib.request.Request(OSM_BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def osm_get(path, token):
    req = urllib.request.Request(OSM_BASE + path)
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def get_token():
    body = urllib.parse.urlencode({
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         "section:programme:read",
    }).encode()
    return osm_post("/oauth/token", body)["access_token"]


def get_sections(token):
    return osm_get("/oauth/resource", token)["data"]["sections"]


def find_terms(section_data):
    today = datetime.date.today()
    terms = sorted(section_data.get("terms", []), key=lambda t: t["startdate"])
    current = None
    nxt = None
    for i, term in enumerate(terms):
        start = datetime.date.fromisoformat(term["startdate"])
        end = datetime.date.fromisoformat(term["enddate"])
        if start <= today <= end:
            current = term
            if i + 1 < len(terms):
                nxt = terms[i + 1]
            break
        elif start > today and current is None:
            nxt = term
            break
    return current, nxt


def get_meetings(token, section_id, term_id):
    path = (
        "/ext/programme/meetings/?action=getSummary"
        + "&section_id=" + str(section_id)
        + "&term_id=" + str(term_id)
    )
    result = osm_get(path, token)
    print("    raw response keys:", list(result.keys()) if isinstance(result, dict) else type(result))
    data = result.get("data", {}) if isinstance(result, dict) else {}
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "meetings", "programme"):
            if key in data:
                return data[key]
    return []


def upcoming(token, section_id, section_data, n=3):
    today = datetime.date.today()
    current, nxt = find_terms(section_data)
    meetings = []
    if current:
        meetings += get_meetings(token, section_id, current["term_id"])
    future = [
        m for m in meetings
        if datetime.date.fromisoformat(m.get("meetingdate", "1970-01-01")) >= today
    ]
    future.sort(key=lambda m: m["meetingdate"])
    if len(future) < n and nxt:
        more = get_meetings(token, section_id, nxt["term_id"])
        more_future = [
            m for m in more
            if datetime.date.fromisoformat(m.get("meetingdate", "1970-01-01")) >= today
        ]
        more_future.sort(key=lambda m: m["meetingdate"])
        future = (future + more_future)[:n]
    return future[:n]


def fmt(m):
    title = (m.get("title") or m.get("meetingname") or m.get("name") or "").strip()
    notes = (m.get("notesforparents") or m.get("notes") or "").strip()
    return {
        "date":  m.get("meetingdate", ""),
        "title": title,
        "notes": notes,
    }


token = get_token()
print("Token OK")

all_sections = get_sections(token)
by_id = {s["section_id"]: s for s in all_sections}

output = {
    "updated":  datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "sections": {},
}

for name, sid in SECTIONS.items():
    print(f"  {name} (section {sid})")
    meetings = upcoming(token, sid, by_id.get(sid, {}))
    output["sections"][name] = [fmt(m) for m in meetings]
    print(f"    -> {len(output['sections'][name])} upcoming meetings")

with open(OUTPUT_PATH, "w") as f:
    json.dump(output, f, indent=2)

print("Written", OUTPUT_PATH)
