"""Shared code for moving a canvas app package (the .zip from Power Apps "Export package")
in and out of clients/<client>/.

Layout of clients/<client>/:
    package/       every file in the export zip except the app file, at its original path
    app/           the contents of the app file (.msapp), with forward-slash paths
    layout.json    original entry names and order, so the zip can be rebuilt exactly

Files are stored byte for byte. Only the zip container is rebuilt.
"""
import io
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
CLIENTS = ROOT / "clients"
LAYOUT = "layout.json"


def client_dir(client):
    return CLIENTS / client


def _is_msapp(name):
    return name.endswith(".msapp")


def _norm(name):
    """Zip entry name -> safe relative POSIX path (the app file uses backslashes)."""
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Unsafe path in package: {name}")
    return p.as_posix()


def pull(zip_path, client):
    """Unpack an export zip into clients/<client>/, replacing what was there."""
    dest = client_dir(client)
    with zipfile.ZipFile(zip_path) as z:
        entries = z.infolist()
        msapps = [e for e in entries if _is_msapp(e.filename)]
        if len(msapps) != 1:
            raise ValueError(f"Expected one .msapp in the package, found {len(msapps)}")
        msapp_entry = msapps[0].filename
        with zipfile.ZipFile(io.BytesIO(z.read(msapp_entry))) as m:
            app_entries = m.infolist()
            app_files = {_norm(e.filename): m.read(e.filename) for e in app_entries if not e.is_dir()}
            app_names = [e.filename for e in app_entries if not e.is_dir()]
        pkg_files = {_norm(e.filename): z.read(e.filename) for e in entries
                     if not e.is_dir() and e.filename != msapp_entry}
        pkg_names = [e.filename for e in entries if not e.is_dir()]

    for sub in ("package", "app"):
        if (dest / sub).exists():
            shutil.rmtree(dest / sub)
    for base, files in (("package", pkg_files), ("app", app_files)):
        for rel, data in files.items():
            out = dest / base / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    layout = {"package_entries": pkg_names, "msapp_entry": msapp_entry, "msapp_entries": app_names}
    (dest / LAYOUT).write_text(json.dumps(layout, indent=2) + "\n")
    return len(pkg_files), len(app_files)


def pull_msapp(msapp_path, client):
    """Replace only clients/<client>/app from a bare .msapp (Studio: File > Save as > This computer).
    The package wrapper from the last full export stays, so build() still works."""
    dest = client_dir(client)
    layout_path = dest / LAYOUT
    if not layout_path.exists():
        raise ValueError(f"clients/{client} has no package yet; pull a full export first")
    with zipfile.ZipFile(msapp_path) as m:
        entries = [e for e in m.infolist() if not e.is_dir()]
        files = {_norm(e.filename): m.read(e.filename) for e in entries}
        names = [e.filename for e in entries]
    if (dest / "app").exists():
        shutil.rmtree(dest / "app")
    for rel, data in files.items():
        out = dest / "app" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    layout = json.loads(layout_path.read_text())
    layout["msapp_entries"] = names
    layout_path.write_text(json.dumps(layout, indent=2) + "\n")
    return len(files)


def _files_under(base):
    return sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())


def _label_package(data, label):
    """Name the package after its file (what the import screen shows under Package details), not after the export."""
    bom = data.startswith(b"\xef\xbb\xbf")
    doc = json.loads(data.decode("utf-8-sig"))
    if not isinstance(doc.get("details"), dict):
        return data
    doc["details"]["displayName"] = label
    return (b"\xef\xbb\xbf" if bom else b"") + json.dumps(doc).encode("utf-8")


NAME_FILES = ("Properties.json", "Resources/PublishInfo.json")


