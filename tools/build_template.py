"""Build an organization's app from the shared template and its settings.

Usage (from the repo root, .venv active):
    python3 tools/build_template.py eht                  compare: template + eht's settings against clients/eht/app
    python3 tools/build_template.py eht --out DIR        write the stamped app to DIR
    python3 tools/build_template.py eht --package dist/eht.zip
                                                         the importable package (the organization's own package wrapper
                                                         if it has one, else the template's; --wrapper <org> borrows another's)
                                                         build an importable package for a settings folder
    python3 tools/build_template.py template/test-install --capture-hookup exports/Export.zip
                                                         record how the weekly flow is connected to the app
    python3 tools/build_template.py --refresh-from eht   remake template/app from clients/eht/app (the app the template is made from)

template/app is one organization's app with every value that belongs to one organization replaced by a token (__MS_..._). Stamping
puts an organization's values back in, from clients/<client>/settings.json and settings.lock.json, and adds the pieces a
switched-on feature needs (template/fragments). It works on files in the repo only: it never signs in and never touches
a tenant. What is and is not templated yet is in template/README.md.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings as st  # noqa: E402

TEMPLATE = st.ROOT / "template" / "app"
FRAGMENTS = st.ROOT / "template" / "fragments"
BINARY = {".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg", ".pdf"}
# Files where the app's name is the app's name, and not a title on a screen.
NAME_FILES = ("Properties.json", "Resources/PublishInfo.json")
# How an app asks "is the person using it an approver?" when approvers are groups.
GROUP_CHECK = 'User().Email in Office365Groups.ListGroupMembers("{}").value.mail'
# The same question when approvers are a SharePoint list of email addresses (the whole address, or the
# part before the @, so an alias still matches).
LIST_CHECK = ("IsApprover = IfError(!IsBlank(LookUp(Approvers, !IsBlank(Title) && (Lower(Trim(Title)) = Lower(Trim(User().Email)) || "
              "First(Split(Lower(Trim(Title)), \"@\")).Value = First(Split(Lower(Trim(User().Email)), \"@\")).Value))), false);")


def hex_to_rgb_text(color):
    return ", ".join(str(int(color[i:i + 2], 16)) for i in (1, 3, 5))


def header_text_color(brand):
    """The colour of the title, the name and the icons on the header bar: brand.headerTextColor if it is set, and
    otherwise white on a dark header and near-black on a light one, so they can always be read."""
    if brand.get("headerTextColor"):
        return brand["headerTextColor"]
    r, g, b = (int(brand["headerColor"][i:i + 2], 16) / 255 for i in (1, 3, 5))
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    return "#FFFFFF" if luminance < 0.4 else "#282828"


def weekly_run(s, lock):
    """What the Send to bookkeeper box does when Send is pressed, as a piece of formula that gives the message to show.
    weekly.runFromApp true: it starts the weekly flow and shows the flow's reply. That needs the flow connected to the app
    once in the editor (see capture_hookup). False: it opens the flow's page in Power Automate, for the person to press
    Run there. That is how a new organization's app starts out, before the flow is connected."""
    if s["weekly"].get("runFromApp"):
        hookup = lock.get("app", {}).get("weeklyFlow")
        return f'{hookup["service"]}.Run(Text(Today(), "yyyy-mm-dd")).result' if hookup else None
    page = f'https://make.powerautomate.com/environments/{lock["environment"]}/flows/shared/{lock["flows"]["weekly"]}/details'
    return f'If(Launch("{page}"), "The page of the weekly flow has opened. Press Run there.")'


