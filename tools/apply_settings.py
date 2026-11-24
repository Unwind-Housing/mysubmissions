"""Stamp an organization's settings into its app and weekly flow in the repo.

Usage (from the repo root, .venv active):
    python3 tools/apply_settings.py eht            dry run: what would change
    python3 tools/apply_settings.py eht --write    change the files under clients/eht

This is how a setting is changed: edit clients/<client>/settings.json, run this with --write, then send the result out
the usual way (tools/push_flows.py for a flow, tools/build_package.py for the app, and rebuild the package after any
flow change). It only edits files in the repo. It never signs in and never touches a tenant.

What it stamps today:
  weekly flow   test mode, recipients, bookkeepers, the finance director's copy address, reply-to;
                links in the spreadsheet that open the file itself; and, when weekly.runFromApp is true, the trigger
                and the reply that let the app's Send to bookkeeper button start it
  app           timecards only: first month, the approver and the approver's approver
The Process Timecards flow is not stamped here: tools/build_timecard_flow.py builds it whole from the same settings.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings as st  # noqa: E402


FILE_LINK = "@concat('{site}/_layouts/15/embed.aspx?UniqueId=', toLower(substring(outputs('Create_file')?['body/ETag'], 2, 36)))"
ITEMS = "length(body('Get_all_items_that_are_ready_for_processing')?['value'])"
DEFAULT_REMINDER_DAYS = 14
APP_TRIGGER_INPUTS = {"schema": {"type": "object", "required": ["text"], "properties": {"text": {
    "title": "Date", "type": "string", "x-ms-dynamically-added": True,
    "description": "The date for the folder name and the Processed Date column, as yyyy-mm-dd", "x-ms-content-hint": "TEXT"}}}}
APP_REPLY = {
    "runAfter": {"Get_all_items_that_are_ready_for_processing": ["Succeeded"]}, "type": "Response", "kind": "PowerApp",
    "inputs": {"statusCode": 200,
               "body": {"result": "@{concat(if(variables('TestMode'), 'TEST RUN. ', ''), 'Sending ', string(" + ITEMS + "), if(equals(" + ITEMS
                                  + ", 1), ' item', ' items'), ' to the bookkeepers. The email arrives in a few minutes.')}"},
               "schema": {"type": "object", "properties": {"result": {"title": "Result", "x-ms-dynamically-added": True, "type": "string"}}}}}


def run_from_app_changes(definition):
    """Give the weekly flow a Power Apps trigger with one input, Date, and a reply sent as soon as the items are read
    (a run takes longer than an app will wait). Returns what changed; nothing if it is already set up."""
    changes = []
    trigger = definition["triggers"]["manual"]
    if trigger.get("kind") != "PowerAppV2":
        text = json.dumps(definition["actions"])
        if "['key-button-date']" not in text:
            raise ValueError("the weekly flow's trigger is not the usual button with a Date box")
        definition["actions"] = json.loads(text.replace("['key-button-date']", "['text']"))
        trigger["kind"], trigger["inputs"] = "PowerAppV2", json.loads(json.dumps(APP_TRIGGER_INPUTS))
        changes.append("weekly flow trigger: the button with a Date box -> started from the app, with the date as its input")
    if "Reply_to_the_app" not in definition["actions"]:
        definition["actions"]["Reply_to_the_app"] = json.loads(json.dumps(APP_REPLY))
        changes.append("weekly flow: new step Reply to the app")
    return changes


def weekly_changes(definition, s):
    """Set the weekly flow's settings in place. Returns a list of what changed."""
    w = s["weekly"]
    variables = {}
    for a in definition["actions"].values():
        if a.get("type") == "InitializeVariable":
            v = a["inputs"]["variables"][0]
            variables[v["name"]] = v
    want = {"TestMode": w["testMode"]}
    if "Recipients" in variables:
        want.update(Recipients=";".join(w["recipients"]), Bookkeepers=";".join(w["bookkeepers"]))
    else:  # the other naming: the bookkeepers, and whoever else gets the email
        want.update(Accountants=";".join(w["bookkeepers"]), EmailAlso=";".join(a for a in w["recipients"] if a not in w["bookkeepers"]))
    if "ReminderDaysAhead" in variables:                                  # payment reminders (tools/weekly_reminders.py)
        want["ReminderDaysAhead"] = int(w.get("reminderDaysAhead", DEFAULT_REMINDER_DAYS))
    changes = []
    for name, value in want.items():
        if name not in variables:
            raise ValueError(f"the weekly flow has no variable named {name}")
        if variables[name].get("value") != value:
            changes.append(f"weekly flow {name}: {variables[name].get('value')!r} -> {value!r}")
            variables[name]["value"] = value
    mail = definition["actions"]["Send_an_email_(V2)"]["inputs"]["parameters"]
    old_to, rest = mail["emailMessage/To"].split("@{", 1)
    new_to = ";".join(w["financeDirector"])
    if old_to != new_to:
        changes.append(f"weekly flow email To: {old_to!r} -> {new_to!r}")
        mail["emailMessage/To"] = new_to + "@{" + rest
    link = FILE_LINK.format(site=s["sharepoint"].get("outputSite", s["sharepoint"]["listsSite"]))
    add_link = definition["actions"]["Pull_out_attachments_and_prepare"]["actions"]["Apply_to_each_2"]["actions"]["Add_file_link"]["inputs"]
    if w.get("fileLinks", "file") == "file" and add_link["value"] != link:
        changes.append("weekly flow file links: open the file itself, not its folder")
        add_link["value"] = link
    if w["runFromApp"]:
        changes += run_from_app_changes(definition)
    reply = w.get("replyTo", w["financeDirector"][0])
    if mail.get("emailMessage/ReplyTo") != reply:
        changes.append(f"weekly flow Reply-To: {mail.get('emailMessage/ReplyTo')!r} -> {reply!r}")
        mail["emailMessage/ReplyTo"] = reply
    return changes


