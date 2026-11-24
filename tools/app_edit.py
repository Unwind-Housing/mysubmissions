"""Structural edits to an unpacked canvas app (clients/<client>/app, with its layout.json): delete a screen, rename a
screen, drop images the app no longer uses, drop a file. Every edit keeps the two copies of the app in step (Controls/*.json
and Src/*.pa.yaml), the editor's screen order (Src/_EditorState.pa.yaml) and the package layout (layout.json), so
tools/check_app.py and tools/build_package.py still work afterwards.

Usage (from the repo root, .venv active):
    python3 tools/app_edit.py eht delete-screen LandingPage_legacy
    python3 tools/app_edit.py eht rename-screen DonationSuccessScreen IncomeSuccessScreen
    python3 tools/app_edit.py eht remove-images "Old Logo" "Old Logo@4x-100"
    python3 tools/app_edit.py eht remove-file AppCheckerResult.sarif
    python3 tools/app_edit.py eht renumber-screens

Formulas are not touched except where a renamed screen is named in one (Navigate(OldName) becomes Navigate(NewName)).
Run tools/check_app.py afterwards. Nothing here signs in or touches a tenant.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apppkg  # noqa: E402

EDITOR_STATE = "Src/_EditorState.pa.yaml"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _save(path, doc):
    # Power Apps' own layout: two-space indent, ASCII escapes, Windows line ends, no BOM. Round-trips byte for byte.
    path.write_bytes(json.dumps(doc, indent=2, ensure_ascii=True).replace("\n", "\r\n").encode("utf-8"))


def _layout(base):
    return base / apppkg.LAYOUT


def _layout_remove(base, rel):
    """Take one app file (forward-slash path) out of layout.json's msapp_entries."""
    path = _layout(base)
    layout = json.loads(path.read_text())
    entry = rel.replace("/", "\\")
    if entry in layout["msapp_entries"]:
        layout["msapp_entries"].remove(entry)
        path.write_text(json.dumps(layout, indent=2) + "\n")
        return True
    return False


def _layout_rename(base, old_rel, new_rel):
    path = _layout(base)
    layout = json.loads(path.read_text())
    old, new = old_rel.replace("/", "\\"), new_rel.replace("/", "\\")
    layout["msapp_entries"] = [new if e == old else e for e in layout["msapp_entries"]]
    path.write_text(json.dumps(layout, indent=2) + "\n")


def screens(app):
    """{screen name: Controls/N.json path}."""
    out = {}
    for p in sorted((app / "Controls").glob("*.json")):
        top = _load(p)["TopParent"]
        if top["Template"]["Name"] == "screen":
            out[top["Name"]] = p
    return out


def _screen_order(app):
    lines = (app / EDITOR_STATE).read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == "ScreensOrder:")
    names = []
    i = start + 1
    while i < len(lines) and lines[i].startswith("    - "):
        names.append(lines[i].strip()[2:])
        i += 1
    return lines, start + 1, i, names


def _write_screen_order(app, lines, first, end, names):
    lines[first:end] = [f"    - {n}" for n in names]
    (app / EDITOR_STATE).write_text("\n".join(lines), encoding="utf-8")


def _text_files(app):
    return [p for p in sorted(app.rglob("*")) if p.is_file() and p.suffix.lower() in (".json", ".yaml", ".sarif")]


def _mentions(app, name, skip=()):
    pat = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(name.encode()) + rb"(?![A-Za-z0-9_])")
    return [p.relative_to(app).as_posix() for p in _text_files(app)
            if p.relative_to(app).as_posix() not in skip and pat.search(p.read_bytes())]


def delete_screen(base, name):
    """Delete a screen nobody navigates to. Refuses if any other file still names it."""
    app = base / "app"
    path = screens(app).get(name)
    if not path:
        raise ValueError(f"no screen named {name}")
    own = {path.relative_to(app).as_posix(), f"Src/{name}.pa.yaml", EDITOR_STATE.replace("Src/", "Src/"), "AppCheckerResult.sarif"}
    used = _mentions(app, name, skip=own)
    if used:
        raise ValueError(f"{name} is still named in {', '.join(used)}; it cannot be deleted")
    path.unlink()
    (app / "Src" / f"{name}.pa.yaml").unlink()
    _layout_remove(base, path.relative_to(app).as_posix())
    _layout_remove(base, f"Src/{name}.pa.yaml")
    lines, first, end, names = _screen_order(app)
    _write_screen_order(app, lines, first, end, [n for n in names if n != name])
    return f"deleted screen {name} ({path.name}, Src/{name}.pa.yaml)"


