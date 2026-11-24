"""Build an organization's weekly flow (the package for the bookkeepers) from the shared template and its settings.

Usage (from the repo root, .venv active):
    python3 tools/build_weekly_flow.py eht                        compare: template + eht's settings against eht's flow
    python3 tools/build_weekly_flow.py <folder> --apply           create the flow with your own connections
    python3 tools/build_weekly_flow.py <folder> --apply --connections-from eht
                                                                  or with the connections of eht's weekly flow
                                                                  create the flow (or update the one this made before)
    python3 tools/build_weekly_flow.py --refresh-from eht         remake template/flows/weekly.json from eht's flow

template/flows/weekly.json is one organization's weekly flow with every value that belongs to one organization replaced by a token.
Stamping puts an organization's values in from settings.json and settings.lock.json, then applies the weekly settings
(test mode, and the trigger and reply that let the app's Send to bookkeeper button start it). Without --apply nothing
outside the repo is touched. With it, the flow is created in the environment named in the lock file, using the signed-in
person's own SharePoint, Excel Online (Business) and Office 365 Outlook connections (or, with --connections-from, those
of another organization's flow in the same environment), and its ID is written to the lock file.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_settings  # noqa: E402
import settings as st  # noqa: E402

TEMPLATE = st.ROOT / "template" / "flows" / "weekly.json"
DEFAULT_NAME = st.WEEKLY_FLOW
DEFAULT_GREETING = "Hello!"
REMINDERS = "__MS_LIST_REMINDERS__"


def _map(obj, fn):
    """Apply fn to every string in a JSON structure, keys included."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_map(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {fn(k): _map(v, fn) for k, v in obj.items()}
    return obj


def _actions(actions):
    for a in actions.values():
        yield a
        yield from _actions(a.get("actions", {}))
        yield from _actions(a.get("else", {}).get("actions", {}))


def template_id(library, template):
    """How the SharePoint connector names a file by its path: /Library/File.xlsx, encoded twice."""
    enc = lambda p: p.replace("/", "%252f").replace(" ", "%2b")
    return "%252f" + enc(library) + "%252f" + enc(template)


def tokens(s, lock):
    """{token: value} for one organization."""
    w, sp, graph = s["weekly"], s["sharepoint"], lock.get("graph", {})
    out = {
        "__MS_LISTS_SITE__": sp["listsSite"],
        "__MS_OUTPUT_SITE__": sp.get("outputSite", sp["listsSite"]),
        "__MS_LIST_SUBMISSIONS__": lock.get("lists", {}).get("submissions"),
        "__MS_LIST_REMINDERS__": lock.get("lists", {}).get("reminders"),
        "__MS_LIBRARY_WEEKLY__": lock.get("libraries", {}).get("weekly"),
        "__MS_EXCEL_SOURCE__": graph.get("weeklyExcelSource"),
        "__MS_EXCEL_DRIVE__": graph.get("weeklyDrive"),
        "__MS_TEMPLATE_ID__": template_id(w["library"], w["template"]),
        "__MS_LIBRARY_NAME__": w["library"],
        "__MS_SUBJECT__": w["subject"],
        "__MS_ORG_NAME__": s["org"]["name"],
        "__MS_GREETING__": w.get("greeting", DEFAULT_GREETING),
        "__MS_RECIPIENTS__": ";".join(w["recipients"]),
        "__MS_BOOKKEEPERS__": ";".join(w["bookkeepers"]),
        "__MS_FINANCE_DIRECTOR__": ";".join(w["financeDirector"]),
        "__MS_REPLY_TO__": w.get("replyTo", w["financeDirector"][0]),
    }
    missing = sorted(k for k, v in out.items() if not v and k != REMINDERS)   # the reminders list only once the flow reads it
    if missing:
        raise ValueError(f"no value for {', '.join(missing)}. The lists come from tools/setup_lists.py, the library and its IDs "
                         "from tools/setup_lists.py --weekly.")
    return out


def definition(s, lock):
    """The organization's weekly flow definition."""
    values = tokens(s, lock)

    def fill(text):
        for token, value in values.items():
            if value:
                text = text.replace(token, value)
        if REMINDERS in text:
            raise ValueError(f"no value for {REMINDERS}: the Payment Reminders list is missing. Make it with tools/setup_lists.py.")
        if "__MS_" in text:
            raise ValueError(f"unknown token in the flow template: {text[text.index('__MS_'):][:40]!r}")
        return text
    d = _map(json.loads(TEMPLATE.read_text())["definition"], fill)
    apply_settings.weekly_changes(d, s)                                  # test mode, file links, start from the app
    return d


