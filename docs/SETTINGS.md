# MySubmissions settings file

Status: in use since 2026-11-15. Each organization has `clients/<org>/settings.json` and `settings.lock.json`. The package build, the flow builders and `tools/apply_settings.py` read them. Settings not yet stamped anywhere (feature switches) are compared with an organization's own app by `tools/check_settings.py` when the app is in the repo.

Each organization has one settings file, `clients/<org>/settings.json`. It holds everything that differs between organizations. The build stamps it into the app package and the flows, so nobody edits those by hand.

## Two files, not one

| File | Who writes it | What is in it |
|---|---|---|
| `settings.json` | A person | Choices and names: the organization's name, colours, the SharePoint site, list names, who approves, which features are on, who gets the emails |
| `settings.lock.json` | The setup script | What the tools looked up: list IDs, group IDs, site and drive IDs, flow IDs. Never typed by hand. |

The reason for the split: an installer knows "the Finance Approvers group" and "the Operations site". They should never have to find a GUID.

## Example

A made-up organization with everything switched on.

```json
{
  "org": {
    "name": "Example Housing Trust",
    "short": "EHT",
    "appName": "EHT Submissions"
  },

  "brand": {
    "headerColor": "#3860B2",
    "logo": "logo.png"
  },

  "sharepoint": {
    "listsSite": "https://example.sharepoint.com/sites/Operations",
    "outputSite": "https://example.sharepoint.com/sites/Accounting",
    "lists": {
      "submissions": "Financial Submissions",
      "projects": "Chargeable Projects",
      "costAccounts": "Chargeable Cost Accounts"
    }
  },

  "approvers": {
    "source": "groups",
    "financial": "Finance Approvers",
    "income": "Income Approvers"
  },

  "features": {
    "bankDeposit": true,
    "income": "income",
    "grants": "off",
    "approvalScreens": "separate",
    "addProjectLink": true,
    "confirmationEmail": false,
    "timecards": true
  },

  "weekly": {
    "library": "Approved Financial Submissions",
    "template": "Financial Submissions Template.xlsx",
    "subject": "EHT Weekly Financials",
    "recipients": ["director@example.org", "books@examplecpa.com"],
    "bookkeepers": ["books@examplecpa.com"],
    "financeDirector": ["finance@example.org"],
    "replyTo": "finance@example.org",
    "runFromApp": true,
    "testMode": false
  },

  "timecards": {
    "firstMonth": "2026-11",
    "approver": "Finance Approvers",
    "approverApprover": "director@example.org",
    "reimbursements": "paycheck",
    "library": "Approved Timecard Submissions",
    "template": "Template/Timesheet Template.xlsx",
    "filePrefix": "EHT Timesheet",
    "recipients": ["books@examplecpa.com"],
    "bookkeepers": ["books@examplecpa.com"],
    "testMode": true
  }
}
```

## What each setting does

### org

| Setting | Meaning |
|---|---|
| `name` | Full name, used in email text |
| `short` | Short name, used in package names (`eht-2.1.0.zip`) and email subjects |
| `appName` | The app's name in Power Apps. A production build always uses this, whatever app the package was exported from. |
| `title` | Optional. The title on the home page. Left out, it is "My Submissions". |

### brand

| Setting | Meaning |
|---|---|
| `headerColor` | Colour of the header bar, the one brand colour. Buttons and everything else are the same for everyone. |
| `headerTextColor` | Optional. The colour of the title, the name and the icons on the header bar. Left out, it is white on a dark header and near-black on a light one. An organization with a light header sets it to a dark colour of its own. |
| `logo` | Image file, as a path inside the organization's folder: it becomes the app's icon (the picture on its tile in Power Apps; the header bar has no logo). A square PNG or JPEG. Left out, the template's plain placeholder stays. |

### sharepoint

| Setting | Meaning |
|---|---|
| `listsSite` | The site that holds the lists |
| `outputSite` | The site the weekly and monthly files are written to. Leave out when it is the same site. |
| `lists` | What each list is called at this organization. The template uses the neutral names on the left. New installs can leave this out and take the default names. Always: `submissions`, `projects`, `costAccounts`, `reminders` (the Payment Reminders list the weekly email reads, D-16; left out, the default name is used). With timecards: `timecards`, `timeEntries`, `payrollStaff`, `holidays`, `mileageRates`. With grants: `grants`. With approvers from a list: `approvers`. |

