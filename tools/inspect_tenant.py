"""Read-only look at one client's tenant: Power Automate flows and SharePoint list columns.

Usage (from the repo root, with .venv active):
    python3 tools/inspect_tenant.py eht

It reads the app's SharePoint sites and lists from clients/<client>/app (see pull_app.py),
signs you in through the browser (twice: once for Power Automate, once for SharePoint), and
writes into clients/<client>/ (committed, so git shows what changed since the last run):
    flows/index.json        every flow you can see, with name, state and dates
    flows/<name>.json       each flow's full definition
    lists/sharepoint.json   columns of every list the app or the flows use (no list items)
and, only with --items "<list name>", that list's rows (read-only) into the gitignored
unpacked/<client>/items/, because rows are client data.

Nothing is changed in the tenant.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

import msal
import requests

ROOT = Path(__file__).resolve().parent.parent
# Microsoft's own public client apps, so no app registration is needed in each tenant.
FLOW_CLIENT = "1950a258-227b-4e31-a9cf-717495945fc2"    # Azure PowerShell (used by the Power Apps PowerShell module)
GRAPH_CLIENT = "14d82eec-204b-4c2f-b7e8-296a70dab67e"   # Microsoft Graph Command Line Tools
FLOW_SCOPE = ["https://service.flow.microsoft.com//.default"]
GRAPH_SCOPE = ["https://graph.microsoft.com/Sites.Read.All"]
FLOW_API = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple"
GRAPH = "https://graph.microsoft.com/v1.0"
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def app_sources(client):
    """SharePoint lists the canvas app connects to: [(site_url, list_id, display_name)]."""
    path = ROOT / "clients" / client / "app" / "References" / "DataSources.json"
    if not path.exists():
        sys.exit(f"Can't find {path}. Pull the app export first: python3 tools/pull_app.py {client} <zip>")
    out = []
    for s in json.loads(path.read_text())["DataSources"]:
        if s.get("Type") == "ConnectedDataSourceInfo" and s.get("DatasetName", "").startswith("https://"):
            out.append((s["DatasetName"].rstrip("/"), s.get("TableName", ""), s.get("Name", "")))
    if not out:
        sys.exit("The app export has no SharePoint data sources.")
    return out


def sign_in(client_id, tenant, scopes, what):
    app = msal.PublicClientApplication(client_id, authority=f"https://login.microsoftonline.com/{tenant}")
    print(f"\nOpening your browser to sign in for {what} ({tenant}).")
    r = app.acquire_token_interactive(scopes=scopes, prompt="select_account", timeout=300)
    if "access_token" not in r:
        raise RuntimeError(r.get("error_description", json.dumps(r)).splitlines()[0])
    print(f"  Signed in as {r.get('id_token_claims', {}).get('preferred_username', '?')}")
    return r["access_token"]


def get_all(session, url):
    items = []
    while url:
        r = session.get(url)
        r.raise_for_status()
        body = r.json()
        items += body.get("value", [])
        url = body.get("nextLink") or body.get("@odata.nextLink")
    return items


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9 ._-]+", "_", s).strip()[:80] or "flow"


# --------------------------------------------------------------------------- flows
def read_flows(token, outdir):
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    v = "api-version=2016-11-01"
    envs = get_all(s, f"{FLOW_API}/environments?{v}")
    index, defs = [], []
    (outdir / "flows").mkdir(parents=True, exist_ok=True)
    for old in (outdir / "flows").glob("*.json"):  # flows deleted in the tenant drop out of the repo too
        old.unlink()
    for env in envs:
        env_name = env["name"]
        env_label = env.get("properties", {}).get("displayName", env_name)
        seen = {}
        for flt in ("", "&$filter=search('personal')", "&$filter=search('team')"):
            try:
                for f in get_all(s, f"{FLOW_API}/environments/{env_name}/flows?{v}{flt}"):
                    seen[f["name"]] = f
            except requests.HTTPError:
                pass
        for fid, f in seen.items():
            p = f.get("properties", {})
            row = {"environment": env_label, "id": fid, "name": p.get("displayName"),
                   "state": p.get("state"), "created": p.get("createdTime"),
                   "modified": p.get("lastModifiedTime")}
            r = s.get(f"{FLOW_API}/environments/{env_name}/flows/{fid}?{v}&$expand=properties.definition")
            if r.ok and r.json().get("properties", {}).get("definition"):
                full = r.json()
                fname = f"{safe_name(row['name'])}__{fid[:8]}.json"
                (outdir / "flows" / fname).write_text(json.dumps(full, indent=2))
                row["file"] = fname
                defs.append(full)
            else:
                row["file"] = None
                row["error"] = f"definition not readable (HTTP {r.status_code})"
            index.append(row)
    (outdir / "flows" / "index.json").write_text(json.dumps(index, indent=2))
    return index, defs


def sharepoint_refs(defs):
    """(site_url, list id or name) pairs used by SharePoint actions and triggers in the flows."""
    refs = set()

    def walk(node):
        if isinstance(node, dict):
            params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
            ds, tb = params.get("dataset"), params.get("table")
            if isinstance(ds, str) and ds.startswith("https://") and isinstance(tb, str) and tb:
                refs.add((ds.rstrip("/"), tb))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for d in defs:
        walk(d.get("properties", {}).get("definition", {}))
    return refs


# --------------------------------------------------------------------------- sharepoint
COLUMN_TYPES = ("text", "choice", "lookup", "number", "currency", "boolean", "dateTime",
                "personOrGroup", "calculated", "hyperlinkOrPicture", "term", "thumbnail")


def column_summary(c):
    kind = next((t for t in COLUMN_TYPES if t in c), "other")
    out = {"name": c.get("name"), "displayName": c.get("displayName"), "type": kind,
           "required": c.get("required", False), "readOnly": c.get("readOnly", False),
           "hidden": c.get("hidden", False), "group": c.get("columnGroup")}
    if kind in ("choice", "lookup", "calculated", "number", "text"):
        out[kind] = c[kind]
    return out


def read_items(token, site_urls, names, outdir):
    """Rows of the named lists (reference data such as projects and cost accounts)."""
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    (outdir / "items").mkdir(parents=True, exist_ok=True)
    for site_url in site_urls:
        u = urlparse(site_url)
        site = s.get(f"{GRAPH}/sites/{u.netloc}:{quote(u.path)}")
        if not site.ok:
            continue
        sid = site.json()["id"]
        lists = get_all(s, f"{GRAPH}/sites/{sid}/lists?$select=id,displayName")
        for name in names:
            l = next((x for x in lists if (x.get("displayName") or "").lower() == name.lower()), None)
            if not l:
                continue
            rows = get_all(s, f"{GRAPH}/sites/{sid}/lists/{l['id']}/items?$expand=fields&$top=999")
            data = [dict(r["fields"], _id=r["id"]) for r in rows]
            (outdir / "items" / f"{safe_name(name)}.json").write_text(json.dumps(data, indent=2))
            print(f"  {name}: {len(data)} rows saved")


def read_lists(token, wanted):
    """wanted: {site_url: set(list ids or names)} -> {site_url: [list info]}"""
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {token}"
    result = {}
    for site_url, keys in wanted.items():
        u = urlparse(site_url)
        site = s.get(f"{GRAPH}/sites/{u.netloc}:{quote(u.path)}")
        if not site.ok:
            result[site_url] = {"error": f"site not readable (HTTP {site.status_code}): {site.text[:200]}"}
            continue
        sid = site.json()["id"]
        lists = get_all(s, f"{GRAPH}/sites/{sid}/lists?$select=id,name,displayName,list,lastModifiedDateTime")
        by_key = {}
        for l in lists:
            for k in (l["id"].lower(), (l.get("displayName") or "").lower(), (l.get("name") or "").lower()):
                by_key[k] = l
        found = []
        for k in sorted(keys):
            l = by_key.get(k.lower())
            if not l:
                found.append({"key": k, "error": "not found on this site"})
                continue
            if any(x.get("id") == l["id"] for x in found):
                continue
            cols = get_all(s, f"{GRAPH}/sites/{sid}/lists/{l['id']}/columns")
            count = len(get_all(s, f"{GRAPH}/sites/{sid}/lists/{l['id']}/items?$select=id&$top=999"))
            found.append({"id": l["id"], "displayName": l.get("displayName"), "name": l.get("name"),
                          "lastModified": l.get("lastModifiedDateTime"), "itemCount": count,
                          "columns": [column_summary(c) for c in cols]})
        result[site_url] = found
    return result


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client", help="the organization's folder name under clients/")
    ap.add_argument("--tenant", help="tenant domain; default: <sharepoint prefix>.onmicrosoft.com")
    ap.add_argument("--skip-flows", action="store_true")
    ap.add_argument("--skip-sharepoint", action="store_true")
    ap.add_argument("--items", action="append", default=[], metavar="LIST",
                    help="also save the rows of this list (repeatable); meant for small reference lists")
    args = ap.parse_args()

    sources = app_sources(args.client)
    tenant = args.tenant or urlparse(sources[0][0]).netloc.split(".")[0] + ".onmicrosoft.com"
    outdir = ROOT / "clients" / args.client
    datadir = ROOT / "unpacked" / args.client  # gitignored: list rows are client data

    wanted = {}
    for site, list_id, _ in sources:
        wanted.setdefault(site, set()).add(list_id)

    if not args.skip_flows:
        try:
            index, defs = read_flows(sign_in(FLOW_CLIENT, tenant, FLOW_SCOPE, "Power Automate"), outdir)
            print(f"\nFlows: {len(index)} found, {len(defs)} definitions saved")
            for row in sorted(index, key=lambda r: (r["name"] or "").lower()):
                mark = "" if row["file"] else f"   ({row.get('error')})"
                print(f"  {row['state'] or '':<10} {row['name']}{mark}")
            for site, table in sharepoint_refs(defs):
                if site.lower().startswith(("https://", "http://")):
                    wanted.setdefault(site, set()).add(table)
        except Exception as e:  # keep going so SharePoint still gets read
            print(f"\nFlows: could not read ({e})")
    else:  # the lists the flows use, from the repo's copies of the flows
        defs = [json.loads(p.read_text()) for p in (outdir / "flows").glob("*__*.json")]
        for site, table in sharepoint_refs(defs):
            if site.lower().startswith(("https://", "http://")):
                wanted.setdefault(site, set()).add(table)

    if not args.skip_sharepoint:
        try:
            token = sign_in(GRAPH_CLIENT, tenant, GRAPH_SCOPE, "SharePoint")
            if args.items:
                print("\nList rows:")
                read_items(token, list(wanted), args.items, datadir)
            sp = read_lists(token, wanted)
            (outdir / "lists").mkdir(parents=True, exist_ok=True)
            (outdir / "lists" / "sharepoint.json").write_text(json.dumps(sp, indent=2) + "\n")
            print("\nSharePoint lists:")
            for site, lists in sp.items():
                print(f"  {site}")
                if isinstance(lists, dict):
                    print(f"    {lists['error']}")
                    continue
                for l in lists:
                    if "error" in l:
                        print(f"    {l['key']}: {l['error']}")
                    else:
                        custom = [c for c in l["columns"] if not c["readOnly"] and not c["hidden"]
                                  and c["group"] != "_Hidden"]
                        print(f"    {l['displayName']:<45} {l['itemCount']:>5} items  {len(custom):>3} editable columns")
        except Exception as e:
            print(f"\nSharePoint: could not read ({e})")

    print(f"\nSaved to {outdir}")


if __name__ == "__main__":
    main()