def refresh(client):
    """Remake the template from a client's weekly flow."""
    s, lock = st.load(client)
    w = s["weekly"]
    doc = json.loads(st.flow_file(client, lock["flows"]["weekly"]).read_text())["properties"]
    d = json.loads(json.dumps(doc["definition"]))
    values = tokens(s, lock)
    lists_site, output_site = values["__MS_LISTS_SITE__"], values["__MS_OUTPUT_SITE__"]
    lists = (values["__MS_LIST_SUBMISSIONS__"], values["__MS_LIST_REMINDERS__"])
    # Which site an action talks to is decided by what it touches: one of the lists, or the output library.
    for a in _actions(d["actions"]):
        p = a.get("inputs", {}).get("parameters") if isinstance(a.get("inputs"), dict) else None
        if isinstance(p, dict) and p.get("dataset") in (lists_site, output_site):
            p["dataset"] = "__MS_LISTS_SITE__" if p.get("table") in lists else "__MS_OUTPUT_SITE__"
    mail = d["actions"]["Send_an_email_(V2)"]["inputs"]["parameters"]
    to, rest = mail["emailMessage/To"].split("@{", 1)
    if to != values["__MS_FINANCE_DIRECTOR__"]:
        raise ValueError("the email's To line does not start with the finance director's address")
    mail["emailMessage/To"] = "__MS_FINANCE_DIRECTOR__@{" + rest
    mail["emailMessage/ReplyTo"] = "__MS_REPLY_TO__"
    for a in d["actions"].values():
        if a.get("type") == "InitializeVariable":
            v = a["inputs"]["variables"][0]
            if v["name"] in ("Recipients", "Bookkeepers"):
                v["value"] = f"__MS_{v['name'].upper()}__"
    order = ["__MS_TEMPLATE_ID__", "__MS_EXCEL_DRIVE__", "__MS_EXCEL_SOURCE__", "__MS_LIST_SUBMISSIONS__", "__MS_LIST_REMINDERS__",
             "__MS_LIBRARY_WEEKLY__",
             "__MS_OUTPUT_SITE__", "__MS_SUBJECT__", "__MS_ORG_NAME__", "__MS_GREETING__"]
    counts = {}

    def strip(text):
        for token in order:
            counts[token] = counts.get(token, 0) + text.count(values[token])
            text = text.replace(values[token], token)
        slashed = f"/{w['library']}/"
        counts["__MS_LIBRARY_NAME__"] = counts.get("__MS_LIBRARY_NAME__", 0) + text.count(slashed)
        return text.replace(slashed, "/__MS_LIBRARY_NAME__/")
    d = _map(d, strip)
    unused = sorted(k for k, n in counts.items() if not n)
    if unused:
        raise ValueError(f"{client}'s flow never uses its own value for {', '.join(unused)}")
    connections = {k: r["id"] for k, r in doc["connectionReferences"].items()}
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE.write_text(json.dumps({"definition": d, "connections": connections}, indent=2) + "\n")
    return counts


CONNECTORS = {"shared_sharepointonline": "SharePoint", "shared_excelonlinebusiness": "Excel Online (Business)", "shared_office365": "Office 365 Outlook"}


def pick_connections(connections, user=None):
    """The template's connection references, each pointed at one of the person's own connections (as the connections API
    lists them: name, properties.apiId, properties.statuses, properties.createdBy). Raises ValueError naming any
    connector the person has no working connection for. Prefers a connection the person owns; among several, the
    newest."""
    want = json.loads(TEMPLATE.read_text())["connections"]
    chosen, missing = {}, []
    for api in sorted(set(want.values())):
        mine = [c for c in connections if c.get("properties", {}).get("apiId") == api
                and all(s.get("status") == "Connected" for s in c["properties"].get("statuses", []) or [{"status": "Connected"}])]
        if user:
            owned = [c for c in mine if (c["properties"].get("createdBy") or {}).get("userPrincipalName", "").lower() == user.lower()]
            mine = owned or mine
        if not mine:
            missing.append(CONNECTORS.get(api.rsplit("/", 1)[-1], api.rsplit("/", 1)[-1]))
            continue
        mine.sort(key=lambda c: c["properties"].get("createdTime", ""), reverse=True)
        chosen[api] = mine[0]["name"]
    if missing:
        raise ValueError("no connection for " + ", ".join(missing) + ". Make one at make.powerautomate.com > More > Connections > "
                         "+ New connection (sign in with the account that will own the flow), then run this again.")
    return {key: {"connectionName": chosen[api], "source": "Invoker", "id": api} for key, api in want.items()}


