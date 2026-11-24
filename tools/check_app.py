"""Check that a client's app is consistent before packaging.

The app file stores every formula twice: in Controls/*.json (what Power Apps loads) and in
Src/*.pa.yaml (the readable copy). When we edit by hand, both must change together. This
compares every property that appears in both and reports any that differ.

Usage:
    python3 tools/check_app.py eht        one client
    python3 tools/check_app.py            every client under clients/
"""
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apppkg import CLIENTS, client_dir  # noqa: E402


class _Loader(yaml.SafeLoader):
    """A bare "=" (an empty formula) is a special 'value' tag in YAML 1.1; read it as text."""


_Loader.add_constructor("tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node))


def _clean(script):
    return script.replace("\r\n", "\n").strip()


def json_rules(app_dir):
    """{(control, property): script} from Controls/*.json."""
    rules = {}

    def walk(node, parent=None):
        name = node.get("Name")
        # A gallery's item template is a hidden child in Controls; Src shows its
        # properties (such as OnSelect) on the gallery itself.
        if (node.get("Template") or {}).get("Name") == "galleryTemplate" and parent:
            name = parent
        for r in node.get("Rules", []):
            rules.setdefault((name, r["Property"]), _clean(r.get("InvariantScript", "")))
        # A control inside a layout container keeps its layout properties (FillPortions,
        # LayoutMinHeight...) in DynamicProperties instead of Rules.
        for d in node.get("DynamicProperties") or []:
            r = d.get("Rule") or {}
            if r.get("Property"):
                rules.setdefault((name, r["Property"]), _clean(r.get("InvariantScript", "")))
        for child in node.get("Children", []):
            walk(child, node.get("Name"))

    for f in sorted((app_dir / "Controls").glob("*.json")):
        walk(json.loads(f.read_text(encoding="utf-8-sig"))["TopParent"])
    return rules


def yaml_rules(app_dir):
    """{(control, property): script} from Src/*.pa.yaml."""
    rules = {}

    def visit(name, node):
        node = node or {}
        for prop, value in (node.get("Properties") or {}).items():
            text = value if isinstance(value, str) else str(value)
            rules[(name, prop)] = _clean(text[1:] if text.startswith("=") else text)
        for child in node.get("Children") or []:
            for child_name, child_node in child.items():
                visit(child_name, child_node)

    for f in sorted((app_dir / "Src").glob("*.pa.yaml")):
        data = yaml.load(f.read_text(encoding="utf-8-sig"), Loader=_Loader) or {}
        if "App" in data:
            visit("App", data["App"])
        for key in ("Screens", "ComponentDefinitions"):
            for name, node in (data.get(key) or {}).items():
                visit(name, node)
    return rules


def structure_problems(app_dir):
    """Every control needs a unique name and a unique ControlUniqueId across the whole app."""
    names, ids, problems = {}, {}, []

    def walk(node, parent, screen, in_card=False):
        name, uid = node.get("Name"), node.get("ControlUniqueId")
        in_card = in_card or (node.get("Template") or {}).get("Name") == "typedDataCard"
        # Studio only marks properties lockable inside form cards; elsewhere it won't open the app.
        locked = [s["InvariantPropertyName"] for s in node.get("ControlPropertyState", [])
                  if isinstance(s, dict) and s.get("IsLockable")]
        if locked and not in_card:
            problems.append(f"{name}: IsLockable outside a form card ({', '.join(locked)})")
        if not isinstance(node.get("Children"), list):
            problems.append(f"{name}: no Children list (Studio needs one, even if empty)")
        if name in names:
            problems.append(f"duplicate control name {name!r} ({names[name]} and {screen})")
        names[name] = screen
        if uid is not None:
            if uid in ids:
                problems.append(f"duplicate ControlUniqueId {uid} ({ids[uid]} and {name})")
            ids[uid] = name
        if parent and node.get("Parent") not in (None, "", parent):
            problems.append(f"{name}: Parent is {node.get('Parent')!r}, expected {parent!r}")
        for child in node.get("Children", []):
            walk(child, name, screen, in_card)

    for f in sorted((app_dir / "Controls").glob("*.json")):
        top = json.loads(f.read_text(encoding="utf-8-sig"))["TopParent"]
        walk(top, None, top.get("Name"))
    return problems + search_items_problems(app_dir)


def _first_source(script):
    """The first data source a formula reads: the first 'quoted name' in it."""
    m = re.search(r"'([^']+)'", script or "")
    return m.group(1) if m else None


def search_items_problems(app_dir):
    """A searchable combo box shows SearchItems, not Items, so both must read the same list.
    SearchItems lives only in Controls/*.json, so the Src comparison can't catch this."""
    problems = []

    def walk(node, screen):
        if (node.get("Template") or {}).get("Name") == "combobox":
            rules = {r["Property"]: r.get("InvariantScript", "") for r in node.get("Rules", [])}
            items, search = _first_source(rules.get("Items")), _first_source(rules.get("SearchItems"))
            if items and search and items != search:
                problems.append(f"{screen}/{node['Name']}: Items reads '{items}' but SearchItems reads '{search}'")
        for child in node.get("Children", []):
            walk(child, screen)

    for f in sorted((app_dir / "Controls").glob("*.json")):
        top = json.loads(f.read_text(encoding="utf-8-sig"))["TopParent"]
        # A screen has no Parent: a screen property that says Parent. is an error, and a bad Height leaves the page blank.
        for r in top.get("Rules", []):
            if "Parent." in r.get("InvariantScript", ""):
                problems.append(f"{top.get('Name')}.{r['Property']} uses Parent, which a screen doesn't have (use Self)")
        walk(top, top.get("Name"))
    return problems


def check(client):
    return check_dir(client_dir(client) / "app", client)


def check_dir(app_dir, client):
    """Check any app folder (a client's, the template, or a stamped build); `client` is the label on each problem."""
    app_dir = Path(app_dir)
    if not app_dir.exists():
        return [f"{client}: no app folder"]
    js, ys = json_rules(app_dir), yaml_rules(app_dir)
    problems = [f"{client}: {p}" for p in structure_problems(app_dir)]
    for key in sorted(set(js) & set(ys)):
        if js[key] != ys[key]:
            problems.append(f"{client}: {key[0]}.{key[1]} differs\n"
                            f"    Controls: {js[key][:160]}\n    Src:      {ys[key][:160]}")
    for key in sorted(set(ys) - set(js)):
        problems.append(f"{client}: {key[0]}.{key[1]} is in Src but not in Controls")
    return problems


def main():
    clients = sys.argv[1:] or sorted(p.name for p in CLIENTS.iterdir() if (p / "app").exists())
    problems = []
    for c in clients:
        found = check(c)
        problems += found
        print(f"{c}: {'OK' if not found else f'{len(found)} problem(s)'}")
    for p in problems:
        print(p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
