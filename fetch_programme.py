#!/usr/bin/env python3
"""Fetch the next 3 upcoming OSM programme sessions per section and write program.json.

Requires env vars:
  OSM_CLIENT_ID
  OSM_CLIENT_SECRET
  OSM_REFRESH_TOKEN   — from the authorization_code flow (see argo/get-osm-token.ps1)

Optional:
  OUTPUT_PATH   — defaults to program.json
"""
import os
import json
import base64
import ssl
import datetime
import urllib.request
import urllib.parse
import urllib.error

OSM_BASE      = "https://www.onlinescoutmanager.co.uk"
CLIENT_ID     = os.environ["OSM_CLIENT_ID"]
CLIENT_SECRET = os.environ["OSM_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["OSM_REFRESH_TOKEN"]
OUTPUT_PATH   = os.environ.get("OUTPUT_PATH", "program.json")

SECTIONS = {
    "beavers": 17499,
    "cubs":    14841,
    "scouts":  14834,
}


def osm_post(path, data):
    req = urllib.request.Request(OSM_BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def osm_get(path, token):
    req = urllib.request.Request(OSM_BASE + path)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        print("HTTP", e.code, OSM_BASE + path)
        print("Body:", body[:400])
        raise


def patch_k8s_refresh_token(new_token):
    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    if not os.path.exists(sa):
        print("WARNING: not in-cluster, cannot auto-rotate K8s secret")
        return
    with open(sa + "/token") as f:
        bearer = f.read().strip()
    with open(sa + "/namespace") as f:
        ns = f.read().strip()
    url = (
        "https://kubernetes.default.svc/api/v1/namespaces/"
        + ns + "/secrets/scouts-site-osm-refresh-token"
    )
    patch = json.dumps({
        "data": {"refresh_token": base64.b64encode(new_token.encode()).decode()}
    }).encode()
    ctx = ssl.create_default_context(cafile=sa + "/ca.crt")
    req = urllib.request.Request(url, data=patch, method="PATCH")
    req.add_header("Authorization", "Bearer " + bearer)
    req.add_header("Content-Type", "application/strategic-merge-patch+json")
    try:
        with urllib.request.urlopen(req, context=ctx) as r:
            print("K8s secret patched (HTTP", r.status, ")")
    except Exception as e:
        print("WARNING: could not patch K8s secret:", e)


def get_token():
    body = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "scope":         "section:programme:read",
    }).encode()
    resp = osm_post("/oauth/token", body)
    print("Scope granted:", resp.get("scope", "(none)"))
    new_rt = resp.get("refresh_token")
    if new_rt and new_rt != REFRESH_TOKEN:
        print("OSM issued a new refresh_token — patching K8s secret")
        patch_k8s_refresh_token(new_rt)
    return resp["access_token"]


def get_sections(token):
    return osm_get("/oauth/resource", token)["data"]["sections"]


def find_terms(section_data):
    today = datetime.date.today()
    terms = sorted(section_data.get("terms", []), key=lambda t: t["startdate"])
    current = None
    nxt = None
    for i, term in enumerate(terms):
        start = datetime.date.fromisoformat(term["startdate"])
        end   = datetime.date.fromisoformat(term["enddate"])
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
    for sid_key, tid_key in [("section_id", "term_id"), ("sectionid", "termid")]:
        path = (
            "/ext/programme/meetings/?action=getSummary"
            "&" + sid_key + "=" + str(section_id) +
            "&" + tid_key + "=" + str(term_id)
        )
        try:
            result = osm_get(path, token)
        except urllib.error.HTTPError as e:
            if e.code == 404 and sid_key == "section_id":
                print("  retrying with sectionid/termid params")
                continue
            raise
        data = result.get("data", {}) if isinstance(result, dict) else {}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "meetings", "programme"):
                if key in data:
                    return data[key]
        return []
    return []


def upcoming(token, section_id, section_data, n=3):
    today   = datetime.date.today()
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
        more_f = [
            m for m in more
            if datetime.date.fromisoformat(m.get("meetingdate", "1970-01-01")) >= today
        ]
        more_f.sort(key=lambda m: m["meetingdate"])
        future = (future + more_f)[:n]
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
    "updated":  datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