def tokens(s, lock):
    """{token: value} for one organization. Every value has to be present: a missing one would leave a hole in the app."""
    groups, lists = lock.get("groups", {}), lock.get("lists", {})
    by_list = s["approvers"]["source"] == "list"
    out = {
        "__MS_LISTS_SITE__": s["sharepoint"]["listsSite"],
        "__MS_LISTS_SITE_PATH__": urlparse(s["sharepoint"]["listsSite"]).path,      # "/sites/Name", in the lists' descriptions
        "__MS_LIST_SUBMISSIONS__": lists.get("submissions"),
        "__MS_LIST_PROJECTS__": lists.get("projects"),
        "__MS_LIST_COSTACCOUNTS__": lists.get("costAccounts"),
        "__MS_WEEKLY_RUN__": weekly_run(s, lock),
        "__MS_HEADER_RGB__": hex_to_rgb_text(s["brand"]["headerColor"]),
        "__MS_HEADER_TEXT_RGB__": hex_to_rgb_text(header_text_color(s["brand"])),
    }
    conns = lock.get("app", {}).get("connections", {})
    for token, (api, placeholder) in CONNECTIONS.items():
        out[token] = conns.get(api, placeholder)
    if by_list:
        out["__MS_IS_FINANCIAL_APPROVER__"] = out["__MS_IS_INCOME_APPROVER__"] = "IsApprover"
        out["__MS_LIST_APPROVERS__"] = lists.get("approvers")
    else:
        for key in ("financial", "income"):
            out[f"__MS_IS_{key.upper()}_APPROVER__"] = GROUP_CHECK.format(groups[key]) if groups.get(key) else None
    missing = sorted(k for k, v in out.items() if not v)
    if missing:
        raise ValueError(f"no value for {', '.join(missing)}. If these are lists, run tools/setup_lists.py first: it makes the lists and "
                         "records their IDs. If it is __MS_WEEKLY_RUN__, the weekly flow is not connected to the app yet: see "
                         "--capture-hookup, or set weekly.runFromApp to false for now.")
    return out


def _text_files(app_dir):
    return [p for p in sorted(app_dir.rglob("*")) if p.is_file() and p.suffix.lower() not in BINARY]


TITLE_TOKEN = "__MS_TITLE__"
DEFAULT_TITLE = "My Submissions"
PLACEHOLDER_ICON = TEMPLATE.parent / "assets" / "logo-placeholder.png"
WRAPPER = TEMPLATE.parent / "wrapper"                 # a package wrapper with no organization in it, for an app that has no export yet
# The app's three connections (groups, Power Automate, SharePoint) have instance IDs that only exist in the tenant the app was
# exported from. They are tokens in the template; an organization's own come from lock app.connections once it has an export,
# and before that these stand in (the import lets the person pick connections, so the IDs only have to look like IDs).
CONNECTIONS = {"__MS_CONNECTION_GROUPS__": ("office365groups", "00000000000000000000000000000001"),
               "__MS_CONNECTION_FLOWS__": ("flowmanagement", "00000000000000000000000000000002"),
               "__MS_CONNECTION_SHAREPOINT__": ("sharepointonline", "00000000-0000-4000-8000-000000000003")}


def title_of(s):
    return s["org"].get("title") or DEFAULT_TITLE


def _formula_files(app_dir):
    """The files that hold formulas: the title on the home page is a formula, a quoted string, and it is only replaced
    there, never in the app's name (Properties.json, PublishInfo.json), which has its own token."""
    return [p for p in _text_files(app_dir) if p.relative_to(app_dir).parts[0] in ("Src", "Controls")]


def icon_path(app_dir):
    """The app's icon, the picture on its tile in Power Apps: Resources/<LogoFileName>."""
    info = _load(app_dir / "Resources" / "PublishInfo.json")
    return app_dir / "Resources" / info["LogoFileName"]


def stamp_logo(s, base, out_dir):
    """brand.logo, a file in the organization's folder, becomes the app's icon. Without it the placeholder stays."""
    logo = s.get("brand", {}).get("logo")
    if not logo or base is None:
        return
    src = Path(base) / logo
    if not src.exists():
        raise ValueError(f"brand.logo: no file {src}")
    icon_path(out_dir).write_bytes(src.read_bytes())


def _as_written(value, path):
    """A value as it appears inside this kind of file: formulas in a JSON file have their quotes escaped."""
    return (json.dumps(value)[1:-1] if path.suffix.lower() in (".json", ".sarif") else value).encode()