def rename_screen(base, old, new):
    """Rename a screen everywhere: its own files, every formula that names it, the editor's screen order, the layout."""
    app = base / "app"
    by_name = screens(app)
    if old not in by_name:
        raise ValueError(f"no screen named {old}")
    if new in by_name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new):
        raise ValueError(f"{new} is taken or is not a plain name")
    pat = re.compile(rb"(?<![A-Za-z0-9_])" + re.escape(old.encode()) + rb"(?![A-Za-z0-9_])")
    touched = []
    for p in _text_files(app):
        data = p.read_bytes()
        n = len(pat.findall(data))
        if n:
            p.write_bytes(pat.sub(new.encode(), data))
            touched.append(f"{p.relative_to(app).as_posix()} ({n})")
    (app / "Src" / f"{old}.pa.yaml").rename(app / "Src" / f"{new}.pa.yaml")
    _layout_rename(base, f"Src/{old}.pa.yaml", f"Src/{new}.pa.yaml")
    return f"renamed screen {old} to {new}: " + ", ".join(touched)


def remove_images(base, names):
    """Drop images from the app's resources, with their files. Refuses if a formula still uses one."""
    app = base / "app"
    res_path = app / "References" / "Resources.json"
    res = _load(res_path)
    by_name = {r["Name"]: r for r in res["Resources"]}
    out = []
    for name in names:
        r = by_name.get(name)
        if not r:
            raise ValueError(f"no image named {name!r} in the app")
        used = _mentions(app, name, skip=("References/Resources.json", "AppCheckerResult.sarif"))
        used = [u for u in used if u.startswith(("Src/", "Controls/"))]
        if used:
            raise ValueError(f"{name!r} is still used in {', '.join(used)}")
        rel = r["Path"].replace("\\", "/")
        if (app / rel).exists():
            (app / rel).unlink()
        _layout_remove(base, rel)
        res["Resources"].remove(r)
        out.append(f"removed image {name!r} ({rel})")
    _save(res_path, res)
    return "\n".join(out)


def remove_file(base, rel):
    app = base / "app"
    p = app / rel
    if not p.exists():
        raise ValueError(f"no file {rel}")
    p.unlink()
    _layout_remove(base, rel)
    return f"removed {rel}"


def renumber_screens(base):
    """Give the screens Index 0, 1, 2... in the editor's order, closing the gap a deleted screen leaves."""
    app = base / "app"
    _, _, _, names = _screen_order(app)
    by_name = screens(app)
    changed = []
    for i, name in enumerate(names):
        doc = _load(by_name[name])
        if doc["TopParent"].get("Index") != i:
            doc["TopParent"]["Index"] = i
            _save(by_name[name], doc)
            changed.append(f"{name}={i}")
    return "renumbered: " + (", ".join(changed) or "nothing to change")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client", help="client short name, or a folder with app/ and layout.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("delete-screen").add_argument("name")
    r = sub.add_parser("rename-screen")
    r.add_argument("old")
    r.add_argument("new")
    sub.add_parser("remove-images").add_argument("names", nargs="+")
    sub.add_parser("remove-file").add_argument("rel")
    sub.add_parser("renumber-screens")
    a = ap.parse_args()
    base = Path(a.client) if Path(a.client).is_dir() else apppkg.client_dir(a.client)
    if a.cmd == "delete-screen":
        print(delete_screen(base, a.name))
    elif a.cmd == "rename-screen":
        print(rename_screen(base, a.old, a.new))
    elif a.cmd == "remove-images":
        print(remove_images(base, a.names))
    elif a.cmd == "remove-file":
        print(remove_file(base, a.rel))
    elif a.cmd == "renumber-screens":
        print(renumber_screens(base))
    print("Next: python3 tools/check_app.py", base.name)


if __name__ == "__main__":
    main()