def build(client, out_zip, app_name=None):
    """Rebuild an importable zip from clients/<client>/. Returns notes about added or removed files.
    app_name: the name to give the app inside the package (for a production build made from a test app's export)."""
    src = client_dir(client)
    layout = json.loads((src / LAYOUT).read_text())
    notes = []
    rename = None
    if app_name:
        # The name the export gave the app: the package's own record of it.
        manifest = json.loads((src / "package" / "manifest.json").read_text(encoding="utf-8-sig"))
        old_name = next(r["details"]["displayName"] for r in manifest["resources"].values() if r.get("type") == "Microsoft.PowerApps/apps")
        if app_name != old_name:
            rename = lambda data: data.replace(json.dumps(old_name).encode(), json.dumps(app_name).encode())
    if rename:
        notes.append(f"app named {app_name!r} in the package (was {old_name!r})")

    # The app file: original entries in order, then any new files (e.g. a new screen).
    app_base = src / "app"
    known = {_norm(n): n for n in layout["msapp_entries"]}
    app_buf = io.BytesIO()
    with zipfile.ZipFile(app_buf, "w", zipfile.ZIP_DEFLATED) as m:
        for name in layout["msapp_entries"]:
            path = app_base / _norm(name)
            if path.exists():
                data = path.read_bytes()
                m.writestr(name, rename(data) if rename and _norm(name) in NAME_FILES else data)
            else:
                notes.append(f"removed from app: {_norm(name)}")
        for rel in _files_under(app_base):
            if rel not in known:
                m.writestr(rel.replace("/", "\\"), (app_base / rel).read_bytes())
                notes.append(f"added to app: {rel}")

    pkg_base = src / "package"
    app_refs = _app_connection_refs(app_base)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for name in layout["package_entries"]:
            if name == layout["msapp_entry"]:
                z.writestr(name, app_buf.getvalue())
            else:
                data = (pkg_base / _norm(name)).read_bytes()
                data = _sync_connection_refs(data, app_refs, notes)
                data = _sync_datasets(data, app_base, notes)
                data = _sync_flow_definition(name, data, src / "flows", notes)
                if _norm(name) == "manifest.json":
                    data = _label_package(data, Path(out_zip).stem)
                if rename and (_norm(name) == "manifest.json" or _norm(name).startswith("Microsoft.PowerApps/apps/")) and name.endswith(".json"):
                    data = rename(data)
                z.writestr(name, data)
    return notes


def strip_flow(src):
    """Take the flow out of a package wrapper borrowed from another organization (its package/ and layout.json copied
    to `src`, next to the stamped app in src/app). An organization with no export of its own imports the stamped app
    as a new app and connects its weekly flow later, in the editor (see build_template --capture-hookup). So the
    borrowed wrapper must not carry the lender's flow, the connections the flow uses, or the lender's account that
    owns them. The stamped app's own Properties.json says which connections it really has; everything else goes.
    Returns notes about what was removed; nothing happens to a wrapper with no flow in it."""
    layout_path = src / LAYOUT
    layout = json.loads(layout_path.read_text())
    flow_entries = [n for n in layout["package_entries"] if _norm(n).startswith("Microsoft.Flow/")]
    if not flow_entries:
        return []
    for n in flow_entries:
        (src / "package" / _norm(n)).unlink()
    shutil.rmtree(src / "package" / "Microsoft.Flow", ignore_errors=True)
    layout["package_entries"] = [n for n in layout["package_entries"] if n not in flow_entries]
    layout_path.write_text(json.dumps(layout, indent=2) + "\n")

    # The app's record in the wrapper: keep only the connections the stamped app has.
    keep = set(_app_connection_refs(src / "app"))
    app_json = next(src / "package" / _norm(n) for n in layout["package_entries"]
                    if _norm(n).startswith("Microsoft.PowerApps/apps/") and n.endswith(".json")
                    and PurePosixPath(_norm(n)).stem == PurePosixPath(_norm(n)).parent.name)
    raw = app_json.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    doc = json.loads(raw.decode("utf-8-sig"))
    refs = doc["appDefinitionTemplate"]["properties"]["connectionReferences"]
    dropped = [refs[k].get("displayName", k) for k in list(refs) if k not in keep]
    for k in list(refs):
        if k not in keep:
            del refs[k]
    api_ids = {ref["id"] for ref in refs.values()}
    app_json.write_bytes((b"\xef\xbb\xbf" if bom else b"") + json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

    # The manifest: the flow goes, and with it everything the app no longer depends on.
    manifest_path = src / "package" / "manifest.json"
    raw = manifest_path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    manifest = json.loads(raw.decode("utf-8-sig"))
    resources = manifest["resources"]
    app_id = next(k for k, r in resources.items() if r.get("type") == "Microsoft.PowerApps/apps")
    resources[app_id]["dependsOn"] = [d for d in resources[app_id].get("dependsOn", []) if d in api_ids]
    reach, stack = set(), [app_id]
    while stack:
        k = stack.pop()
        if k in reach:
            continue
        reach.add(k)
        stack.extend(resources[k].get("dependsOn", []))
    removed = [r.get("details", {}).get("displayName", k) for k, r in resources.items() if k not in reach]
    manifest["resources"] = {k: r for k, r in resources.items() if k in reach}
    manifest_path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + json.dumps(manifest).encode("utf-8"))
    return [f"borrowed wrapper: left out the lender's flow and what only it used ({', '.join(removed)}); "
            f"the app keeps {len(refs)} connections" + (f", dropped {', '.join(dropped)}" if dropped else "")]


