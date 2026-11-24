"""Give an organization's weekly flow the payment reminders steps (decision D-16).

Usage (from the repo root, .venv active):
    python3 tools/weekly_reminders.py eht            add the steps to clients/eht's weekly flow in the repo (no sign-in)

The flow reads the Payment Reminders list (sharepoint.lists.reminders, made by tools/setup_lists.py; its ID in the lock
file under lists.reminders): one row per card or bill, with the day of the month it is due, a link and a note. Each run
works out when each active row is next due and, when that is within weekly.reminderDaysAhead days (14 unless set) of the
run's date, adds it to the weekly email under "Payment reminders". Nothing is added when no payment is due soon.

Steps added, all at the top level of the flow:
    Initialize_ReminderDaysAhead   the look-ahead, stamped from settings by tools/apply_settings.py
    Get_payment_reminders          the list's rows
    Reminders_with_due_date        each row with its next due date (this month if the day has not passed, else next month)
    Reminders_due_soon             the active rows due within the look-ahead
    Reminder_lines                 one line of HTML per reminder
The email then waits for Reminder_lines and carries the section before "Thank you!".

And in the week's workbook, one row per reminder after the items: Type "Reminder", the name as Vendor, the due date and
note as Comment, the link as LinkToThing, no number and no amount, so it cannot be taken for a transaction. Rows of that
type do not count towards the numbering, and a second send the same day does not add one twice:
    Existing_reminder_rows         reminder rows already in the workbook
    Existing_reminder_names        their names
    Add_reminder_rows              the loop that adds the missing ones

Running it on a flow that has the steps changes nothing. After it: tools/check_settings.py <client>, tests, and if the
flow is the one the template is made from, tools/build_weekly_flow.py --refresh-from <client> so the template carries the
steps too. Then tools/push_flows.py to the live flow, and rebuild the client's package.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings as st  # noqa: E402

DEFAULT_DAYS = 14
ITEMS = "Get_all_items_that_are_ready_for_processing"
MAIL = "Send_an_email_(V2)"
THANKS = '<p class="editor-paragraph">Thank you!</p>'
TODAY = "triggerBody()['text']"                                           # the run's date, yyyy-mm-dd, from the app
_DUE_DAY = "int(first(split(string(item()?['DueDay']), '.')))"           # SharePoint sends the number as 18.0
_PAD = f"if(less({_DUE_DAY}, 10), concat('0', string({_DUE_DAY})), string({_DUE_DAY}))"
NEXT_DUE = (f"@if(greaterOrEquals({_DUE_DAY}, int(formatDateTime({TODAY}, 'dd'))), "
            f"concat(formatDateTime({TODAY}, 'yyyy-MM-'), {_PAD}), "
            f"concat(formatDateTime(addToTime({TODAY}, 1, 'Month'), 'yyyy-MM-'), {_PAD}))")
DUE_SOON = (f"@and(not(equals(item()?['Active'], false)), "
            f"lessOrEquals(ticks(item()?['Due']), ticks(addDays({TODAY}, variables('ReminderDaysAhead')))))")
_DAYS = f"div(sub(ticks(item()?['Due']), ticks({TODAY})), 864000000000)"
_WHEN = f"if(equals({_DAYS}, 0), 'today', if(equals({_DAYS}, 1), 'tomorrow', concat('in ', string({_DAYS}), ' days')))"
LINE = ("@concat('<li><b>', item()?['Name'], '</b>: due ', formatDateTime(item()?['Due'], 'dddd d MMMM'), ' (', " + _WHEN + ", ')'"
        ", if(empty(item()?['Link']), '', concat('. Pay at <a href=\"', item()?['Link'], '\">', item()?['Link'], '</a>'))"
        ", if(empty(item()?['Note']), '', concat('. ', item()?['Note'])), '</li>')")
SECTION = ("@{if(empty(body('Reminders_due_soon')), '', concat('<p class=\"editor-paragraph\"><b>Payment reminders</b></p><ul>', "
           "join(body('Reminder_lines'), ''), '</ul><br>'))}")


def steps(site, list_id, host):
    """The new steps. `host` is the SharePoint connection of the step that reads the submissions list."""
    return {
        "Initialize_ReminderDaysAhead": {
            "runAfter": {"Initialize_TestMode": ["Succeeded"]}, "type": "InitializeVariable",
            "inputs": {"variables": [{"name": "ReminderDaysAhead", "type": "integer", "value": DEFAULT_DAYS}]}},
        "Get_payment_reminders": {
            "runAfter": {ITEMS: ["Succeeded"], "Initialize_ReminderDaysAhead": ["Succeeded"]}, "type": "OpenApiConnection",
            "inputs": {"parameters": {"dataset": site, "table": list_id}, "host": dict(host),
                       "authentication": {"value": "@json(decodeBase64(triggerOutputs().headers['X-MS-APIM-Tokens']))['$ConnectionKey']",
                                          "type": "Raw"}}},
        "Reminders_with_due_date": {
            "runAfter": {"Get_payment_reminders": ["Succeeded"]}, "type": "Select",
            "inputs": {"from": "@coalesce(body('Get_payment_reminders')?['value'], json('[]'))",
                       "select": {"Name": "@item()?['Title']", "Due": NEXT_DUE, "Link": "@item()?['Link']",
                                  "Note": "@item()?['Note']", "Active": "@item()?['Active']"}}},
        "Reminders_due_soon": {
            "runAfter": {"Reminders_with_due_date": ["Succeeded"]}, "type": "Query",
            "inputs": {"from": "@body('Reminders_with_due_date')", "where": DUE_SOON}},
        "Reminder_lines": {
            "runAfter": {"Reminders_due_soon": ["Succeeded"]}, "type": "Select",
            "inputs": {"from": "@body('Reminders_due_soon')", "select": LINE}},
    }


LOOP = "Pull_out_attachments_and_prepare"
ROWS_FILLED = "Rows_already_filled_in"
ROWS_FILLED_WHERE = "@and(not(empty(item()?['Type'])), not(equals(item()?['Type'], 'Reminder')))"
ROW_TYPE = "Reminder"
COMMENT = ("@{concat('Due ', formatDateTime(item()?['Due'], 'dddd d MMMM'), ' (', " + _WHEN + ", ')'"
           ", if(empty(item()?['Note']), '', concat('. ', item()?['Note'])))}")


def workbook_steps(add_row, columns):
    """One row per reminder in the week's workbook, after the items: Type Reminder, no number, no amount. `add_row` is the
    loop's Add a row step (for the workbook and its connection), `columns` the workbook's column names in order."""
    row = {c: "" for c in columns}
    row.update(Type=ROW_TYPE, Vendor="@{item()?['Name']}", Comment=COMMENT, ProcessedDate="@{" + TODAY + "}",
               LinkToThing="@{item()?['Link']}")
    add = json.loads(json.dumps(add_row))
    add["runAfter"] = {"Compose_Reminder_Row": ["Succeeded"]}
    add["inputs"]["parameters"]["item"] = "@outputs('Compose_Reminder_Row')"
    add.pop("metadata", None)
    return {
        # Reminder rows already in the workbook (a second send the same day), so none is added twice.
        "Existing_reminder_rows": {
            "runAfter": {ROWS_FILLED: ["Succeeded"]}, "type": "Query",
            "inputs": {"from": "@coalesce(body('List_existing_rows')?['value'], json('[]'))",
                       "where": f"@equals(item()?['Type'], '{ROW_TYPE}')"}},
        "Existing_reminder_names": {
            "runAfter": {"Existing_reminder_rows": ["Succeeded"]}, "type": "Select",
            "inputs": {"from": "@body('Existing_reminder_rows')", "select": "@item()?['Vendor']"}},
        "Add_reminder_rows": {
            "foreach": "@body('Reminders_due_soon')",
            "runAfter": {LOOP: ["Succeeded"], "Reminders_due_soon": ["Succeeded"], "Existing_reminder_names": ["Succeeded"]},
            "type": "Foreach", "runtimeConfiguration": {"concurrency": {"repetitions": 1}},
            "actions": {
                "Reminder_row_not_there_yet": {
                    "runAfter": {}, "type": "If",
                    "expression": "@not(contains(body('Existing_reminder_names'), item()?['Name']))",
                    "actions": {
                        "Compose_Reminder_Row": {"runAfter": {}, "type": "Compose", "inputs": row},
                        "Add_reminder_row": add},
                    "else": {"actions": {}}}}},
    }


def add(definition, site, list_id):
    """Add the steps to a weekly flow definition in place: the email section, then the workbook rows. Returns what
    changed; nothing if both are already there."""
    actions = definition["actions"]
    changes = []
    if "Get_payment_reminders" not in actions:
        for name in (ITEMS, MAIL, "Initialize_TestMode"):
            if name not in actions:
                raise ValueError(f"the weekly flow has no step named {name}")
        host = actions[ITEMS]["inputs"]["host"]
        if host.get("operationId") != "GetItems":
            raise ValueError(f"{ITEMS} is not a Get items step")
        actions.update(steps(site, list_id, host))
        mail = actions[MAIL]
        mail["runAfter"]["Reminder_lines"] = ["Succeeded"]
        body = mail["inputs"]["parameters"]["emailMessage/Body"]
        if THANKS not in body:
            raise ValueError("the email body has no \"Thank you!\" paragraph to put the reminders before")
        mail["inputs"]["parameters"]["emailMessage/Body"] = body.replace(THANKS, SECTION + THANKS, 1)
        changes.append("weekly flow: payment reminders read from the list and added to the email (5 new steps)")
    if "Add_reminder_rows" not in actions:
        for name in (LOOP, ROWS_FILLED, "List_existing_rows", "Delay"):
            if name not in actions:
                raise ValueError(f"the weekly flow has no step named {name}")
        inner = actions[LOOP]["actions"]
        if "Compose_Excel_Row" not in inner or "Add_a_row_into_a_table" not in inner:
            raise ValueError(f"{LOOP} does not write the workbook the usual way")
        actions.update(workbook_steps(inner["Add_a_row_into_a_table"], list(inner["Compose_Excel_Row"]["inputs"])))
        actions[ROWS_FILLED]["inputs"]["where"] = ROWS_FILLED_WHERE       # reminder rows do not count towards ItemNo
        actions["Delay"]["runAfter"]["Add_reminder_rows"] = ["Succeeded"]
        changes.append("weekly flow: one Reminder row per payment due soon in the week's workbook, after the items (3 new steps)")
    return changes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client")
    args = ap.parse_args()
    s, lock = st.load(args.client)
    list_id = lock.get("lists", {}).get("reminders")
    if not list_id:
        sys.exit(f"STOPPED: {args.client}'s lock file has no lists.reminders. Make the list first: "
                 f"python3 tools/setup_lists.py {args.client} --apply")
    path = st.flow_file(args.client, lock["flows"]["weekly"])
    doc = json.loads(path.read_text())
    changes = add(doc["properties"]["definition"], s["sharepoint"]["listsSite"], list_id)
    if not changes:
        print(f"{args.client}: the weekly flow already has the payment reminders steps. Nothing to change.")
        return
    path.write_text(json.dumps(doc, indent=2))                              # as the inspector writes it
    for c in changes:
        print("  " + c)
    print(f"Written to {path.relative_to(st.ROOT)}. Next: tools/apply_settings.py {args.client} --write (sets the look-ahead), "
          f"tools/check_settings.py {args.client}, the tests, then tools/push_flows.py {args.client}.")


if __name__ == "__main__":
    main()
