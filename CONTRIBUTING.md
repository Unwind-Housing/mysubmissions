# Contributing to MySubmissions

MySubmissions is built to be run by many organizations from one code base. The aim is that every organization can take new versions safely, and that an improvement made at one reaches the others.

## How changes flow

1. **Fork** this repository (or clone it, if you are a maintainer) and install from your copy.
2. Make a change on a **branch based on `main`**.
3. Run the tests (below). They run offline, with no Microsoft sign-in.
4. Try it on a **test SharePoint site** with a test copy of the app, not on the office's live app.
5. Open a **pull request** to `main` here. The same tests run automatically. Say what the change is for and how you tried it.
6. Once it's merged, other organizations get it by pulling `main`, rebuilding their package and importing it over their app (`INSTALL.md`, "Getting new versions").

## Rules that keep every installation compatible

- **Nothing specific to one organization in the template or the tools.** No organization names, site addresses, email addresses or IDs in `template/`, `tools/` or `tests/`. Those belong in an organization's own folder, `clients/<name>/`, which git ignores. The only organization that appears in the repo is the made-up Example Housing Trust in `template/example/`.
- **Need something different? Make it a setting.** If your organization needs a different behaviour, add a setting in `settings.json` that any organization could use, stamp it in the template, document it in `docs/SETTINGS.md`, and send that back. A private edit of the app will collide with the next version.
- **The app is edited in code, never in the Power Apps editor.** Every formula is stored twice in the unpacked app, in `Controls/*.json` (what Power Apps loads) and `Src/*.pa.yaml` (the readable copy). Change both; `tools/check_app.py` fails if they differ. The one exception is connecting the weekly flow to the app, which only the editor can do (`INSTALL.md`, step 7).
- **Never rename a list or a column in place.** Existing installations have data in them. Add the new one and say in the release notes what happens to the old one.
- **Rebuild the package after any flow change.** The package carries a copy of the flow, and importing an old package puts the old flow back.
- **No real data.** Tests and examples use made-up organizations and people at `example.com`. See `SECURITY.md`.
- **Write the release notes for staff.** `docs/releases/<major>.<feature>.md` says what people will notice, in plain language. Fixes get a line in the Fixes table of that file.

## Tests

From the repo folder, with the virtual environment active:

```
python3 tests/test_tools.py
```

They cover the package round trip, the stamping of settings into the app and the flow, the list definitions, and the settings checks.

## Versions

Releases are tagged `v<major>.<feature>.<fix>` (for example `v2.2.1`) and listed in `README.md`; the same number is the one staff see in `docs/releases/`. A feature number goes up when people will notice a change, a fix number for small fixes. An organization's package is named `<org>-<version>.zip`.

## Asking for a feature

Open an issue describing what you need and why. If it is something your organization needs now and others might later, the maintainers can often make it a setting.

## Working with Claude

`CLAUDE.md` is the brief Claude Code reads when it opens this repo. If you use Claude to make a change, it will follow the rules above. Keep your own organization's details in `CLAUDE.local.md`, which git ignores.
