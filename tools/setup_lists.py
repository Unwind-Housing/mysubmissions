"""Create the core SharePoint lists an organization's app and weekly flow need, and record their IDs.

Usage (from the repo root, .venv active):
    python3 tools/setup_lists.py <client or settings folder>                    dry run: what exists, what would be made
    python3 tools/setup_lists.py <client or settings folder> --apply            make what is missing
    python3 tools/setup_lists.py <client or settings folder> --apply --sample   and add a few sample projects and accounts
    python3 tools/setup_lists.py <client or settings folder> --apply --weekly   and the library the weekly flow writes to
    python3 tools/setup_lists.py eht template/test-install --apply            several at once, one sign-in per tenant

It reads sharepoint.listsSite and sharepoint.lists from settings.json, creates Chargeable Projects, Chargeable Cost
Accounts, Financial Submissions and Payment Reminders (under whatever names the settings give them) with the columns
the template app and weekly flow expect, and writes the lists' IDs into settings.lock.json. It is safe to run again: it only adds a list or a column
that is missing, and never changes or removes one that is there.

--weekly also makes the document library named in weekly.library on the output site, puts an empty workbook template in
it (weekly.template), and records the IDs the weekly flow needs (tools/build_weekly_flow.py).

The columns' internal names matter. The template app's description of each list uses them, so they are created with
exactly the names below, and the tool stops if SharePoint ends up with different ones.
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings as st  # noqa: E402

CLASSES = ["Income", "Operations Expense", "Capital Project Expense"]
ONE_LINE = {"text": {"allowMultipleLines": False, "maxLength": 255}}
DATE = {"dateTime": {"format": "dateOnly"}}
# A Yes/No column made this way has no default: SharePoint ignores one sent through Microsoft Graph, at creation and
# afterwards (tested 2026-11-16). So nothing may rely on it. The app saves Processed = No itself on every new submission,
# and rows this tool adds say Retired = No outright. To set a default by hand: List settings > the column > Default value.
YES_NO = {"boolean": {}}


def choice(*values):
    return {"choice": {"choices": list(values), "displayAs": "dropDownMenu", "allowTextEntry": False}}


def col(name, display, kind, required=False):
    return {"name": name, "displayName": display, "required": required, **kind}


# key in sharepoint.lists -> the list. "title" renames the first column. A lookup is (name, display name, key of the
# list it points at, column shown). Order matters: a list is made after the lists it looks up.
SPECS = {
    "projects": {
        "default": "Chargeable Projects", "title": "Project Name", "title_required": False,
        "columns": [col("Project_x0020_Class", "Project Class", choice(*CLASSES)),
                    col("Retired", "Retired", YES_NO)],
        "lookups": [],
    },
    "costAccounts": {
        "default": "Chargeable Cost Accounts", "title": "Cost Account Description", "title_required": False,
        "columns": [col("Cost_x0020_Account_x0020_Number", "Cost Account Number", {"number": {"decimalPlaces": "none"}}),
                    col("Project_x0020_Class", "Project Class", choice(*CLASSES)),
                    col("Cost_x0020_Account_x0020_Number_", "Cost Account Number and Description",
                        {"calculated": {"formula": '=[Cost Account Number]&" - "&[Cost Account Description]', "outputType": "text"}})],
        "lookups": [],
    },
    "submissions": {
        "default": "Financial Submissions", "title": "Vendor", "title_required": True,
        "columns": [col("Type_x0020_of_x0020_thing", "Type of thing", choice("Invoice", "Deposit", "Receipt", "Reimbursement", "Income"), True),
                    col("Amount", "Amount", {"currency": {"locale": "en-us"}}),
                    col("Approved", "Approved", YES_NO),
                    col("Donation_x0020_Date", "Income Date", DATE),
                    col("Donation_x0020_Type", "Income Type", choice("Paper Check", "EFT")),
                    col("Processed", "Processed", YES_NO),
                    col("ProcessedDate", "ProcessedDate", DATE),
                    col("Invoice_x0020_Date", "Invoice Date", DATE),
                    col("Invoice_x0020_Number", "Invoice Number", ONE_LINE),
                    col("PreparedDate", "PreparedDate", DATE),
                    col("Comments", "Comments", {"text": {"allowMultipleLines": True, "linesForEditing": 6, "textType": "plain"}}),
                    col("ExtractMe", "ExtractMe", YES_NO),
                    col("DonationRecorded", "DonationRecorded", YES_NO)],
        "lookups": [("Cost_x0020_Account", "Cost Account", "costAccounts", "Cost_x0020_Account_x0020_Number_"),
                    ("Project", "Project", "projects", "Title")],
    },
    # Payment reminders the weekly email carries (D-16): one row per card or bill, due on the same day each month.
    "reminders": {
        "default": "Payment Reminders", "title": "What", "title_required": True,
        "columns": [col("DueDay", "Due day of the month", {"number": {"decimalPlaces": "none", "minimum": 1, "maximum": 28}}, True),
                    col("Link", "Link", ONE_LINE),
                    col("Note", "Note", {"text": {"allowMultipleLines": True, "linesForEditing": 3, "textType": "plain"}}),
                    col("Active", "Active", YES_NO)],
        "lookups": [],
    },
}
SAMPLE = {
    "projects": [{"Title": "Operations", "Project_x0020_Class": "Operations Expense", "Retired": False},
                 {"Title": "Income", "Project_x0020_Class": "Income", "Retired": False},
                 {"Title": "12 Sample Street", "Project_x0020_Class": "Capital Project Expense", "Retired": False}],
    "costAccounts": [{"Title": "Office supplies", "Cost_x0020_Account_x0020_Number": 8100, "Project_x0020_Class": "Operations Expense"},
                     {"Title": "Professional fees", "Cost_x0020_Account_x0020_Number": 8200, "Project_x0020_Class": "Operations Expense"},
                     {"Title": "Donations", "Cost_x0020_Account_x0020_Number": 5100, "Project_x0020_Class": "Income"},
                     {"Title": "Grants", "Cost_x0020_Account_x0020_Number": 5200, "Project_x0020_Class": "Income"},
                     {"Title": "Construction", "Cost_x0020_Account_x0020_Number": 9100, "Project_x0020_Class": "Capital Project Expense"},
                     {"Title": "Permits and fees", "Cost_x0020_Account_x0020_Number": 9200, "Project_x0020_Class": "Capital Project Expense"}],
    "reminders": [{"Title": "Sample credit card", "DueDay": 5, "Link": "https://example.com/pay", "Note": "A sample row: change or delete it.",
                   "Active": True}],
}


def tenant_id(token):
    """The tenant's ID, from the signed-in token: a Power Platform Default environment is named Default-<tenant ID>."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["tid"]