Existing lists keep their names and their data. The setup script creates only the lists that are missing.

### approvers

| Setting | Values | Meaning |
|---|---|---|
| `source` | `groups` or `list` | `groups`: Microsoft 365 or security groups. `list`: a SharePoint list of email addresses. |
| `financial`, `income` | A group's name or its ID | With `groups`. The setup script finds the ID from a name. |
| `list` | A list's name | With `list`: a SharePoint list with one email address per row, in the Title column. New installs call it MySubmissions Approvers. Its ID goes in the lock file under `lists.approvers`. One list covers financial and income approvals. |
| `approveOwnIncome` | `true` or `false` | Left out means `false`: nobody approves their own submission (D-11). `true`: on Income Approvals an approver may approve an income item they submitted themselves. Expenses and deposits are never included. The template does not stamp it yet. |

### features

| Setting | Values | Meaning |
|---|---|---|
| `bankDeposit` | `true`, `false` | The Bank deposit tile and form |
| `income` | `income`, `donation`, `off` | What the third tile is. `donation` also gives the "recorded" tick on the approvals screen. |
| `grants` | `off`, `expense`, `expenseAndIncome` | The Grant field. `expenseAndIncome` also gets the grant picker and balances for approvers. |
| `approvalScreens` | `separate`, `combined` | Two approvals screens (the usual), or one for expenses and income together (an older layout) |
| `addProjectLink` | `true`, `false` | The "+ Add a project" link on the forms |
| `confirmationEmail` | `true`, `false` | A flow emails the submitter when their item is saved |
| `timecards` | `true`, `false` | The Time tile, Timecard, Your timecards and Timecard Approvals, and the monthly flow |

### weekly (the weekly package for the bookkeepers)

| Setting | Meaning |
|---|---|
| `library` | The document library the dated folders go in |
| `template` | The workbook template in that library |
| `subject` | Start of the email subject |
| `greeting` | Optional first line of the weekly email, such as "Hello Pat!". Left out: "Hello!" |
| `flowName` | Optional name for the weekly flow. Left out: Process Financial Submissions v4. Only needed when two organizations share an environment, as a test install does with the real one. |
| `recipients` | Who gets the weekly email |
| `bookkeepers` | Who the week's folder is shared with |
| `financeDirector` | The person who runs the bookkeeping, as a list of one or more addresses. They get a copy of every email, and they are the only ones who get test runs. |
| `replyTo` | Where replies to the weekly email go. Left out: the first `financeDirector` address. |
| `fileLinks` | Records where an existing client is today. `file` (the default): each link in the weekly spreadsheet opens the file itself. `folder`: PDFs and photos open their folder (older installations). A new organization never sets it. |
| `runFromApp` | `true`: Send to bookkeeper on Financial Approvals runs the weekly flow from inside the app. `false`: the flow is run from Power Automate. In an app built from the template the button is always there (D-12); with `false` it opens the flow's page. A new organization starts on `false` and switches to `true` once the flow has been connected to the app in the editor and recorded with `tools/build_template.py --capture-hookup` (steps in `template/README.md`). |
| `testMode` | `true`: folder name ends in TEST, nothing is marked processed, only `financeDirector` is emailed |
| `reminderDaysAhead` | Optional, 1 to 90. The weekly email lists each row of the Payment Reminders list that is due within this many days of the run's date, under "Payment reminders" (D-16). Left out: 14. The list itself (what, due day of the month, link, note, active) is edited in SharePoint, not here. |

### timecards (only read when `features.timecards` is true)

| Setting | Meaning |
|---|---|
| `firstMonth` | Earlier months are locked |
| `approver` | A person's email or a group's name. Left out: the financial approvers. |
| `approverApprover` | Who approves the approver's own timecard: a person's email or a group's name. If it is a person who is also an approver, that person may approve their own. |
| `reimbursements` | `paycheck`: reimbursements go on the timesheet and the weekly package skips them. `weekly`: they go with the weekly package. Without timecards they are always weekly. |
| `library`, `template`, `filePrefix` | Where the monthly workbook goes, its template, and the start of its file name |
| `recipients`, `bookkeepers` | Who gets the monthly email, and who the workbook is shared with |
| `testMode` | `true`: workbook named TEST, nothing marked processed, only the finance director (`weekly.financeDirector`) is emailed |

