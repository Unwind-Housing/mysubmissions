"""Push edited flow definitions from clients/<client>/flows to the client's Power Automate.

Usage (from the repo root, .venv active):
    python3 tools/push_flows.py eht                          dry run: which flows differ from main
    python3 tools/push_flows.py eht --apply                  push them
    python3 tools/push_flows.py eht --apply --turn-off "Retired - Process Financial Submissions"

A flow is pushed only if its definition in the working tree differs from the one on --base
(default: main), and only if the live flow still matches --base, so edits made in Power
Automate since the last pull are never overwritten. Before each push the live flow is saved to
unpacked/<client>/flow-backups/ (gitignored); after it, the flow is read back and compared,
and the repo copy is refreshed from the tenant.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_tenant import FLOW_API, FLOW_CLIENT, FLOW_SCOPE, ROOT, sign_in  # noqa: E402

V = "api-version=2016-11-01"


def base_version(path, base):
    rel = path.relative_to(ROOT).as_posix()
    r = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def changed_flows(client, base):
    out = []
    for path in sorted((ROOT / "clients" / client / "flows").glob("*__*.json")):
        local = json.loads(path.read_text())
        old = base_version(path, base)
        if old is None:
            continue
        if local["properties"]["definition"] != old["properties"]["definition"]:
            out.append((path, local, old))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client")
    ap.add_argument("--base", default="main", help="git ref the live flows should match (default main)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--turn-off", action="append", default=[], metavar="FLOW NAME")
    args = ap.parse_args()

    todo = changed_flows(args.client, args.base)
    print(f"Flows that differ from {args.base}:" if todo else f"No flow definitions differ from {args.base}.")
    for path, local, _ in todo:
        print(f"  {local['properties']['displayName']}")
    for name in args.turn_off:
        print(f"Turn off: {name}")
    if not args.apply:
        print("\nDry run: nothing changed. Add --apply to push.")
        return
    if not todo and not args.turn_off:
        return

    # Tenant from the app's SharePoint site, as the inspector does.
    ds = json.loads((ROOT / "clients" / args.client / "app" / "References" / "DataSources.json").read_text(encoding="utf-8-sig"))
    site = next(s["DatasetName"] for s in ds["DataSources"] if str(s.get("DatasetName", "")).startswith("https://"))
    tenant = urlparse(site).netloc.split(".")[0] + ".onmicrosoft.com"

    import requests
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {sign_in(FLOW_CLIENT, tenant, FLOW_SCOPE, 'Power Automate (update flows)')}"
    backups = ROOT / "unpacked" / args.client / "flow-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backups.mkdir(parents=True, exist_ok=True)
    failed = False

    for path, local, old in todo:
        name = local["properties"]["displayName"]
        url = f"{FLOW_API}{local['id'].split('/providers/Microsoft.ProcessSimple', 1)[1]}"
        live = s.get(f"{url}?{V}&$expand=properties.definition")
        live.raise_for_status()
        live = live.json()
        (backups / path.name).write_text(json.dumps(live, indent=2))
        if live["properties"]["definition"] != old["properties"]["definition"]:
            print(f"SKIPPED {name}: it was changed in Power Automate since the last pull. Pull it, redo the edit, try again.")
            failed = True
            continue
        r = s.patch(f"{url}?{V}", json={"properties": {"definition": local["properties"]["definition"]}})
        if not r.ok:
            print(f"FAILED {name}: HTTP {r.status_code} {r.text[:500]}")
            failed = True
            continue
        after = s.get(f"{url}?{V}&$expand=properties.definition").json()
        if after["properties"]["definition"] != local["properties"]["definition"]:
            print(f"CHECK {name}: pushed, but the live definition doesn't match exactly; saved what the tenant has")
            failed = True
        else:
            print(f"Updated {name} (state: {after['properties'].get('state')})")
        path.write_text(json.dumps(after, indent=2))

    for name in args.turn_off:
        match = [p for p in (ROOT / "clients" / args.client / "flows").glob("*__*.json")
                 if json.loads(p.read_text())["properties"]["displayName"] == name]
        if not match:
            print(f"Turn off: no flow named {name!r}")
            failed = True
            continue
        doc = json.loads(match[0].read_text())
        url = f"{FLOW_API}{doc['id'].split('/providers/Microsoft.ProcessSimple', 1)[1]}"
        r = s.post(f"{url}/stop?{V}")
        if r.ok:
            after = s.get(f"{url}?{V}&$expand=properties.definition").json()
            match[0].write_text(json.dumps(after, indent=2))
            print(f"Turned off {name} (state: {after['properties'].get('state')})")
        else:
            print(f"FAILED to turn off {name}: HTTP {r.status_code} {r.text[:300]}")
            failed = True

    print(f"Backups of the previous live flows: {backups}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
