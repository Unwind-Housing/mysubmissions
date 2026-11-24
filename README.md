# MySubmissions

MySubmissions is an open source way for a small nonprofit to collect invoices, receipts, bank deposits and income from staff, have a second person approve each one, and send the week's approved items to the bookkeeper as one tidy package: a folder of the documents and a spreadsheet of the details.

It runs on **Microsoft 365**, which most nonprofits already have: a Power Apps canvas app on the phone and the computer, SharePoint lists for the data, and one Power Automate flow for the weekly package. Everything stays in your own tenant. The app uses only standard connectors (SharePoint, Excel, Outlook), so it normally needs no Power Apps licence beyond the Microsoft 365 ones your staff already hold.

## What it does

**For staff**
- Submit an expense (invoice or receipt), a bank deposit or an income item from a phone or a computer, with a photo or a PDF attached.
- Pick the project and the cost account from your own lists, so the bookkeeper gets the coding right first time.
- See your own items, open or approved, and fix or delete one before it is approved.

**For approvers**
- Financial Approvals: everything waiting, with the document beside it. Approve, change the coding, add a comment, or send it back.
- Income Approvals: a separate screen for income, with its own approvers.
- **Send to bookkeeper**: one button runs the weekly package and tells you what went.

**For the bookkeeper**
- One email a week: a link to a dated folder with every document, sorted by type, and a spreadsheet with one row per item (vendor, project, cost account, amount, comment, links to the files).
- Payment reminders for recurring bills, such as a credit card, from a list your office keeps.

Planned as settings: monthly timecards with a payroll package, grants on expenses and income, and donations in place of income (see Status).

## How it's built

An organization's app is **built from a shared template plus one settings file**, by script. Nobody edits the app by hand, so an update is a rebuild and an improvement made at one organization reaches the others.

| Folder | What it holds |
|---|---|
| `template/app/` | The canvas app, unpacked, with every value that belongs to one organization replaced by a token |
| `template/flows/` | The weekly flow, the same way |
| `template/example/` | A made-up organization, Example Housing Trust: copy it to start your own |
| `tools/` | The scripts: make the SharePoint lists, build the flow, build the app package, check that settings and app agree |
| `tests/` | Offline tests. No Microsoft sign-in needed. |
| `docs/SETTINGS.md` | Every setting, what it does and where it lands |
| `docs/releases/` | Release notes, written for staff |

Your own organization's folder, `clients/<yourname>/`, holds its settings and is ignored by git here. Keep it as its own private repository.

## Install

See **[INSTALL.md](INSTALL.md)**. It is written to be followed with [Claude Code](https://claude.com/claude-code): open this folder and say "Help me install MySubmissions for our organization." Plan on an afternoon.

## Contribute

See **[CONTRIBUTING.md](CONTRIBUTING.md)**. Report security problems privately: **[SECURITY.md](SECURITY.md)**.

## Status

MySubmissions is not yet at v1.0: settings can still change between versions, and the features listed above as planned are still being added. Try it on a test SharePoint site first.

## Licence

MIT. See [LICENSE](LICENSE). Maintained by Josey's LLC d/b/a Unwind Housing, which also offers paid installs and support.

## Versions

One number line, `major.feature.fix`: the feature number goes up when people will notice a change, the fix number for small fixes. Each release is a Git tag (`v2.2.1`) with a GitHub release titled `v2.2.1 (YYYY-MM-DD)` and release notes for staff in `docs/releases/`. An organization's own package is named after the release and the organization, for example `yourorg-2.2.1.zip`. Version 2 is the line in production use since November 2026; version 1 was never public.

| Version | Date | What changed |
|---|---|---|
| v2.2.2 | 2026-11 | First public release: expenses, bank deposits, income, two approval screens, Send to bookkeeper, the weekly flow, payment reminders. |