## The lock file

`settings.lock.json` holds what the tools looked up, never typed by hand:

| Key | What it holds |
|---|---|
| `environment` | The Power Platform environment the app and flows live in |
| `lists` | The ID of each list named in `sharepoint.lists` |
| `groups` | The IDs of the approver groups |
| `libraries` | The IDs of the output libraries (`weekly`, `timecards`) |
| `flows` | The IDs of the flows (`weekly`, `timecards`, `confirmationEmail`) |
| `columns` | Internal column names where organizations differ: `approved`, and `donationRecorded` where that column has another name |
| `app` | `weeklyFlow`: how the weekly flow is connected to the app, as the editor recorded it (the flow as a data source, and its connections). Written by `tools/build_template.py --capture-hookup`; needed when `weekly.runFromApp` is true in an app built from the template. `connections`: the instance IDs of the app's own connections (`office365groups`, `flowmanagement`, `sharepointonline`), once it has been exported; before that the template's placeholders are used. |
| `graph` | IDs as Microsoft Graph names them, for the flows' Excel steps. Weekly flow: `weeklyExcelSource` and `weeklyDrive`. With timecards: `outputSite` and `timecardsDrive`. |

## Changing a setting

1. Edit `clients/<org>/settings.json`.
2. Stamp it into the repo's copy of the app and weekly flow:

   ```
   python3 tools/apply_settings.py eht            dry run: lists what would change
   python3 tools/apply_settings.py eht --write    changes the files under clients/eht
   ```

3. Send it out the usual way: `tools/push_flows.py` for the weekly flow, `tools/build_timecard_flow.py --apply` for the timecard flow, `tools/build_package.py` for the app.
4. **Rebuild the package after any flow change, before anyone imports it.** The package carries a copy of each flow.

What reads which setting today:

| Setting | Read by |
|---|---|
| `org.appName` | `build_package.py`: a production build is given this name. A `--beta` build keeps the name of the app it was exported from. |
| `org.short`, version | `build_package.py`: the package's file name and label |
| `weekly.testMode`, `recipients`, `bookkeepers`, `financeDirector`, `replyTo`, `reminderDaysAhead` | `apply_settings.py`, into the weekly flow |
| `timecards.firstMonth`, `approver`, `approverApprover` | `apply_settings.py`, into the app's formulas |
| Everything else under `timecards`, plus the lists and sites it needs | `build_timecard_flow.py`, which builds the whole Process Timecards flow |
| Colour, logo, list names, approver groups, feature switches, `weekly.library`, `template`, `subject`, `runFromApp` | In an organization's own pulled app: checked against the app and flows, not stamped. `tools/build_template.py` stamps the colour, lists, approvers and `runFromApp` into an app built from the template. |

## Checking a settings file

```
python3 tools/check_settings.py          all clients
python3 tools/check_settings.py eht      one organization
```

It checks two things and changes nothing:

1. **The file is well formed:** nothing missing, choices are from the allowed values, addresses look like addresses.
2. **It says what the repo does:** the header colour, list names and IDs, approver groups, feature switches, the weekly flow's recipients, bookkeepers, library, template, subject, test mode and trigger, and the timecard settings, each compared with the app and flows under `clients/<org>`.

The offline tests run it for the example organization (and, in the private repo, for every client), and also prove it catches wrong values.

## What is not a setting

- **Approving your own submission.** Nobody can, at any organization (decided 2026-11-15). The one exception is timecards, through `timecards.approverApprover`.
- **The lists' columns.** Every organization gets the same internal column names, so the same flows work everywhere. Existing clients keep the columns they have; the lock file records which column plays which role where they differ (for example, an older list's approved column can have another internal name than `Approved`).
- **Screen layout, fonts, sizes.** One design for everyone.
- **Connections.** Whoever installs it picks their own connections on import.
- **Extract flows** (audit items, grant extracts). They stay per organization for now.

## Decided (2026-11-15)

- One header colour is enough.
- Nobody approves their own submission, everywhere. It is a rule, not a setting.
- The copy and test address is called `financeDirector`.
- An organization's old `timecards.json` is folded in: its choices are under `timecards`, its IDs are in the lock file.
