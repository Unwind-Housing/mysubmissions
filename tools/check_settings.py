"""Check an organization's settings file: that it is well formed, and that it says what the app and flows in the repo do.

Usage (from the repo root, .venv active):
    python3 tools/check_settings.py            all clients that have a settings.json
    python3 tools/check_settings.py eht        just this one
    python3 tools/check_settings.py eht --format   the file on its own, for a new organization with no app in the repo yet

Reads clients/<client>/settings.json (what a person fills in) and settings.lock.json (IDs the tools looked up), then
compares them with clients/<client>/app and clients/<client>/flows. Nothing is changed and nothing is contacted.
The format is described in docs/SETTINGS.md.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EMAIL = re.compile(r"^[^@\s;]+@[^@\s;]+\.[A-Za-z]{2,}$")
GUID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
CORE_LISTS = ("submissions", "projects", "costAccounts")
TIME_LISTS = ("timecards", "timeEntries", "payrollStaff", "holidays", "mileageRates")
CHOICES = {
    ("approvers", "source"): ("groups", "list"),
    ("features", "income"): ("income", "donation", "off"),
    ("features", "grants"): ("off", "expense", "expenseAndIncome"),
    ("features", "approvalScreens"): ("separate", "combined"),
    ("timecards", "reimbursements"): ("paycheck", "weekly"),
    ("weekly", "fileLinks"): ("file", "folder"),
}
FLAGS = [("features", k) for k in ("bankDeposit", "addProjectLink", "confirmationEmail", "timecards")] + \
        [("weekly", "runFromApp"), ("weekly", "testMode")]
REQUIRED = {
    "org": ("name", "short", "appName"),
    "brand": ("headerColor",),
    "sharepoint": ("listsSite", "lists"),
    "approvers": ("source",),
    "features": ("bankDeposit", "income", "grants", "approvalScreens", "addProjectLink", "confirmationEmail", "timecards"),
    "weekly": ("library", "template", "subject", "recipients", "bookkeepers", "financeDirector", "runFromApp", "testMode"),
}


def hex_to_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def format_problems(s):
    """Problems with the settings file on its own."""
    out = []
    for section, keys in REQUIRED.items():
        if not isinstance(s.get(section), dict):
            out.append(f"missing section: {section}")
            continue
        out += [f"missing: {section}.{k}" for k in keys if k not in s[section]]
    if out:
        return out
    for (section, key), allowed in CHOICES.items():
        if section in s and key in s[section] and s[section][key] not in allowed:
            out.append(f"{section}.{key} is {s[section][key]!r}; it has to be one of: {', '.join(allowed)}")
    for section, key in FLAGS:
        if not isinstance(s[section].get(key), bool):
            out.append(f"{section}.{key} has to be true or false")
    if not isinstance(s["approvers"].get("approveOwnIncome", False), bool):
        out.append("approvers.approveOwnIncome has to be true or false")
    if "title" in s["org"] and not (isinstance(s["org"]["title"], str) and s["org"]["title"].strip()):
        out.append("org.title has to be text, or left out for \"My Submissions\"")
    days = s["weekly"].get("reminderDaysAhead", 14)
    if not (isinstance(days, int) and 1 <= days <= 90):
        out.append("weekly.reminderDaysAhead has to be a whole number of days, 1 to 90 (left out: 14)")
    for key in ("headerColor", "headerTextColor"):
        if (key in s["brand"] or key == "headerColor") and not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(s["brand"].get(key))):
            out.append(f"brand.{key} has to look like #3860B2")
    out += [f"sharepoint.lists.{k} is missing" for k in CORE_LISTS if not s["sharepoint"]["lists"].get(k)]
    for site in ("listsSite", "outputSite"):
        url = s["sharepoint"].get(site)
        if url is not None and not re.fullmatch(r"https://[a-z0-9-]+\.sharepoint\.com/sites/[^/\s]+", url):
            out.append(f"sharepoint.{site} has to look like https://name.sharepoint.com/sites/Site")
    mail_lists = [("weekly", k) for k in ("recipients", "bookkeepers", "financeDirector")]
    if s["features"]["timecards"]:
        t = s.get("timecards")
        if not isinstance(t, dict):
            return out + ["features.timecards is true but there is no timecards section"]
        out += [f"missing: timecards.{k}" for k in ("firstMonth", "reimbursements", "library", "template", "filePrefix", "recipients",
                                                    "bookkeepers", "testMode") if k not in t]
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(t.get("firstMonth", ""))):
            out.append("timecards.firstMonth has to look like 2026-11")
        if not isinstance(t.get("testMode"), bool):
            out.append("timecards.testMode has to be true or false")
        out += [f"sharepoint.lists.{k} is missing (needed for timecards)" for k in TIME_LISTS if not s["sharepoint"]["lists"].get(k)]
        mail_lists += [("timecards", "recipients"), ("timecards", "bookkeepers")]
    elif "timecards" in s and s["timecards"].get("reimbursements") == "paycheck":
        out.append("reimbursements can only go on the paycheck when features.timecards is true")
    for section, key in mail_lists:
        v = s[section].get(key)
        if not isinstance(v, list) or not v:
            out.append(f"{section}.{key} has to be a list with at least one address")
        else:
            out += [f"{section}.{key}: {a!r} is not an email address" for a in v if not EMAIL.match(str(a))]
    a = s["approvers"]
    if a["source"] == "groups":
        out += [f"approvers.{k} is missing (a group's name or ID)" for k in ("financial", "income") if not a.get(k)]
    elif not a.get("list"):
        out.append("approvers.list is missing (the list of approvers' email addresses)")
    if s["features"]["grants"] != "off" and s["approvers"]["source"] == "list" and not s["sharepoint"]["lists"].get("grants"):
        out.append("sharepoint.lists.grants is missing (needed for the grant picker)")
    return out


def _flow(client, flow_id):
    hit = [p for p in (ROOT / "clients" / client / "flows").glob("*__*.json") if json.loads(p.read_text())["name"] == flow_id]
    return json.loads(hit[0].read_text())["properties"] if hit else None


def _variables(definition):
    out = {}
    for a in definition["actions"].values():
        if a.get("type") == "InitializeVariable":
            v = a["inputs"]["variables"][0]
            out[v["name"]] = v.get("value")
    return out


def _addresses(text):
    return {a.strip().lower() for a in str(text or "").split(";") if a.strip()}


def repo_problems(client, s, lock):
    """Where the settings disagree with the app and flows in the repo."""
    out = []
    base = ROOT / "clients" / client
    sources = {d["Name"]: d for d in json.loads((base / "app/References/DataSources.json").read_text(encoding="utf-8-sig"))["DataSources"]}
    src = {p.name[:-len(".pa.yaml")]: p.read_text(encoding="utf-8") for p in (base / "app/Src").glob("*.pa.yaml")}
    everything = "\n".join(src.values())

    # brand
    r, g, b = hex_to_rgb(s["brand"]["headerColor"])
    if not re.search(rf"Fill: =RGBA\({r}, ?{g}, ?{b}, ?1\)", everything):
        out.append(f"brand.headerColor {s['brand']['headerColor']} is not a fill colour anywhere in the app")
    logo = s["brand"].get("logo")
    if logo and not (base / logo).exists():
        out.append(f"brand.logo: no file {logo}")
    title = s["org"].get("title") or "My Submissions"
    if f'Text: ="{title}"' not in src.get("LandingPage", ""):
        out.append(f"org.title: the home page does not show {title!r}")

    # lists
    for key, name in s["sharepoint"]["lists"].items():
        d = sources.get(name)
        if not d:
            out.append(f"sharepoint.lists.{key}: the app has no data source named {name!r}")
            continue
        if d.get("DatasetName") != s["sharepoint"]["listsSite"]:
            out.append(f"sharepoint.lists.{key}: {name!r} is on {d.get('DatasetName')}, not sharepoint.listsSite")
        if lock.get("lists", {}).get(key) != d.get("TableName"):
            out.append(f"lock lists.{key} is {lock.get('lists', {}).get(key)}; the app uses {d.get('TableName')}")

    # approvers
    a = s["approvers"]
    if a["source"] == "groups":
        for key in ("financial", "income"):
            gid = lock.get("groups", {}).get(key)
            if not gid or not GUID.match(gid):
                out.append(f"lock groups.{key} is missing")
            elif f'ListGroupMembers("{gid}")' not in everything:
                out.append(f"lock groups.{key} ({gid}) is not checked anywhere in the app")
            if GUID.match(str(a.get(key))) and a[key] != gid:
                out.append(f"approvers.{key} and lock groups.{key} are different IDs")
    elif a["list"] not in sources:
        out.append(f"approvers.list: the app has no data source named {a['list']!r}")

    # features
    f = s["features"]
    forms = {k: v for k, v in src.items() if k.startswith("Create") and "TimeSheet" not in k}
    facts = {
        "timecards": ("CreateTimeSheet" in src and "Timecards" in sources, f["timecards"]),
        "income": ("donation" if any("Donation" in k for k in forms) else "income", f["income"]),
        "grants": ({0: "off", 1: "expense"}.get(sum("Grant" in v for v in forms.values()), "expenseAndIncome"), f["grants"]),
        "addProjectLink": ("Add a project" in everything, f["addProjectLink"]),
    }
    out += [f"features.{k} is {want!r}, but the app looks like {have!r}" for k, (have, want) in facts.items() if have != want]

    # weekly flow
    w = s["weekly"]
    props = _flow(client, lock.get("flows", {}).get("weekly"))
    if not props:
        return out + ["lock flows.weekly does not match a flow in clients/<client>/flows"]
    d = props["definition"]
    v = _variables(d)
    text = json.dumps(d)
    to = d["actions"]["Send_an_email_(V2)"]["inputs"]["parameters"]
    bookkeepers = _addresses(v.get("Bookkeepers", v.get("Accountants")))
    recipients = _addresses(v.get("Recipients")) or (bookkeepers | _addresses(v.get("EmailAlso")))
    checks = [
        ("recipients", {x.lower() for x in w["recipients"]}, recipients),
        ("bookkeepers", {x.lower() for x in w["bookkeepers"]}, bookkeepers),
        ("financeDirector", {x.lower() for x in w["financeDirector"]}, _addresses(to["emailMessage/To"].split("@{")[0])),
        ("testMode", w["testMode"], v.get("TestMode")),
        ("runFromApp", w["runFromApp"], next(iter(d["triggers"].values())).get("kind") == "PowerAppV2"),
        ("library", True, f"\"folderPath\": \"/{w['library']}/@{{variables('FolderName')}}" in text),
        ("template", True, w["template"].replace(" ", "%2b") in text or w["template"] in text),
        ("subject", True, w["subject"] in to["emailMessage/Subject"]),
    ]
    if "ReminderDaysAhead" in v:
        checks.append(("reminderDaysAhead", int(w.get("reminderDaysAhead", 14)), v["ReminderDaysAhead"]))
        if not lock.get("lists", {}).get("reminders"):
            out.append("lock lists.reminders is missing (the weekly flow reads payment reminders from it)")
    elif "reminderDaysAhead" in w:
        out.append("weekly.reminderDaysAhead is set, but the weekly flow has no payment reminders step")
    add_link = d["actions"]["Pull_out_attachments_and_prepare"]["actions"]["Apply_to_each_2"]["actions"]["Add_file_link"]["inputs"]["value"]
    checks.append(("fileLinks", w.get("fileLinks", "file"), "file" if "embed.aspx?UniqueId=" in add_link else "folder"))
    if w.get("replyTo"):
        checks.append(("replyTo", w["replyTo"].lower(), str(to.get("emailMessage/ReplyTo", "")).lower()))
    out += [f"weekly.{k}: settings say {want!r}, the weekly flow has {have!r}" for k, want, have in checks if want != have]
    if w["runFromApp"] and not any(x.get("Type") == "ServiceInfo" and x.get("FlowNameId") == lock["flows"]["weekly"] for x in sources.values()):
        out.append("weekly.runFromApp is true, but the weekly flow is not added to the app")
    confirm = _flow(client, lock.get("flows", {}).get("confirmationEmail", ""))
    if f["confirmationEmail"] != bool(confirm and confirm.get("state") == "Started"):
        out.append(f"features.confirmationEmail is {f['confirmationEmail']}, which is not what the confirmation flow's state says")
    approved = lock.get("columns", {}).get("approved")
    if not approved or f"{approved} eq '1'" not in text:
        out.append(f"lock columns.approved ({approved}) is not the column the weekly flow filters on")

    # timecards
    if f["timecards"]:
        t = s["timecards"]
        skips = "Typeofthing ne 'Reimbursement'" in text
        if (t["reimbursements"] == "paycheck") != skips:
            out.append(f"timecards.reimbursements is {t['reimbursements']!r}, but the weekly flow {'skips' if skips else 'does not skip'} reimbursements")
        if f"TCStart = Date({int(t['firstMonth'][:4])}, {int(t['firstMonth'][5:])}, 1);" not in src["App"]:
            out.append("timecards.firstMonth is not the app's TCStart")
        for key, formula in (("approver", "TCApprover"), ("approverApprover", "TCApproverApprover")):
            m = re.search(rf'^\s*{formula} = "([^"]*)";', src["App"], re.M)
            have, want = (m.group(1) if m else None), t.get(key, "")
            if have is None or have.lower() != lock.get("groups", {}).get(want, want).lower():
                out.append(f"timecards.{key} is {want!r}, but the app's {formula} is {have!r}")
        for key in ("outputSite", "timecardsDrive"):
            if not lock.get("graph", {}).get(key):
                out.append(f"lock graph.{key} is missing (the timecard flow needs it)")
        if not lock.get("libraries", {}).get("timecards"):
            out.append("lock libraries.timecards is missing")
    return out


def check(client, format_only=False):
    """Problems with clients/<client>'s settings. format_only: the file on its own, for an organization that has no app in
    the repo yet (a new install)."""
    base = ROOT / "clients" / client
    try:
        s = json.loads((base / "settings.json").read_text())
        lock = json.loads((base / "settings.lock.json").read_text())
    except (OSError, ValueError) as e:
        return [f"{client}: {e}"]
    problems = format_problems(s)
    if not problems and not format_only and not (base / "app").is_dir():
        format_only = True                                      # a new install: no pulled app to compare with, the file on its own
    if not problems and not format_only:
        problems = repo_problems(client, s, lock)
    return [f"{client}: {p}" for p in problems]


def main():
    args = [a for a in sys.argv[1:] if a != "--format"]
    format_only = "--format" in sys.argv[1:]
    clients = args or sorted(p.parent.name for p in (ROOT / "clients").glob("*/settings.json"))
    failed = False
    for c in clients:
        problems = check(c, format_only)
        failed = failed or bool(problems)
        print("\n".join(problems) if problems else f"{c}: OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