def app_formulas(s, lock):
    """The app formulas that come from settings, as {name: the text after "name = "}."""
    if not s["features"]["timecards"]:
        return {}
    t = s["timecards"]
    year, month = int(t["firstMonth"][:4]), int(t["firstMonth"][5:])
    return {
        "TCStart": f"Date({year}, {month}, 1)",
        "TCApprover": json.dumps(st.group_or_person(t.get("approver"), s, lock)),
        "TCApproverApprover": json.dumps(st.group_or_person(t.get("approverApprover"), s, lock)),
    }


def formula_changes(text, formulas):
    """Replace `Name = ...;` lines in the app's Formulas text. Returns (new text, changes)."""
    lines, changes, seen = text.split("\n"), [], set()
    for i, line in enumerate(lines):
        body = line.lstrip().lstrip("=")
        for name, value in formulas.items():
            if body.startswith(f"{name} = ") and body.rstrip().endswith(";"):
                seen.add(name)
                new = f"{name} = {value};"
                if body.strip() != new:
                    changes.append(f"app {name}: {body.strip()[len(name) + 3:-1]} -> {value}")
                    lines[i] = line[:len(line) - len(body)] + new
    missing = set(formulas) - seen
    if missing:
        raise ValueError(f"the app has no formula named {', '.join(sorted(missing))}")
    return "\n".join(lines), changes


def apply(client, write=False):
    """Stamp settings into clients/<client>. Returns the list of changes (made, or that would be made)."""
    s, lock = st.load(client)
    base = st.client_dir(client)
    changes = []

    flow_path = st.flow_file(client, lock["flows"]["weekly"])
    if not flow_path:
        raise ValueError(f"{client}: the weekly flow in settings.lock.json is not in clients/{client}/flows")
    doc = json.loads(flow_path.read_text())
    flow_changes = weekly_changes(doc["properties"]["definition"], s)
    changes += flow_changes
    if write and flow_changes:
        flow_path.write_text(json.dumps(doc, indent=2))

    formulas = app_formulas(s, lock)
    if formulas:
        # The formulas are stored twice: in the app's control file (what Power Apps loads) and the readable copy.
        json_path = next(p for p in sorted((base / "app/Controls").glob("*.json"))
                         if json.loads(p.read_text(encoding="utf-8-sig"))["TopParent"]["Name"] == "App")
        app = json.loads(json_path.read_text(encoding="utf-8-sig"))
        rule = next(r for r in app["TopParent"]["Rules"] if r["Property"] == "Formulas")
        rule["InvariantScript"], app_changes = formula_changes(rule["InvariantScript"], formulas)
        yaml_path = base / "app/Src/App.pa.yaml"
        new_yaml, yaml_changes = formula_changes(yaml_path.read_bytes().decode("utf-8"), formulas)
        if app_changes != yaml_changes:
            raise ValueError(f"{client}: the app's two copies of its formulas differ; run tools/check_app.py")
        changes += app_changes
        if write and app_changes:
            # Same layout Power Apps exports: two-space indent, ASCII escapes, Windows line ends.
            json_path.write_bytes(json.dumps(app, indent=2, ensure_ascii=True).replace("\n", "\r\n").encode("utf-8"))
            yaml_path.write_bytes(new_yaml.encode("utf-8"))
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client")
    ap.add_argument("--write", action="store_true", help="change the files (default: only list what would change)")
    args = ap.parse_args()
    changes = apply(args.client, write=args.write)
    if not changes:
        print(f"{args.client}: the app and weekly flow already match settings.json. Nothing to change.")
        return
    print(f"{args.client}: {'changed' if args.write else 'would change'}:")
    for c in changes:
        print(f"  {c}")
    if not args.write:
        print("Dry run: nothing written. Add --write to change the files.")
    else:
        print("Next: tools/check_app.py and tests, then tools/push_flows.py for the flow and tools/build_package.py for the app.\n"
              "Rebuild the package after any flow change, before anyone imports it.")


if __name__ == "__main__":
    main()
