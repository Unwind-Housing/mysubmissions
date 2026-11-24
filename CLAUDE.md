# MySubmissions: project brief for Claude

MySubmissions is an open source submissions-and-approvals app for small nonprofits on Microsoft 365. Read this first. If someone has asked you to **install MySubmissions for their organization, read `INSTALL.md` next and follow it with them one step at a time.** If you are changing MySubmissions, read `CONTRIBUTING.md`, then `docs/SETTINGS.md` for what every setting does.

- `CLAUDE.local.md`, if it exists, holds this installation's own details (sites, group names, the bookkeeper, the person's preferences). It is not committed. Create it during the install.
- Every browser sign-in is announced before it happens: which account, which service (SharePoint through Microsoft Graph, or Power Automate; they are separate sign-ins), and why. Before running anything that changes a SharePoint site, a flow or an app, say what it will do.
- Before every commit: `python3 tests/test_tools.py`.

## What this is

A Power Apps canvas app plus one Power Automate flow. Staff submit expenses, bank deposits and income items with documents attached; approvers approve them on two screens; one button sends the week's approved items to the bookkeeper as a folder of documents and a spreadsheet. Data lives in SharePoint lists on the organization's own site. Standard connectors only.

- **Maintainer:** Josey's LLC d/b/a Unwind Housing. MIT licence. Each organization owns its own tenant and data.
- **One template, one settings file per organization.** `template/app` is the app with every organization-specific value as a token (`__MS_LISTS_SITE__` and the like); `template/flows/weekly.json` is the flow the same way. `clients/<name>/settings.json` (names and choices, typed by a person) and `settings.lock.json` (IDs, written by the scripts) are stamped in by the tools. Nobody edits the app in the Power Apps editor, except to connect the flow once.

## Where things are

| Folder | What it holds |
|---|---|
| `template/app/` | The app, unpacked: screens as `Src/*.pa.yaml` (readable), controls as `Controls/*.json` (what Power Apps loads), images, references. Every formula is in both; they must match. |
| `template/flows/weekly.json` | The weekly flow definition with tokens |
| `template/fragments/` | Pieces a switched-on feature adds to the app |
| `template/example/` | The made-up Example Housing Trust: the starting point for an organization's folder, and what the tests use |
| `template/assets/` | The placeholder app icon |
| `clients/<name>/` | An organization's settings, lock file, logo, and (after the flow is connected) its package wrapper. Git ignores everything here. |
| `tools/` | The scripts, below |
| `tests/test_tools.py` | Offline tests, no sign-in |
| `docs/SETTINGS.md` | The settings reference. `docs/releases/`: release notes for staff. |

## The tools

From the repo root, with `.venv` active. `<org>` is the folder name under `clients/`.

```
python3 tools/check_settings.py <org>                    settings.json well formed, and in step with the lock file
python3 tools/setup_lists.py <org> [--apply --weekly]    make the lists (and the output library) on the organization's sites
python3 tools/build_weekly_flow.py <org> [--apply]       the weekly flow from the template and the settings; --apply creates or updates it
python3 tools/build_template.py <org> --package dist/<org>.zip     the importable app package
python3 tools/build_template.py <org> --capture-hookup exports/<export>.zip   record how the flow is connected to the app
python3 tools/apply_settings.py <org> [--write]          stamp a changed setting into the flow definition
python3 tools/weekly_reminders.py <org>                  give an older flow the payment reminders steps
python3 tools/check_app.py                                the two copies of every formula in the template agree
python3 tools/app_edit.py ...                             rename or delete a screen, remove an image, keeping every copy in step
```

Sign-ins: `setup_lists.py` signs in to SharePoint (Microsoft Graph, the person must be a site owner; `--apply` asks for the right to create lists). `build_weekly_flow.py --apply` signs in to Power Automate. Everything else runs offline on files in the repo.

## Rules

- **Nothing specific to one organization in `template/`, `tools/` or `tests/`.** The example organization is the only one named.
- **Change the app in code, in both copies.** `tools/check_app.py` fails if `Controls` and `Src` disagree. Helpers for common edits are in `tools/app_edit.py`.
- **A changed setting goes through `settings.json` and the tools**, never by hand into a flow or an app.
- **Rebuild the package after any flow change.** The package carries a copy of the flow; an old package puts the old flow back.
- **Every import switches the packaged flow off.** Remind whoever imports to turn it on again (Power Automate > My flows > the flow > Turn on). Never record that a flow is on unless the person said so.
- **Never commit client data:** list rows, submissions, attachments, exports, built packages. `clients/`, `exports/`, `dist/` and `unpacked/` are ignored for that reason.
- **Release notes are for staff:** plain language, what they will notice, no internals.

## Conventions

- Versions: one line, `major.feature.fix`. Releases are `v2.2.1`-style tags, with staff release notes in `docs/releases/`.
- Work on a branch based on `main`, open a pull request, run the tests first.
- Plain language, step-by-step instructions with exact menu paths. One command at a time, with a check before anything that changes a tenant.
