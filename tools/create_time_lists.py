"""Create the time tracking lists on a client's sites.

Usage (from the repo root, .venv active):
    python3 tools/create_time_lists.py eht <lists site url> <output site url>           dry run: prints the plan
    python3 tools/create_time_lists.py eht <lists site url> <output site url> --apply   creates them

On the lists site (where Chargeable Projects lives) it creates six lists: Payroll Staff, Staff Allocations,
Timecards, Time Entries, Holidays and Mileage Rates, and adds a "Chargeable" yes/no column to Chargeable
Projects (a project shows on time cards when it is ticked). On the output site it creates the document
library "Approved Timecard Submissions". Holidays is filled with the US federal holidays and Mileage Rates
with the current year's rate.

It only adds things. It stops, changing nothing, if any of the lists, the library or the column already exists.
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_tenant import GRAPH, GRAPH_CLIENT, GRAPH_SCOPE, ROOT, get_all, sign_in  # noqa: E402

WRITE_SCOPE = ["https://graph.microsoft.com/Sites.Manage.All"]
PROJECTS_LIST = "Chargeable Projects"
LIBRARY = "Approved Timecard Submissions"

PERSON = {"personOrGroup": {"allowMultipleSelection": False, "chooseFromType": "peopleOnly"}}
DATE = {"dateTime": {"format": "dateOnly"}}
DATETIME = {"dateTime": {"format": "dateTime"}}
NOTES = {"text": {"allowMultipleLines": True, "linesForEditing": 4}}
MONEY = {"currency": {"locale": "en-us"}}


def choice(*values, default=None):
    col = {"choice": {"choices": list(values), "displayAs": "dropDownMenu", "allowTextEntry": False}}
    if default:
        col["defaultValue"] = {"value": default}
    return col


def col(name, display, kind, required=False):
    return {"name": name, "displayName": display, "required": required, **kind}


# "lookup" columns are filled in at run time: (name, display name, target list, required)
LISTS = [
    {"displayName": "Payroll Staff", "title": "Employee Name", "columns": [
        col("Employee", "Employee", PERSON, True),
        col("FTE", "FTE", {"number": {"decimalPlaces": "two", "minimum": 0, "maximum": 1}}, True),
        col("PayType", "Pay Type", choice("Salaried", "Hourly", default="Salaried")),
        col("EntryMode", "Entry Mode", choice("Central percentages", "Daily entry", default="Central percentages")),
        col("CellPhone", "Cell Phone Amount", MONEY),
        {"name": "Active", "displayName": "Active", "boolean": {}, "defaultValue": {"value": "1"}},
    ], "lookups": []},
    {"displayName": "Staff Allocations", "title": "Note", "columns": [
        col("Employee", "Employee", PERSON, True),
        col("Percent", "Percent", {"number": {"decimalPlaces": "one", "minimum": 0, "maximum": 100}}, True),
    ], "lookups": [("Project", "Project", PROJECTS_LIST, True)]},
    {"displayName": "Timecards", "title": "Timecard", "columns": [
        col("Employee", "Employee", PERSON, True),
        col("MonthStart", "Month", DATE, True),
        col("Status", "Status", choice("Draft", "Submitted", "Approved", "Returned", "Processed", default="Draft")),
        col("SubmittedOn", "Submitted On", DATETIME),
        col("ApprovedBy", "Approved By", PERSON),
        col("ApprovedOn", "Approved On", DATETIME),
        col("ReturnReason", "Return Reason", NOTES),
        col("Mileage", "Mileage", {"number": {"decimalPlaces": "one", "minimum": 0}}),
        col("MileageNotes", "Mileage Notes", NOTES),
        col("Notes", "Notes", NOTES),
        col("ExtendedHealthBenefits", "Extended Health Benefits", MONEY),
        col("ProcessedDate", "Processed Date", DATE),
    ], "lookups": []},
    {"displayName": "Time Entries", "title": "Entry", "columns": [
        col("EntryDate", "Date", DATE, True),
        col("Category", "Category", choice("Worked", "PTO", "Sick", "Holiday", "Floating holiday", default="Worked"), True),
        col("Hours", "Hours", {"number": {"decimalPlaces": "two", "minimum": 0, "maximum": 24}}, True),
    ], "lookups": [("Timecard", "Timecard", "Timecards", True), ("Project", "Project", PROJECTS_LIST, False)]},
    {"displayName": "Holidays", "title": "Holiday", "columns": [col("HolidayDate", "Date", DATE, True)], "lookups": []},
    {"displayName": "Mileage Rates", "title": "Year", "columns": [
        col("Rate", "Rate Per Mile", {"number": {"decimalPlaces": "three", "minimum": 0}}, True)], "lookups": []},
]
CHARGEABLE = {"name": "TimeChargeable", "displayName": "Chargeable", "boolean": {}, "defaultValue": {"value": "0"},
              "description": "Tick to show this project on time cards."}

# US federal holidays on the day they are observed (weekday checked below)
HOLIDAYS = [
    ("Veterans Day", date(2026, 11, 11)), ("Thanksgiving Day", date(2026, 11, 26)), ("Christmas Day", date(2026, 12, 25)),
    ("New Year's Day", date(2027, 1, 1)), ("Martin Luther King Jr. Day", date(2027, 1, 18)),
    ("Presidents' Day", date(2027, 2, 15)), ("Memorial Day", date(2027, 5, 31)),
    ("Juneteenth (observed)", date(2027, 6, 18)), ("Independence Day (observed)", date(2027, 7, 5)),
    ("Labor Day", date(2027, 9, 6)), ("Columbus Day / Indigenous Peoples' Day", date(2027, 10, 11)),
    ("Veterans Day", date(2027, 11, 11)), ("Thanksgiving Day", date(2027, 11, 25)),
    ("Christmas Day (observed)", date(2027, 12, 24)),
]
assert all(d.weekday() < 5 for _, d in HOLIDAYS), [n for n, d in HOLIDAYS if d.weekday() >= 5]
ITEMS = {
    "Holidays": [{"Title": n, "HolidayDate": d.isoformat() + "T12:00:00Z"} for n, d in HOLIDAYS],
    "Mileage Rates": [{"Title": "2026", "Rate": 0.70}],
}


class Site:
    def __init__(self, token, site_url):
        import requests
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self.url = site_url
        u = urlparse(site_url)
        r = self.s.get(f"{GRAPH}/sites/{u.netloc}:{quote(u.path)}")
        r.raise_for_status()
        self.id = r.json()["id"]

    def call(self, method, path, body=None):
        r = self.s.request(method, f"{GRAPH}/sites/{self.id}{path}", json=body)
        if not r.ok:
            raise RuntimeError(f"{method} {path} failed (HTTP {r.status_code}): {r.text[:400]}")
        return r.json() if r.text else {}

    def lists(self):
        return {l["displayName"].lower(): l["id"] for l in get_all(self.s, f"{GRAPH}/sites/{self.id}/lists?$select=displayName,id")}

    def column_names(self, list_id):
        return {c["name"].lower() for c in self.call("GET", f"/lists/{list_id}/columns")["value"]} | \
               {c["displayName"].lower() for c in self.call("GET", f"/lists/{list_id}/columns")["value"]}


def plan(ops_url, out_url):
    print(f"Lists site:  {ops_url}")
    for spec in LISTS:
        cols = [f"{c['displayName']}{'*' if c.get('required') else ''}" for c in spec["columns"]]
        cols += [f"{d}{'*' if req else ''} (lookup to {target})" for _, d, target, req in spec["lookups"]]
        print(f"  new list  {spec['displayName']:<18} first column \"{spec['title']}\"; {', '.join(cols)}")
        if spec["displayName"] in ITEMS:
            print(f"            filled with {len(ITEMS[spec['displayName']])} rows")
    print(f"  new column on {PROJECTS_LIST}: \"{CHARGEABLE['displayName']}\" (yes/no, default no)")
    print(f"Output site: {out_url}")
    print(f"  new document library  {LIBRARY}")
    print("  (* = required)")
    print("Holidays:", ", ".join(f"{d:%b %-d %Y}" for _, d in HOLIDAYS))


def check(ops, out):
    problems = []
    have = ops.lists()
    for spec in LISTS:
        if spec["displayName"].lower() in have:
            problems.append(f"list already exists: {spec['displayName']}")
    if PROJECTS_LIST.lower() not in have:
        problems.append(f"no {PROJECTS_LIST} list on {ops.url}")
    else:
        names = ops.column_names(have[PROJECTS_LIST.lower()])
        if CHARGEABLE["name"].lower() in names or CHARGEABLE["displayName"].lower() in names:
            problems.append(f"{PROJECTS_LIST} already has a {CHARGEABLE['displayName']} column")
    if LIBRARY.lower() in out.lists():
        problems.append(f"library already exists: {LIBRARY}")
    return problems, have


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client")
    ap.add_argument("lists_site", help="site with Chargeable Projects, e.g. https://contoso.sharepoint.com/sites/Operations")
    ap.add_argument("output_site", help="site for the output library, e.g. https://contoso.sharepoint.com/sites/AccountingPayroll")
    ap.add_argument("--apply", action="store_true", help="actually create everything (default: dry run)")
    args = ap.parse_args()

    plan(args.lists_site, args.output_site)
    tenant = urlparse(args.lists_site).netloc.split(".")[0] + ".onmicrosoft.com"
    scope, what = (WRITE_SCOPE, "SharePoint (create lists)") if args.apply else (GRAPH_SCOPE, "SharePoint (read only)")
    token = sign_in(GRAPH_CLIENT, tenant, scope, what)
    ops, out = Site(token, args.lists_site), Site(token, args.output_site)
    problems, have = check(ops, out)
    if problems:
        sys.exit("Stopped, nothing changed:\n  " + "\n  ".join(problems))
    print("\nChecked: none of these exist yet, and Chargeable Projects is there.")
    if not args.apply:
        print("Dry run: nothing changed. Add --apply to create them.")
        return

    log = []
    logfile = ROOT / "unpacked" / args.client / f"create-time-lists-{datetime.now():%Y%m%d-%H%M%S}.json"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        ids = dict(have)
        for spec in LISTS:
            lst = ops.call("POST", "/lists", {"displayName": spec["displayName"], "columns": spec["columns"],
                                             "list": {"template": "genericList"}})
            lid = lst["id"]
            ids[spec["displayName"].lower()] = lid
            log.append({"created_list": spec["displayName"], "id": lid})
            title = next(c for c in ops.call("GET", f"/lists/{lid}/columns")["value"] if c["name"] == "Title")
            ops.call("PATCH", f"/lists/{lid}/columns/{title['id']}", {"displayName": spec["title"], "required": False})
            for name, display, target, required in spec["lookups"]:
                ops.call("POST", f"/lists/{lid}/columns", {"name": name, "displayName": display, "required": required,
                                                         "lookup": {"listId": ids[target.lower()], "columnName": "Title"}})
            for it in ITEMS.get(spec["displayName"], []):
                ops.call("POST", f"/lists/{lid}/items", {"fields": it})
            print(f"  created {spec['displayName']}" + (f" ({len(ITEMS[spec['displayName']])} rows)" if spec["displayName"] in ITEMS else ""))
        ops.call("POST", f"/lists/{ids[PROJECTS_LIST.lower()]}/columns", CHARGEABLE)
        log.append({"added_column": f"{PROJECTS_LIST}.{CHARGEABLE['name']}"})
        print(f"  added the {CHARGEABLE['displayName']} column to {PROJECTS_LIST}")
        lib = out.call("POST", "/lists", {"displayName": LIBRARY, "list": {"template": "documentLibrary"}})
        log.append({"created_library": LIBRARY, "id": lib["id"]})
        print(f"  created the library {LIBRARY}")
        print("Done.")
    finally:
        logfile.write_text(json.dumps(log, indent=2))
        print(f"Log: {logfile}")


if __name__ == "__main__":
    main()
