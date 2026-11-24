# Installing MySubmissions

This guide is written so that a staff person who is comfortable with computers can install MySubmissions with Claude's help. Open this folder in Claude Code and say:

> Help me install MySubmissions for our organization. Follow INSTALL.md with me, one step at a time.

**For Claude:** go one step at a time and wait for the person. Say what each command does before running it, and which site or environment it changes. Announce every browser sign-in before it happens: which account, which service (SharePoint or Power Automate), and why. Install on a test SharePoint site first, never straight onto the site the office works from. Write what you learn about this installation (site addresses, group names, who the bookkeeper is, decisions) into `CLAUDE.local.md` as you go.

Plan on an afternoon for a first install. Most of it is a few minutes of waiting at a time.

## What you need first

| You need | Notes |
|---|---|
| A Microsoft 365 tenant | With SharePoint, Power Apps and Power Automate, which the business and nonprofit plans include. |
| A SharePoint site for the lists | Where submissions are stored. The office's Finance or Operations site, or a new one. You must be a **site owner**. |
| A SharePoint site for the weekly output | Where the dated folders and spreadsheets go, shared with the bookkeeper. Can be the same site. |
| Two Microsoft 365 groups | Who may approve expenses and deposits (say, Finance Approvers) and who may approve income (Income Approvers). The same group can do both. Make them in **admin.microsoft.com > Teams & groups > Active teams & groups**. |
| The bookkeeper's email address | And anyone else who should get the weekly email. |
| A computer with Python 3.9 or newer and Git | Mac, Windows or Linux. |
| Permission to import a canvas app | In **make.powerapps.com**, in your default environment. Most staff have this. If the import is refused, ask your Microsoft 365 administrator for the Environment Maker role. |

No premium licence is needed: the app and the flow use only standard connectors.

## Step 1. Get the code

```
git clone https://github.com/Unwind-Housing/mysubmissions.git
cd mysubmissions
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell the activate line is `.venv\Scripts\Activate.ps1`.

Then check everything runs, with no sign-in:

```
python3 tests/test_tools.py
```

## Step 2. Make your organization's folder

```
cp -R template/example clients/yourname
```

Pick a short lower-case name with no spaces (`yourname` above, for example `eht` for Example Housing Trust). Open `clients/yourname/settings.json` and fill it in. Every setting is explained in `docs/SETTINGS.md`; the ones you must change:

| Setting | What to put |
|---|---|
| `org.name`, `org.short`, `org.appName` | Your organization's name, a short code for file names, and what the app is called in Power Apps |
| `brand.headerColor` | Your colour, as `#RRGGBB`. `brand.logo`: a square PNG or JPEG in your folder, for the app's icon. |
| `sharepoint.listsSite` | The address of the site for the lists. `outputSite` only if the weekly output goes to a different site. |
| `approvers.financial`, `approvers.income` | The two groups' names |
| `weekly.recipients`, `bookkeepers`, `financeDirector` | Who gets the weekly email, who the folder is shared with, and who runs the books and gets a copy of every email |
| `weekly.subject`, `greeting` | The start of the email's subject, and its first line |
| `weekly.testMode` | Leave `true` for now: test runs email only the finance director and mark nothing as sent |

Leave `settings.lock.json` alone: the scripts fill it in.

Then check the file:

```
python3 tools/check_settings.py yourname --format
```

This folder is yours. Git ignores it here, so nothing in it reaches the public repository. To keep it backed up and versioned, make it its own private repository: `cd clients/yourname && git init`.

## Step 3. Make the SharePoint lists and the output library

```
python3 tools/setup_lists.py yourname
python3 tools/setup_lists.py yourname --apply --weekly --sample
```

- The first line is a dry run: it signs in to SharePoint (a browser window opens; use your own account, which must be a site owner) and says what it would make. The second makes it. Each line is its own sign-in, because the dry run asks only to read and the second asks to create lists. If you trust the list of what it makes (below), skip the dry run.
- It creates four lists on the lists site: **Chargeable Projects**, **Chargeable Cost Accounts**, **Financial Submissions** and **Payment Reminders**, with their columns; the output library named in `weekly.library` on the output site, with an empty spreadsheet template in it; and, with `--sample`, a few sample projects, cost accounts and one sample payment reminder to start with. The lists' IDs and your tenant's Default environment (where the flow and the app will go) go into `settings.lock.json`.
- It is safe to run again: it adds what is missing and never changes or removes what is there.

If the sign-in is refused with a message about admin consent, your tenant blocks the Microsoft Graph command line tools. Ask Claude to list the lists and columns from `tools/setup_lists.py`, make them by hand in SharePoint with exactly those names, and have Claude put each list's ID into `settings.lock.json` (the ID is in the list's address under **List settings**).

## Step 4. Record the approver groups

The app checks group membership by the groups' IDs. Open **entra.microsoft.com > Groups > All groups**, open each approver group and copy its **Object ID**. Put them in `clients/yourname/settings.lock.json`:

```json
"groups": {
  "financial": "the Finance Approvers group's Object ID",
  "income": "the Income Approvers group's Object ID"
}
```

## Step 5. Create the weekly flow

```
python3 tools/build_weekly_flow.py yourname
python3 tools/build_weekly_flow.py yourname --apply
```