def _load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _save(path, doc):
    # The layout Power Apps exports: two-space indent, ASCII escapes, Windows line ends.
    path.write_bytes(json.dumps(doc, indent=2, ensure_ascii=True).replace("\n", "\r\n").encode("utf-8"))


def add_formula(app_dir, formula):
    """Add a named formula to the app, in both places it is stored."""
    json_path = next(p for p in sorted((app_dir / "Controls").glob("*.json")) if _load(p)["TopParent"]["Name"] == "App")
    app = _load(json_path)
    rule = next(r for r in app["TopParent"]["Rules"] if r["Property"] == "Formulas")
    rule["InvariantScript"] = rule["InvariantScript"].rstrip() + "\n" + formula
    _save(json_path, app)
    yaml_path = app_dir / "Src" / "App.pa.yaml"
    lines = yaml_path.read_bytes().decode("utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == "Formulas: |-")
    indent = len(lines[start + 1]) - len(lines[start + 1].lstrip())
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) >= indent):
        end += 1
    lines.insert(end, " " * indent + formula)
    yaml_path.write_bytes("\n".join(lines).encode("utf-8"))


def add_sharepoint_list(app_dir, entry):
    """Add a SharePoint list to the app's data sources, and to the SharePoint connection's record of what it serves."""
    ds_path = app_dir / "References" / "DataSources.json"
    ds = _load(ds_path)
    if any(d["Name"] == entry["Name"] for d in ds["DataSources"]):
        raise ValueError(f"the app already has a data source named {entry['Name']}")
    ds["DataSources"].append(entry)
    _save(ds_path, ds)
    props_path = app_dir / "Properties.json"
    props = _load(props_path)
    refs = json.loads(props["LocalConnectionReferences"])
    sp = next(r for r in refs.values() if "shared_sharepointonline" in r["connectionInstanceId"])
    sp["dataSources"].append(entry["Name"])
    sp["datasets"].setdefault(entry["DatasetName"], {"dataSources": {}})["dataSources"][entry["Name"]] = {"tableName": entry["TableName"]}
    props["LocalConnectionReferences"] = json.dumps(refs, separators=(",", ":"))
    _save(props_path, props)


def _refs(app_dir):
    props = _load(app_dir / "Properties.json")
    return props, json.loads(props["LocalConnectionReferences"])


def _save_refs(app_dir, props, refs):
    props["LocalConnectionReferences"] = json.dumps(refs, separators=(",", ":"))
    _save(app_dir / "Properties.json", props)


def apply_hookup(app_dir, hookup):
    """Connect the weekly flow to the app, the way the editor does it (Power Automate pane > Add flow): the flow as a
    data source, its connection, and the connections the flow itself uses. `hookup` is what capture_hookup recorded."""
    ds_path = app_dir / "References" / "DataSources.json"
    ds = _load(ds_path)
    if any(d["Name"] == hookup["service"] for d in ds["DataSources"]):
        raise ValueError(f"the app already has a data source named {hookup['service']}")
    ds["DataSources"].insert(hookup["dataSourceIndex"], hookup["dataSource"])
    _save(ds_path, ds)
    props, refs = _refs(app_dir)
    refs.update(hookup["connections"])
    for key in ("dependents", "appActions"):
        for ref_id, added in hookup.get(key, {}).items():
            refs[ref_id][key] = refs[ref_id].get(key, []) + added
    _save_refs(app_dir, props, refs)


def remove_hookup(app_dir, hookup):
    """The reverse of apply_hookup, for remaking the template from an app that has its flow connected."""
    ds_path = app_dir / "References" / "DataSources.json"
    ds = _load(ds_path)
    if ds["DataSources"][hookup["dataSourceIndex"]] != hookup["dataSource"]:
        raise ValueError("the app's connection to the weekly flow is not the one recorded in settings.lock.json; capture it again")
    del ds["DataSources"][hookup["dataSourceIndex"]]
    _save(ds_path, ds)
    props, refs = _refs(app_dir)
    for ref_id in hookup["connections"]:
        del refs[ref_id]
    for key in ("dependents", "appActions"):
        for ref_id, added in hookup.get(key, {}).items():
            refs[ref_id][key] = [x for x in refs[ref_id][key] if x not in added]
    _save_refs(app_dir, props, refs)


