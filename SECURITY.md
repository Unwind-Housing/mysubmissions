# Security

MySubmissions handles an organization's financial documents: invoices, receipts, deposit slips, and who approved what. Please report problems privately.

## Reporting a problem

Use **Security > Report a vulnerability** on this repository's GitHub page (a private report to the maintainers). Please don't open a public issue for a security problem, and never paste real documents, list rows, sign-in details or email addresses into an issue or pull request.

## What must never be committed

- Credentials, `.env` files, tokens, certificates.
- Anything in `clients/` except through the made-up example in `template/example/`. An organization's settings name its SharePoint sites, its approver groups and its bookkeeper's address.
- Exported packages (`exports/`, `dist/`) and anything pulled from a tenant (`unpacked/`). A package carries the organization's connections and list IDs.
- Real people's details in tests, examples or screenshots. Use made-up names at `example.com`.

## How access works in the design

- The data lives in SharePoint lists on your own site. Who can see and change it is SharePoint's permissions on that site, not the app's. Share the site with the people who use the app, and no one else.
- Approvers are the members of two Microsoft 365 groups. The app checks membership each time it opens; the lists themselves do not stop a staff member from editing an Approved column by hand in SharePoint. If that matters to you, keep the lists site small and trusted.
- The weekly flow runs as the person who presses Send, on their own connections, and shares each week's folder with the bookkeepers' addresses in the settings. Check those addresses when a bookkeeper changes.
- The design is for two people to touch every item: the one who submits it and the one who approves it. Today the shared app does not stop an approver approving their own submission; making that a rule (with a setting for organizations that need to allow it) is planned.