- The first line shows what the flow will do and where it reads and writes, with no sign-in. The second signs in to Power Automate and creates **Process Financial Submissions** in your default environment, owned by you. The flow's ID goes into `settings.lock.json`. Running it again updates the flow in place.
- **Connections.** The flow needs your connections to SharePoint, Excel Online (Business) and Office 365 Outlook. Today the tool can only copy them from a MySubmissions flow that already exists in the same environment: add `--connections-from <name>`, where `<name>` is another organization folder in `clients/` whose flow was made this way (a test install next to the real one, say). **If this is the first MySubmissions flow in your tenant, the tool cannot create it yet.** That is the next thing being fixed; until then, open an issue and we will walk you through it.

Whoever presses Send to bookkeeper in the app runs the flow on their own connections, and is asked to allow them the first time. They also need access to the output site.

## Step 6. Build the app and import it

```
python3 tools/build_template.py yourname --package dist/yourname.zip
```

This stamps your settings into the template app and writes an importable package. Then, in **make.powerapps.com**:

1. **Apps > Import canvas app > Upload**, pick `dist/yourname.zip`.
2. Under Import setup, leave the app as **Create as new**. For the SharePoint connection, choose **Select during import** and pick your own connection (or create one).
3. **Import**. Open the app with **Play** and check the home page shows your colour and title.

## Step 7. Connect the flow to the app

The Send to bookkeeper button starts the flow from inside the app. That link can only be made in the app editor, once:

1. **Apps >** your app **> Edit**. On the left, open the **Power Automate** pane, **+ Add flow**, pick **Process Financial Submissions**. Then **Save**, **Publish**.
2. **Apps > ... > Export package** (not Export .msapp). Name it anything; download the zip into `exports/`.
3. Record the connection and rebuild:

```
python3 tools/build_template.py yourname --capture-hookup exports/<the file>.zip
```

4. In `settings.json` set `weekly.runFromApp` to `true`, then:

```
python3 tools/apply_settings.py yourname --write
python3 tools/build_template.py yourname --package dist/yourname.zip
```

5. Import `dist/yourname.zip` again. This time set the app's row to **Update** and pick your app, and the flow's row to **Update** and pick Process Financial Submissions. **The import switches the flow off:** afterwards, **make.powerautomate.com > My flows > Process Financial Submissions > Turn on.** Every later import does the same.

## Step 8. Fill the lists and share the app

- **Chargeable Projects** and **Chargeable Cost Accounts** (on the lists site): your real projects and your chart of accounts' cost accounts. Delete the sample rows. Staff pick from these.
- **Payment Reminders**: one row per recurring bill the bookkeeper should be reminded of, such as a credit card: what it is, the day of the month it is due, a link to pay it, a note. Leave it empty if you don't want reminders.
- Add approvers to the two groups.
- **Apps > ... > Share**: share the app with the staff who will submit things, and with the approvers. Share the SharePoint lists site with the same people (Edit).

## Step 9. Check it works

With a made-up item:

1. Open the app on a phone or in a browser. **Expense**: fill it in, attach a photo, Submit. It should appear under **Your items**.
2. As an approver (not the person who submitted it): **Financial Approvals**, open the item, tick Approved.
3. **Send to bookkeeper > Send**. The reply should say "TEST RUN. Sending 1 item to the bookkeepers." A few minutes later the finance director gets the email with the folder and the spreadsheet; the test item is not marked as sent, so you can run it again.
4. Check the folder and the spreadsheet on the output site.

When it all looks right, set `weekly.testMode` to `false`, run `python3 tools/apply_settings.py yourname --write`, then step 5's second command to update the flow, and rebuild and import the package as in step 7 (turn the flow back on). From then on the email goes to the recipients and sent items are marked as processed.

## Getting new versions

```
git pull
python3 tests/test_tools.py
python3 tools/check_settings.py yourname
python3 tools/build_weekly_flow.py yourname --apply
python3 tools/build_template.py yourname --package dist/yourname.zip
```

Read `docs/releases/` for what changed and anything a version asks you to do. Then import the package over your app (app row and flow row set to **Update**) and turn the flow back on. If a release adds a list or a column, run step 3's `--apply` line again first: it adds only what is missing.

## If something goes wrong

| What you see | Do this |
|---|---|
| The sign-in window goes to the wrong organization | Pick **Use another account** and sign in with your work account for this tenant. |
| A sign-in error mentioning admin consent | Your tenant blocks the command line tools for SharePoint. See the end of step 3 for the by-hand path. |
| `STOPPED:` followed by a message | Fix what it names and run the same command again. If it stops in the same place twice, give Claude the message. |
| Import says "Connection not configured for this service" | The package was built before the flow was connected (step 7). Do step 7 again from the export. |
| Send to bookkeeper opens a web page instead of sending | `weekly.runFromApp` is still `false`, or the package was built before step 7's capture. |
| The flow does nothing after Send | It was switched off by the last import. **My flows >** the flow **> Turn on.** |
| Submit fails with "Field(s) with problems" | A list column is missing or misnamed. Run step 3's `--apply` line again and read what it adds. |
| Staff see "You don't have access" on the home page | Share the lists site with them (step 8), not just the app. |