POWERAPPS_SCOPE = ["https://service.powerapps.com//.default"]
CONNECTIONS_API = "https://api.powerapps.com/providers/Microsoft.PowerApps/connections"


def own_connections(token, environment):
    """The signed-in person's connections in the environment, from the Power Apps connections API (a different service
    from the flow API, so it takes its own token)."""
    import requests
    r = requests.get(CONNECTIONS_API, headers={"Authorization": f"Bearer {token}"},
                     params={"api-version": "2016-11-01", "$filter": f"environment eq '{environment}'"})
    if not r.ok:
        raise RuntimeError(f"could not list connections: HTTP {r.status_code} {r.text[:400]}")
    return r.json().get("value", [])


def sign_in_twice(tenant, first, second, what):
    """One browser sign-in for two services: the second token is taken silently from the first sign-in when the
    identity service allows it, and only otherwise asks again."""
    import msal
    from inspect_tenant import FLOW_CLIENT
    app = msal.PublicClientApplication(FLOW_CLIENT, authority=f"https://login.microsoftonline.com/{tenant}")
    print(f"\nOpening your browser to sign in for {what} ({tenant}).")
    r = app.acquire_token_interactive(scopes=first, prompt="select_account", timeout=300)
    if "access_token" not in r:
        raise RuntimeError(r.get("error_description", json.dumps(r)).splitlines()[0])
    print(f"  Signed in as {r.get('id_token_claims', {}).get('preferred_username', '?')}")
    accounts = app.get_accounts()
    s = app.acquire_token_silent(second, account=accounts[0]) if accounts else None
    if not s or "access_token" not in s:
        print("  One more sign-in, for the connections list.")
        s = app.acquire_token_interactive(scopes=second, timeout=300)
        if "access_token" not in s:
            raise RuntimeError(s.get("error_description", json.dumps(s)).splitlines()[0])
    return r["access_token"], s["access_token"]