def find_hookup(app_dir, plain_dir, flow_id):
    """What connecting the weekly flow added to an app: compare the app (from an export made after Add flow, Save and
    Publish) with the same app without the flow."""
    before = {d["Name"] for d in _load(plain_dir / "References" / "DataSources.json")["DataSources"]}
    after = _load(app_dir / "References" / "DataSources.json")["DataSources"]
    new = [(i, d) for i, d in enumerate(after) if d["Name"] not in before]
    if len(new) != 1 or new[0][1].get("Type") != "ServiceInfo" or new[0][1].get("FlowNameId") != flow_id:
        raise ValueError("expected the export to add exactly one thing to the app, the weekly flow in settings.lock.json. It adds: "
                         + (", ".join(f"{d['Name']} ({d.get('FlowNameId', d.get('Type'))})" for _, d in new) or "nothing")
                         + ". In the editor: Power Automate pane > Add flow > the weekly flow, then Save, Publish and export again.")
    (index, entry), (_, old), (_, now) = new[0], _refs(plain_dir), _refs(app_dir)
    hookup = {"service": entry["Name"], "dataSourceIndex": index, "dataSource": entry,
              "connections": {k: v for k, v in now.items() if k not in old}, "dependents": {}, "appActions": {}}
    for ref_id in old:
        for key in ("dependents", "appActions"):
            added = [x for x in now[ref_id].get(key, []) if x not in old[ref_id].get(key, [])]
            if added:
                hookup[key][ref_id] = added
    return hookup