def expected_names(key):
    spec = SPECS[key]
    return ["Title"] + [c["name"] for c in spec["columns"]] + [l[0] for l in spec["lookups"]]


def ensure(site, names, apply, say=print):
    """Make whatever is missing. `site` has lists() -> {lower-case name: id} and call(method, path, body).
    `names` is sharepoint.lists from the settings. Returns ({key: list id}, problems). With apply=False nothing is
    changed, and lists that do not exist yet are left out of the result."""
    have = site.lists()
    ids, problems = {}, []
    for key, spec in SPECS.items():
        name = names.get(key) or spec["default"]
        lid = have.get(name.lower())
        if not lid:
            say(f"  {name}: {'creating' if apply else 'would be created'}, {len(spec['columns']) + len(spec['lookups']) + 1} columns")
            if not apply:
                continue
            lid = site.call("POST", "/lists", {"displayName": name, "list": {"template": "genericList"}})["id"]
        else:
            say(f"  {name}: already there")
        ids[key] = lid
        columns = site.call("GET", f"/lists/{lid}/columns")["value"]
        present = {c["name"] for c in columns}
        title = next(c for c in columns if c["name"] == "Title")
        if title.get("displayName") != spec["title"]:
            say(f"    first column: {'renaming to' if apply else 'would be renamed to'} \"{spec['title']}\"")
            if apply:
                site.call("PATCH", f"/lists/{lid}/columns/{title['id']}", {"displayName": spec["title"], "required": spec["title_required"]})
        todo = [c for c in spec["columns"] if c["name"] not in present]
        for c in todo:
            say(f"    column \"{c['displayName']}\": {'adding' if apply else 'would be added'}")
            if apply:
                site.call("POST", f"/lists/{lid}/columns", c)
        for cname, display, target, shown in spec["lookups"]:
            if cname in present:
                continue
            say(f"    column \"{display}\" (looks up {names.get(target) or SPECS[target]['default']}): {'adding' if apply else 'would be added'}")
            if not apply:
                continue
            if target not in ids:
                problems.append(f"{name}.{display}: the list it looks up does not exist")
                continue
            body = {"name": cname, "displayName": display, "lookup": {"listId": ids[target], "columnName": shown}}
            try:
                site.call("POST", f"/lists/{lid}/columns", body)
            except RuntimeError as e:
                if shown == "Title":
                    raise
                say(f"      SharePoint would not show \"{shown}\" in the lookup ({str(e)[:120]}); using the first column instead")
                body["lookup"]["columnName"] = "Title"
                site.call("POST", f"/lists/{lid}/columns", body)
        if apply:
            after = {c["name"] for c in site.call("GET", f"/lists/{lid}/columns")["value"]}
            missing = [n for n in expected_names(key) if n not in after]
            if missing:
                problems.append(f"{name}: SharePoint did not keep these column names: {', '.join(missing)}. "
                                f"It has: {', '.join(sorted(n for n in after if n not in present))}")
    return ids, problems


WEEKLY_TABLE = "WeeklySubmissions"
WEEKLY_COLUMNS = ["ItemNo", "Type", "Vendor", "Project", "CostAccount", "Comment", "Amount", "ProcessedDate", "LinkToThing", "FileCount",
                  "File1URL", "File2URL", "File3URL", "FolderURL"]


