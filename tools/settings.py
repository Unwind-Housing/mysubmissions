"""Read an organization's settings: clients/<client>/settings.json (what a person fills in) and settings.lock.json (IDs the
tools looked up). The format is in docs/SETTINGS.md. Imported by the build tools; nothing here changes a file or a tenant.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
WEEKLY_FLOW = "Process Financial Submissions v4"


def client_dir(client):
    return ROOT / "clients" / client


def load(client):
    """(settings, lock). The lock is {} for an organization that has not been set up yet."""
    return load_dir(client_dir(client))


def load_dir(base):
    """The same, from any folder that holds a settings.json (template/example, for one)."""
    base = Path(base)
    path = base / "settings.json"
    if not path.exists():
        raise FileNotFoundError(f"No {path}")
    lock = base / "settings.lock.json"
    return json.loads(path.read_text()), (json.loads(lock.read_text()) if lock.exists() else {})


def exists(client):
    return (client_dir(client) / "settings.json").exists()


def flow_file(client, flow_id):
    """The repo copy of the flow with this ID, or None."""
    for p in sorted((client_dir(client) / "flows").glob("*__*.json")):
        if json.loads(p.read_text()).get("name") == flow_id:
            return p
    return None


def group_or_person(value, settings, lock):
    """What an approver setting means inside the app: "" (not set), an email address, or a group's ID.
    A group may be given by ID, or by the name used under approvers.financial or approvers.income."""
    value = (value or "").strip()
    if not value or "@" in value or GUID.match(value.lower()):
        return value
    for key in ("financial", "income"):
        if settings.get("approvers", {}).get(key) == value and lock.get("groups", {}).get(key):
            return lock["groups"][key]
    raise ValueError(f"{value!r} is not an email address, a group ID, or the name of the financial or income approvers group")


def timecard_config(client):
    """Settings in the shape tools/build_timecard_flow.py builds the Process Timecards flow from."""
    s, lock = load(client)
    if not s["features"]["timecards"]:
        raise ValueError(f"{client}: features.timecards is off")
    t, sp = s["timecards"], s["sharepoint"]
    weekly = flow_file(client, lock["flows"]["weekly"])
    if not weekly:
        raise ValueError(f"{client}: the weekly flow in settings.lock.json is not in clients/{client}/flows")
    lists = lock["lists"]
    return {
        "orgName": s["org"]["name"],
        "orgShort": s["org"]["short"],
        "testMode": t["testMode"],
        "tester": s["weekly"]["financeDirector"][0],
        "recipients": t["recipients"],
        "bookkeepers": t["bookkeepers"],
        "listsSite": sp["listsSite"],
        "lists": {"Timecards": lists["timecards"], "Time Entries": lists["timeEntries"], "Payroll Staff": lists["payrollStaff"],
                  "Mileage Rates": lists["mileageRates"], "Financial Submissions": lists["submissions"]},
        "outputSite": sp.get("outputSite", sp["listsSite"]),
        "excelSite": lock["graph"]["outputSite"],
        "outputLibrary": {"id": lock["libraries"]["timecards"], "path": t["library"], "drive": lock["graph"]["timecardsDrive"]},
        "template": t["template"],
        "filePrefix": t["filePrefix"],
        "reimbursements": t["reimbursements"],
        "connectionsFrom": json.loads(weekly.read_text())["properties"]["displayName"],
    }