def capture_hookup(export_zip, where):
    """Record how an organization's weekly flow is connected to its app, from an export made after the flow was added in
    the editor. Writes it to settings.lock.json (app.weeklyFlow) and keeps the export's package wrapper, which carries
    the flow, next to the settings. Returns the hookup."""
    import apppkg
    base = (Path(where) if Path(where).is_dir() else st.client_dir(where)).resolve()
    s, lock = st.load_dir(base)
    plain = json.loads(json.dumps(s))
    plain["weekly"]["runFromApp"] = False
    real = apppkg.CLIENTS
    with tempfile.TemporaryDirectory() as tmp:
        apppkg.CLIENTS = Path(tmp) / "clients"
        try:
            apppkg.pull(export_zip, "export")
        finally:
            apppkg.CLIENTS = real
        pulled = Path(tmp) / "clients" / "export"
        hookup = find_hookup(pulled / "app", stamp_settings(plain, lock, Path(tmp) / "plain"), lock["flows"]["weekly"])
        if not (base / "app").is_dir():          # a pulled client's wrapper comes in with its app (pull_app); anyone else keeps the export's
            if (base / "package").exists():
                shutil.rmtree(base / "package")
            shutil.copytree(pulled / "package", base / "package")
            shutil.copy(pulled / apppkg.LAYOUT, base / apppkg.LAYOUT)
    lock.setdefault("app", {})["weeklyFlow"] = hookup
    (base / "settings.lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    return hookup


def stamp(client, out_dir):
    """Write template/app into out_dir with the organization's values in place of the tokens.
    `client` is a client's short name, or a folder that holds settings.json and settings.lock.json."""
    base = Path(client) if Path(client).is_dir() else st.client_dir(client)
    return stamp_settings(*st.load_dir(base), out_dir, base=base)


def stamp_settings(s, lock, out_dir, base=None):
    """`base` is the organization's folder, where brand.logo is looked for; without it the icon stays the placeholder."""
    values = tokens(s, lock)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(TEMPLATE, out_dir)
    stamp_logo(s, base, out_dir)
    for p in _formula_files(out_dir):
        data = p.read_bytes()
        if TITLE_TOKEN.encode() in data:
            p.write_bytes(data.replace(TITLE_TOKEN.encode(), _as_written('"' + title_of(s) + '"', p)))
    # Features that add something to the app, before the tokens are filled in, so their tokens are filled in too.
    if s["approvers"]["source"] == "list":
        add_formula(out_dir, LIST_CHECK)
        add_sharepoint_list(out_dir, json.loads((FRAGMENTS / "approvers-list" / "datasource.json").read_text()))
    if s["weekly"].get("runFromApp"):
        apply_hookup(out_dir, lock["app"]["weeklyFlow"])
    for p in _text_files(out_dir):
        data = p.read_bytes()
        new = data
        for token, value in values.items():
            new = new.replace(token.encode(), _as_written(value, p))
        if p.relative_to(out_dir).as_posix() in NAME_FILES:
            new = new.replace(b"__MS_APP_NAME__", _as_written(s["org"]["appName"], p))
        if b"__MS_" in new:
            i = new.index(b"__MS_")
            raise ValueError(f"{p.relative_to(out_dir)}: unknown token {new[i:i + 40].decode(errors='replace')!r}")
        if new != data:
            p.write_bytes(new)
    return out_dir


def refresh(client):
    """Remake template/app from a client's app: copy it, then put a token wherever one of its own values appears."""
    s, lock = st.load(client)
    if s["approvers"]["source"] != "groups":
        raise ValueError("the template is made from an app whose approvers are groups")
    values = sorted(tokens(s, lock).items(), key=lambda x: -len(x[1]))          # longest first: a group check holds a group ID
    if len({v for _, v in values}) != len(values):
        raise ValueError("two settings have the same value, so the template could not tell them apart")
    src = st.client_dir(client) / "app"
    if TEMPLATE.exists():
        shutil.rmtree(TEMPLATE)
    shutil.copytree(src, TEMPLATE)
    if s["weekly"].get("runFromApp"):
        remove_hookup(TEMPLATE, lock["app"]["weeklyFlow"])
    icon_path(TEMPLATE).write_bytes(PLACEHOLDER_ICON.read_bytes())          # the organization's icon never goes in the template
    counts = {TITLE_TOKEN: 0}
    for p in _formula_files(TEMPLATE):
        data = p.read_bytes()
        written = _as_written('"' + title_of(s) + '"', p)
        if written in data:
            counts[TITLE_TOKEN] += data.count(written)
            p.write_bytes(data.replace(written, TITLE_TOKEN.encode()))
    for p in _text_files(TEMPLATE):
        data = p.read_bytes()
        new = data
        for token, value in values:
            written = _as_written(value, p)
            counts[token] = counts.get(token, 0) + new.count(written)
            new = new.replace(written, token.encode())
        if p.relative_to(TEMPLATE).as_posix() in NAME_FILES:
            quoted = b'"' + _as_written(s["org"]["appName"], p) + b'"'
            counts["__MS_APP_NAME__"] = counts.get("__MS_APP_NAME__", 0) + new.count(quoted)
            new = new.replace(quoted, b'"__MS_APP_NAME__"')
        if new != data:
            p.write_bytes(new)
    unused = sorted(k for k, n in counts.items() if not n)
    if unused:
        raise ValueError(f"{client}'s app never uses its own value for {', '.join(unused)}")
    leftover = [g for g in lock.get("groups", {}).values() if any(g.encode() in p.read_bytes() for p in _text_files(TEMPLATE))]
    if leftover:
        raise ValueError(f"{client}'s app uses an approver group outside the usual check: {', '.join(leftover)}")
    return counts


def package(where, out_zip, wrapper):
    """Build an importable package: the stamped app inside a package wrapper (the manifest and connection list of an
    export). The wrapper is the organization's own if it has one (kept by capture_hookup, and it carries the weekly
    flow); otherwise another client's, for an organization with no export yet, to import as a new app."""
    import apppkg
    import check_app
    home = Path(where) if Path(where).is_dir() else st.client_dir(where)
    s, lock = st.load_dir(home)
    source = home if (home / "package").is_dir() else (apppkg.client_dir(wrapper) if wrapper else WRAPPER)
    out_zip = Path(out_zip).resolve()
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    real = apppkg.CLIENTS
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "clients" / "build"
        stamp_settings(s, lock, base / "app")
        problems = check_app.check_dir(base / "app", s["org"]["short"])
        if problems:
            raise ValueError("the stamped app is not consistent:\n" + "\n".join(problems))
        shutil.copytree(source / "package", base / "package")
        shutil.copy(source / apppkg.LAYOUT, base / apppkg.LAYOUT)
        notes = []
        icon = icon_path(base / "app").read_bytes()
        for p in (base / "package").rglob("*logoSmallFile"):       # the tile and loading-screen picture: the app's icon, not the wrapper owner's
            if p.read_bytes() != icon:
                p.write_bytes(icon)
                notes.append("package: tile and loading picture replaced with the app's icon")
        if source != home:
            notes += apppkg.strip_flow(base)                       # a borrowed wrapper must not carry the lender's flow
        if (home / "flows").is_dir():
            shutil.copytree(home / "flows", base / "flows")       # the package's copy of a flow is brought up to date from here
        apppkg.CLIENTS = Path(tmp) / "clients"
        try:
            notes += apppkg.build("build", out_zip, app_name=s["org"]["appName"])
        finally:
            apppkg.CLIENTS = real
    return notes


def differences(a, b):
    """Files that differ between two app folders, as relative paths."""
    fa = {p.relative_to(a).as_posix() for p in Path(a).rglob("*") if p.is_file()}
    fb = {p.relative_to(b).as_posix() for p in Path(b).rglob("*") if p.is_file()}
    out = [f"only in the template build: {f}" for f in sorted(fa - fb)] + [f"only in the client's app: {f}" for f in sorted(fb - fa)]
    return out + [f"differs: {f}" for f in sorted(fa & fb) if (Path(a) / f).read_bytes() != (Path(b) / f).read_bytes()]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client", nargs="?")
    ap.add_argument("--out", metavar="DIR", help="write the stamped app here (default: compare with clients/<client>/app)")
    ap.add_argument("--refresh-from", metavar="CLIENT", help="remake template/app from this client's app")
    ap.add_argument("--package", metavar="ZIP", help="build an importable package here")
    ap.add_argument("--capture-hookup", metavar="ZIP", help="record how the weekly flow is connected to the app, from an export "
                    "made after Add flow, Save and Publish in the editor")
    ap.add_argument("--wrapper", metavar="CLIENT", help="with --package, for an organization with no wrapper of its own: borrow this client's instead of the template's")
    args = ap.parse_args()
    if args.refresh_from:
        counts = refresh(args.refresh_from)
        print(f"template/app remade from clients/{args.refresh_from}/app. Tokens placed:")
        for k, n in sorted(counts.items()):
            print(f"  {k}: {n}")
        return
    if not args.client:
        ap.error("give a client, or --refresh-from CLIENT")
    if args.capture_hookup:
        h = capture_hookup(args.capture_hookup, args.client)
        print(f"The weekly flow is connected to the app as {h['service']}, with {len(h['connections'])} connections. "
              "Recorded in settings.lock.json (app.weeklyFlow).")
        return
    if args.package:
        for n in package(args.client, args.package, args.wrapper):
            print(f"  {n}")
        own = ((Path(args.client) if Path(args.client).is_dir() else st.client_dir(args.client)) / "package").is_dir()
        print(f"Built {args.package}. " + (
            "Import it over the organization's app: set the app row and the flow row to Update. "
            "The import switches the flow off: turn it back on in Power Automate > My flows." if own else
            "Import it as a NEW app (Import setup: Create as new), not over an existing one."))
        return
    if args.out:
        print(f"Stamped app for {args.client} written to {stamp(args.client, args.out)}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        diffs = differences(stamp(args.client, Path(tmp) / "app"), st.client_dir(args.client) / "app")
    if not diffs:
        print(f"{args.client}: template + settings gives exactly clients/{args.client}/app (every file identical).")
        return
    print(f"{args.client}: template + settings differs from clients/{args.client}/app in {len(diffs)} files:")
    for d in diffs[:40]:
        print(f"  {d}")
    sys.exit(1)


if __name__ == "__main__":
    main()