def _app_connection_refs(app_base):
    """{connection key: [data source names]} from the app's Properties.json."""
    path = app_base / "Properties.json"
    if not path.exists():
        return {}
    refs = json.loads(path.read_text(encoding="utf-8-sig")).get("LocalConnectionReferences") or {}
    refs = json.loads(refs) if isinstance(refs, str) else refs
    return {k: v.get("dataSources") or [] for k, v in refs.items()}


def _sync_connection_refs(data, app_refs, notes):
    """The package wrapper lists which data sources use each connection. It comes from the last
    full export, so data sources added in Studio since then are missing, and after import Power
    Apps reports them as "not working properly". Copy the app's current lists into the wrapper."""
    if not app_refs or b'"connectionReferences"' not in data:
        return data
    doc = json.loads(data.decode("utf-8-sig"))
    refs = (doc.get("appDefinitionTemplate") or {}).get("properties", {}).get("connectionReferences") or {}
    changed = False
    for key, ref in refs.items():
        current = app_refs.get(key)
        if current is not None and ref.get("dataSources") != current:
            added = sorted(set(current) - set(ref.get("dataSources") or []))
            ref["dataSources"] = current
            changed = True
            notes.append(f"package: {ref.get('displayName', key)} connection now lists {', '.join(added) or 'updated data sources'}")
    if not changed:
        return data
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sync_datasets(data, app_base, notes):
    """The wrapper also says which site and lists each connection reads. On import Power Apps copies that into the app,
    so a wrapper borrowed from another organization would leave that organization's site named in the new app. Copy the
    app's own record in. For a client's own wrapper the two already agree and nothing changes."""
    path = app_base / "Properties.json"
    if not path.exists() or b'"connectionReferences"' not in data:
        return data
    app_refs = json.loads(path.read_text(encoding="utf-8-sig")).get("LocalConnectionReferences") or {}
    app_refs = json.loads(app_refs) if isinstance(app_refs, str) else app_refs
    doc = json.loads(data.decode("utf-8-sig"))
    refs = (doc.get("appDefinitionTemplate") or {}).get("properties", {}).get("connectionReferences") or {}
    changed = False
    for key, ref in refs.items():
        current = (app_refs.get(key) or {}).get("datasets")
        if current is not None and "dataSets" in ref and ref["dataSets"] != current:
            ref["dataSets"] = current
            changed = True
            notes.append(f"package: {ref.get('displayName', key)} connection now names the app's own site and lists")
    if not changed:
        return data
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sync_flow_definition(name, data, flows_dir, notes):
    """A package for an app that runs a flow carries a copy of the flow, and importing with Update writes that copy
    over the live flow. Put the repo's current definition in (clients/<client>/flows, kept in step with the tenant by
    the flow tools), so an import can never roll the flow back to how it was at the last export."""
    norm = _norm(name)
    if "Microsoft.Flow/flows/" not in norm or not norm.endswith("/definition.json"):
        return data
    doc = json.loads(data.decode("utf-8-sig"))
    repo = next((json.loads(f.read_text()) for f in sorted(flows_dir.glob("*__*.json"))
                 if json.loads(f.read_text()).get("name") == doc.get("name")), None)
    title = doc.get("properties", {}).get("displayName", doc.get("name"))
    if repo is None:
        raise ValueError(f"The package carries the flow {title!r}, but clients/.../flows has no copy of it. Pull flows first.")
    current = dict(repo["properties"]["definition"])
    if "metadata" in doc["properties"]["definition"]:
        current.setdefault("metadata", doc["properties"]["definition"]["metadata"])
    if current == doc["properties"]["definition"]:
        return data
    doc["properties"]["definition"] = current
    notes.append(f"package: flow {title!r} definition refreshed from clients/.../flows")
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compare(zip_a, zip_b):
    """List the differences between two packages, entry by entry (including inside the app file)."""
    diffs = []
    with zipfile.ZipFile(zip_a) as a, zipfile.ZipFile(zip_b) as b:
        if a.namelist() != b.namelist():
            diffs.append("package entries differ")
        for name in set(a.namelist()) & set(b.namelist()):
            x, y = a.read(name), b.read(name)
            if _is_msapp(name):
                with zipfile.ZipFile(io.BytesIO(x)) as ma, zipfile.ZipFile(io.BytesIO(y)) as mb:
                    if ma.namelist() != mb.namelist():
                        diffs.append("app file entries differ")
                    for n in set(ma.namelist()) & set(mb.namelist()):
                        if ma.read(n) != mb.read(n):
                            diffs.append(f"app: {_norm(n)}")
            elif x != y:
                diffs.append(f"package: {name}")
    return sorted(diffs)