def weekly_template():
    """The empty workbook the weekly flow copies each week: one table, one column per value the flow writes."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    wb = Workbook()
    ws = wb.active
    ws.title = "Submissions"
    ws.append(WEEKLY_COLUMNS)
    ws.append([None] * len(WEEKLY_COLUMNS))
    table = Table(displayName=WEEKLY_TABLE, ref=f"A1:{get_column_letter(len(WEEKLY_COLUMNS))}2")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    for i, name in enumerate(WEEKLY_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = 40 if "URL" in name or name in ("Vendor", "Comment", "LinkToThing") else 16
        ws.cell(row=1, column=i).font = Font(bold=True)
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def ensure_weekly_library(token, s, say=print):
    """Make the weekly library and its template if they are missing. Returns what goes in the lock file."""
    import requests
    from urllib.parse import quote
    from create_time_lists import Site
    from inspect_tenant import GRAPH
    w, sp = s["weekly"], s["sharepoint"]
    site = Site(token, sp.get("outputSite", sp["listsSite"]))
    lid = site.lists().get(w["library"].lower())
    if lid:
        say(f"  {w['library']}: already there")
    else:
        lid = site.call("POST", "/lists", {"displayName": w["library"], "list": {"template": "documentLibrary"}})["id"]
        say(f"  {w['library']}: library created")
    drive = site.call("GET", f"/lists/{lid}/drive")["id"]
    http = requests.Session()
    http.headers["Authorization"] = f"Bearer {token}"
    path = f"{GRAPH}/drives/{drive}/root:/{quote(w['template'])}"
    if http.get(path).ok:
        say(f"  {w['template']}: already there")
    else:
        r = http.put(f"{path}:/content", data=weekly_template(),
                     headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
        if not r.ok:
            raise RuntimeError(f"uploading the template failed (HTTP {r.status_code}): {r.text[:300]}")
        say(f"  {w['template']}: template uploaded")
    return {"libraries": {"weekly": lid}, "graph": {"weeklyExcelSource": f"sites/{site.id}", "weeklyDrive": drive}}


class _Site:
    """A SharePoint site through Microsoft Graph."""

    def __init__(self, token, url):
        from create_time_lists import Site
        self._s = Site(token, url)
        self.url = url

    def lists(self):
        return self._s.lists()

    def call(self, method, path, body=None):
        return self._s.call(method, path, body)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("where", nargs="+", help="a client's short name, or a folder that holds settings.json (several allowed)")
    ap.add_argument("--apply", action="store_true", help="make what is missing (default: dry run)")
    ap.add_argument("--sample", action="store_true", help="add a few sample projects and cost accounts to lists this run creates")
    ap.add_argument("--weekly", action="store_true", help="with --apply: also make the weekly flow's library and workbook template")
    args = ap.parse_args()
    from create_time_lists import WRITE_SCOPE
    from inspect_tenant import GRAPH_CLIENT, GRAPH_SCOPE, sign_in
    tokens = {}                                                           # one sign-in per tenant
    for where in args.where:
        base = Path(where) if Path(where).is_dir() else st.client_dir(where)
        s, lock = st.load_dir(base)
        url = s["sharepoint"]["listsSite"]
        names = s["sharepoint"].get("lists", {})
        print(f"Site: {url}")
        tenant = urlparse(url).netloc.split(".")[0] + ".onmicrosoft.com"
        if tenant not in tokens:
            scope, what = (WRITE_SCOPE, "SharePoint (create lists)") if args.apply else (GRAPH_SCOPE, "SharePoint (read only)")
            tokens[tenant] = sign_in(GRAPH_CLIENT, tenant, scope, what)
        token = tokens[tenant]
        site = _Site(token, url)
        before = site.lists()
        ids, problems = ensure(site, names, args.apply)
        if args.apply and args.sample:
            for key, rows in SAMPLE.items():
                name = (names.get(key) or SPECS[key]["default"]).lower()
                if name in before or key not in ids:
                    continue                                              # only lists this run created
                for row in rows:
                    site.call("POST", f"/lists/{ids[key]}/items", {"fields": row})
                print(f"  {names.get(key) or SPECS[key]['default']}: {len(rows)} sample rows added")
        if problems:
            sys.exit("STOPPED, the lock file was not written:\n  " + "\n  ".join(problems))
        if not args.apply:
            print("Dry run: nothing changed. Add --apply to make what is missing.")
            continue
        lock.setdefault("lists", {}).update(ids)
        if args.weekly:
            for section, values in ensure_weekly_library(token, s).items():
                lock.setdefault(section, {}).update(values)
        if not re.fullmatch(r"Default-[0-9a-f-]{36}", lock.get("environment", "")) or lock["environment"].startswith("Default-00000000"):
            lock["environment"] = f"Default-{tenant_id(token)}"            # the tenant's Default environment, where the flow and app go
            print(f"  environment: {lock['environment']}")
        (base / "settings.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        print(f"List IDs written to {base / 'settings.lock.json'}")


if __name__ == "__main__":
    main()