def connection_refs(source_props):
    """Connection references for a new flow: the template's names, each pointed at the source flow's connection for that
    service."""
    by_api = {r["id"]: r for r in source_props["connectionReferences"].values()}
    out = {}
    for key, api in json.loads(TEMPLATE.read_text())["connections"].items():
        if api not in by_api:
            raise ValueError(f"the flow the connections come from has no connection for {api.rsplit('/', 1)[-1]}")
        out[key] = {"connectionName": by_api[api]["connectionName"], "source": by_api[api].get("source", "Invoker"), "id": api}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("where", nargs="?", help="a client's short name, or a folder that holds settings.json")
    ap.add_argument("--refresh-from", metavar="CLIENT", help="remake the template from this client's weekly flow")
    ap.add_argument("--apply", action="store_true", help="create or update the flow in Power Automate")
    ap.add_argument("--connections-from", metavar="CLIENT", help="with --apply: borrow the connections of this client's weekly flow instead of using your own")
    args = ap.parse_args()
    if args.refresh_from:
        for k, n in sorted(refresh(args.refresh_from).items()):
            print(f"  {k}: {n}")
        print(f"{TEMPLATE.relative_to(st.ROOT)} remade from {args.refresh_from}'s weekly flow.")
        return
    if not args.where:
        ap.error("give a client or a settings folder, or --refresh-from CLIENT")
    base = Path(args.where) if Path(args.where).is_dir() else st.client_dir(args.where)
    s, lock = st.load_dir(base)
    d = definition(s, lock)
    name = s["weekly"].get("flowName", DEFAULT_NAME)
    flows = base / "flows"
    existing = lock.get("flows", {}).get("weeklyBuilt")                 # only ever update a flow this tool created
    print(f"{name}: {len(d['actions'])} top-level steps, test mode {'ON' if s['weekly']['testMode'] else 'off'}, "
          f"{'started from the app' if s['weekly']['runFromApp'] else 'started from Power Automate'}")
    print(f"  reads   {s['sharepoint']['listsSite']} (the submissions list)")
    print(f"  writes  {s['sharepoint'].get('outputSite', s['sharepoint']['listsSite'])}/{s['weekly']['library']}")
    print(f"  emails  {'; '.join(s['weekly']['financeDirector'])}" + ("" if s["weekly"]["testMode"] else "; " + "; ".join(s["weekly"]["recipients"])))
    if not args.apply:
        current = st.flow_file(base.name, lock.get("flows", {}).get("weekly", "")) if base.parent == st.ROOT / "clients" else None
        if current:
            same = json.loads(current.read_text())["properties"]["definition"] == d
            print("Identical to the flow in the repo." if same else "DIFFERS from the flow in the repo.")
            sys.exit(0 if same else 1)
        print("Dry run: nothing created. Add --apply to create the flow with your own connections.")
        return
    if not re.fullmatch(r"Default-[0-9a-f-]{36}", lock.get("environment", "")) or lock["environment"].startswith("Default-00000000"):
        sys.exit("STOPPED: the lock file's environment is not set. Run tools/setup_lists.py <org> --apply first (it records it), or put "
                 "Default-<your tenant ID> under environment in settings.lock.json.")

    import requests
    from inspect_tenant import FLOW_API, FLOW_CLIENT, FLOW_SCOPE, safe_name, sign_in
    from urllib.parse import urlparse
    v = "api-version=2016-11-01"
    tenant = urlparse(s["sharepoint"]["listsSite"]).netloc.split(".")[0] + ".onmicrosoft.com"
    env_path = f"/environments/{lock['environment']}"
    if args.connections_from:
        src_lock = st.load(args.connections_from)[1]
        src = json.loads(st.flow_file(args.connections_from, src_lock["flows"]["weekly"]).read_text())
        if src_lock["environment"] != lock["environment"]:
            sys.exit("STOPPED: the connections have to come from a flow in the same environment.")
        refs = connection_refs(src["properties"])
    http = requests.Session()
    if args.connections_from:
        http.headers["Authorization"] = f"Bearer {sign_in(FLOW_CLIENT, tenant, FLOW_SCOPE, 'Power Automate (weekly flow)')}"
    else:
        flow_token, apps_token = sign_in_twice(tenant, FLOW_SCOPE, POWERAPPS_SCOPE, "Power Automate (weekly flow and your connections)")
        http.headers["Authorization"] = f"Bearer {flow_token}"
        try:
            refs = pick_connections(own_connections(apps_token, lock["environment"]))
        except (ValueError, RuntimeError) as e:
            sys.exit(f"STOPPED: {e}")
        used = {r["connectionName"] for r in refs.values()}
        print(f"  using your own connections: {', '.join(sorted(CONNECTORS[k.rsplit('/', 1)[-1]] for k in {r['id'] for r in refs.values()}))} ({len(used)} connections)")
    body = {"properties": {"displayName": name, "definition": d, "connectionReferences": refs, "state": "Started"}}
    if existing:
        r = http.patch(f"{FLOW_API}{env_path}/flows/{existing}?{v}", json=body)
    else:
        live = http.get(f"{FLOW_API}{env_path}/flows?{v}")
        if live.ok and any(x["properties"].get("displayName") == name for x in live.json().get("value", [])):
            sys.exit(f"STOPPED: a flow named {name!r} already exists there. Give this one another name (weekly.flowName), or record the existing flow's ID in the lock file.")
        r = http.post(f"{FLOW_API}{env_path}/flows?{v}", json=body)
    if not r.ok:
        sys.exit(f"FAILED: HTTP {r.status_code} {r.text[:1500]}")
    fid = r.json()["name"]
    full = http.get(f"{FLOW_API}{env_path}/flows/{fid}?{v}&$expand=properties.definition").json()
    full["properties"].pop("installedConnectionReferences", None)        # the API's expansion of each connection: bulk, not definition
    flows.mkdir(exist_ok=True)
    (flows / f"{safe_name(name)}__{fid[:8]}.json").write_text(json.dumps(full, indent=2))
    lock.setdefault("flows", {}).update(weekly=fid, weeklyBuilt=fid)
    (base / "settings.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    print(f"{'Updated' if existing else 'Created'} {name} ({fid}), state {full['properties'].get('state')}. Its ID is in {base / 'settings.lock.json'}.")
    print("REMINDER: a flow run from the app has to be added to the app once, and every package import switches it off.")


if __name__ == "__main__":
    main()
