"""Offline tests for the package tools (pull, build, check). Run: python3 tests/test_tools.py"""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import apppkg  # noqa: E402
import check_app  # noqa: E402

HEADER = "# comment line\n"


def screen_json(formula):
    return json.dumps({"TopParent": {
        "Name": "Screen1", "Template": {"Name": "screen"}, "Rules": [],
        "Children": [
            {"Name": "Button1", "Template": {"Name": "button"},
             "Rules": [{"Property": "OnSelect", "InvariantScript": formula},
                       {"Property": "Text", "InvariantScript": ""}], "Children": []},
            {"Name": "Gallery1", "Template": {"Name": "gallery"}, "Rules": [], "Children": [
                {"Name": "galleryTemplate1", "Template": {"Name": "galleryTemplate"},
                 "Rules": [{"Property": "OnSelect", "InvariantScript": "Navigate(Screen1)"}], "Children": []}]},
        ]}}, indent=2).encode()


def screen_yaml(formula):
    return (HEADER + "Screens:\n  Screen1:\n    Children:\n"
            "      - Button1:\n          Control: Button\n          Properties:\n"
            f"            OnSelect: ={formula}\n            Text: =\n"
            "      - Gallery1:\n          Control: Gallery\n          Properties:\n"
            "            OnSelect: =Navigate(Screen1)\n").encode()


def make_package(path, formula="Notify(\"hi\")", yaml_formula=None):
    msapp = io.BytesIO()
    with zipfile.ZipFile(msapp, "w") as m:
        m.writestr("Header.json", b'{"DocVersion":"1"}')
        m.writestr("Controls\\4.json", screen_json(formula))
        m.writestr("Src\\Screen1.pa.yaml", screen_yaml(yaml_formula or formula))
        m.writestr("Assets\\Images\\logo.png", b"\x89PNG fake")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Microsoft.PowerApps/apps/1/1.json", b"{}")
        z.writestr("Microsoft.PowerApps/apps/1/N1-document.msapp", msapp.getvalue())
        z.writestr("manifest.json", b'{"schema":"1.0"}')


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._clients = apppkg.CLIENTS
        apppkg.CLIENTS = self.root / "clients"
        self.pkg = self.root / "export.zip"

    def tearDown(self):
        apppkg.CLIENTS = self._clients
        self.tmp.cleanup()

    def test_round_trip_is_identical(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        out = self.root / "out.zip"
        self.assertEqual(apppkg.build("demo", out), [])
        self.assertEqual(apppkg.compare(self.pkg, out), [])
        with zipfile.ZipFile(out) as z, zipfile.ZipFile(io.BytesIO(z.read("Microsoft.PowerApps/apps/1/N1-document.msapp"))) as m:
            self.assertIn("Controls\\4.json", m.namelist())  # original backslash names kept

    def test_pull_uses_forward_slash_paths(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        self.assertTrue((apppkg.CLIENTS / "demo/app/Controls/4.json").exists())
        self.assertTrue((apppkg.CLIENTS / "demo/package/manifest.json").exists())

    def test_pull_replaces_previous_files(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        stale = apppkg.CLIENTS / "demo/app/Src/Old.pa.yaml"
        stale.write_text("x")
        apppkg.pull(self.pkg, "demo")
        self.assertFalse(stale.exists())

    def test_edit_is_carried_into_build(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        for rel, old, new in (("Controls/4.json", b"Notify", b"Launch"), ("Src/Screen1.pa.yaml", b"Notify", b"Launch")):
            p = apppkg.CLIENTS / "demo/app" / rel
            p.write_bytes(p.read_bytes().replace(old, new))
        self.assertEqual(check_app.check("demo"), [])
        out = self.root / "out.zip"
        apppkg.build("demo", out)
        self.assertEqual(apppkg.compare(self.pkg, out), ["app: Controls/4.json", "app: Src/Screen1.pa.yaml"])

    def test_new_and_removed_files_are_reported(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        (apppkg.CLIENTS / "demo/app/Src/New.pa.yaml").write_text(HEADER + "Screens: {}\n")
        (apppkg.CLIENTS / "demo/app/Assets/Images/logo.png").unlink()
        out = self.root / "out.zip"
        notes = apppkg.build("demo", out)
        self.assertIn("added to app: Src/New.pa.yaml", notes)
        self.assertIn("removed from app: Assets/Images/logo.png", notes)

    def test_check_catches_one_sided_edit(self):
        make_package(self.pkg, formula="Notify(\"a\")", yaml_formula="Notify(\"b\")")
        apppkg.pull(self.pkg, "demo")
        problems = check_app.check("demo")
        self.assertEqual(len(problems), 1)
        self.assertIn("Button1.OnSelect differs", problems[0])

    def test_check_catches_duplicate_control_ids(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        path = apppkg.CLIENTS / "demo/app/Controls/4.json"
        d = json.loads(path.read_text())
        d["TopParent"]["Children"][0]["ControlUniqueId"] = "7"
        d["TopParent"]["Children"][1]["ControlUniqueId"] = "7"
        path.write_text(json.dumps(d))
        self.assertTrue(any("duplicate ControlUniqueId 7" in p for p in check_app.check("demo")))

    def test_check_catches_searchitems_reading_another_list(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        path = apppkg.CLIENTS / "demo/app/Controls/4.json"
        d = json.loads(path.read_text())
        d["TopParent"]["Children"].append({"Name": "Combo1", "Template": {"Name": "combobox"}, "Children": [], "Rules": [
            {"Property": "Items", "InvariantScript": "Sort('New List', Title)"},
            {"Property": "SearchItems", "InvariantScript": "Choices('Old List'.Col, Combo1.SearchText)"}]})
        path.write_text(json.dumps(d))
        self.assertTrue(any("SearchItems reads 'Old List'" in p for p in check_app.check("demo")))

    def test_check_catches_lockable_outside_form_card(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        path = apppkg.CLIENTS / "demo/app/Controls/4.json"
        d = json.loads(path.read_text())
        state = {"InvariantPropertyName": "Visible", "IsLockable": True}
        d["TopParent"]["Children"][0]["ControlPropertyState"] = [state]
        d["TopParent"]["Children"].append({"Name": "Card1", "Template": {"Name": "typedDataCard"}, "Rules": [],
                                           "Children": [{"Name": "Combo1", "Template": {"Name": "combobox"}, "Rules": [],
                                                         "ControlPropertyState": [dict(state)], "Children": []}]})
        path.write_text(json.dumps(d))
        problems = check_app.check("demo")
        self.assertTrue(any("Button1: IsLockable outside a form card" in p for p in problems))
        self.assertFalse(any("Combo1" in p for p in problems))  # fine inside a card

    def test_check_catches_missing_children_list(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        path = apppkg.CLIENTS / "demo/app/Controls/4.json"
        d = json.loads(path.read_text())
        del d["TopParent"]["Children"][0]["Children"]
        path.write_text(json.dumps(d))
        self.assertTrue(any("Button1: no Children list" in p for p in check_app.check("demo")))

    def test_check_handles_empty_formula_and_gallery_template(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        self.assertEqual(check_app.check("demo"), [])

    def test_pull_msapp_replaces_only_the_app(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        msapp = self.root / "draft.msapp"
        with zipfile.ZipFile(msapp, "w") as m:
            m.writestr("Header.json", b'{"DocVersion":"2"}')
            m.writestr("Controls\\4.json", screen_json("Launch(1)"))
            m.writestr("Src\\Screen1.pa.yaml", screen_yaml("Launch(1)"))
        self.assertEqual(apppkg.pull_msapp(msapp, "demo"), 3)
        self.assertFalse((apppkg.CLIENTS / "demo/app/Assets/Images/logo.png").exists())
        self.assertTrue((apppkg.CLIENTS / "demo/package/manifest.json").exists())
        out = self.root / "out.zip"
        self.assertEqual(apppkg.build("demo", out), [])
        with zipfile.ZipFile(out) as z, zipfile.ZipFile(io.BytesIO(z.read("Microsoft.PowerApps/apps/1/N1-document.msapp"))) as m:
            self.assertEqual(m.read("Header.json"), b'{"DocVersion":"2"}')

    def test_build_syncs_connection_data_sources_into_package(self):
        make_package(self.pkg)
        apppkg.pull(self.pkg, "demo")
        pkg_json = apppkg.CLIENTS / "demo/package/Microsoft.PowerApps/apps/1/1.json"
        pkg_json.write_text(json.dumps({"appDefinitionTemplate": {"properties": {"connectionReferences": {
            "k1": {"displayName": "SharePoint", "dataSources": ["Old List"]}}}}}))
        (apppkg.CLIENTS / "demo/app/Properties.json").write_text(json.dumps({"LocalConnectionReferences": json.dumps({
            "k1": {"id": "k1", "dataSources": ["Old List", "New List"]}})}))
        out = self.root / "out.zip"
        notes = apppkg.build("demo", out)
        self.assertIn("package: SharePoint connection now lists New List", notes)
        with zipfile.ZipFile(out) as z:
            refs = json.loads(z.read("Microsoft.PowerApps/apps/1/1.json"))["appDefinitionTemplate"]["properties"]["connectionReferences"]
        self.assertEqual(refs["k1"]["dataSources"], ["Old List", "New List"])
        self.assertIn('"Old List"', pkg_json.read_text())  # the repo copy is left as exported

    def test_unsafe_paths_rejected(self):
        msapp = io.BytesIO()
        with zipfile.ZipFile(msapp, "w") as m:
            m.writestr("Header.json", b"{}")
        with zipfile.ZipFile(self.pkg, "w") as z:
            z.writestr("../evil.json", b"{}")
            z.writestr("a/N1-document.msapp", msapp.getvalue())
        with self.assertRaisesRegex(ValueError, "Unsafe path"):
            apppkg.pull(self.pkg, "demo")
        self.assertFalse((self.root / "evil.json").exists())


class PackagedFlow(unittest.TestCase):
    def test_packaged_flow_follows_the_repo_copy(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            flows = Path(tmp)
            (flows / "Send__abc.json").write_text(json.dumps({"name": "abc", "properties": {"definition": {"actions": {"New": {}}}}}))
            stale = json.dumps({"name": "abc", "properties": {"displayName": "Send", "definition": {"actions": {"Old": {}}, "metadata": {"m": 1}}}})
            notes = []
            out = json.loads(apppkg._sync_flow_definition("Microsoft.Flow/flows/x/definition.json", stale.encode(), flows, notes))
            self.assertEqual(out["properties"]["definition"], {"actions": {"New": {}}, "metadata": {"m": 1}})
            self.assertEqual(len(notes), 1)
            self.assertEqual(apppkg._sync_flow_definition("manifest.json", b"{}", flows, notes), b"{}")
            other = json.dumps({"name": "zzz", "properties": {"definition": {}}}).encode()
            with self.assertRaises(ValueError):
                apppkg._sync_flow_definition("Microsoft.Flow/flows/x/definition.json", other, flows, notes)


class Template(unittest.TestCase):
    """template/app plus an organization's settings gives that organization's app."""

    def test_template_is_consistent(self):
        import build_template
        self.assertEqual(check_app.check_dir(build_template.TEMPLATE, "template"), [])

    def test_example_organization_stamps_cleanly(self):
        import build_template
        import check_settings
        import settings
        example = settings.ROOT / "template" / "example"
        s, lock = settings.load_dir(example)
        self.assertEqual(check_settings.format_problems(s), [])
        with tempfile.TemporaryDirectory() as tmp:
            out = build_template.stamp(example, Path(tmp) / "app")
            self.assertEqual(check_app.check_dir(out, "example"), [])
            text = b"".join(p.read_bytes() for p in out.rglob("*") if p.suffix in (".json", ".yaml", ".sarif"))
        self.assertNotIn(b"__MS_", text)
        for value in build_template.tokens(s, lock).values():
            self.assertIn(value.encode(), text)
        self.assertIn(b'"Name": "EHT Submissions"', text)

    def test_header_text_can_be_read_on_any_header(self):
        import build_template as b
        self.assertEqual(b.header_text_color({"headerColor": "#BBDD8C"}), "#282828")       # light header, dark text
        self.assertEqual(b.header_text_color({"headerColor": "#2F6F5E"}), "#FFFFFF")       # dark header, white text
        self.assertEqual(b.header_text_color({"headerColor": "#2F6F5E", "headerTextColor": "#516C50"}), "#516C50")
        import settings
        with tempfile.TemporaryDirectory() as tmp:
            out = b.stamp(settings.ROOT / "template" / "example", Path(tmp) / "app")
            home = (out / "Src/LandingPage.pa.yaml").read_text()
        title = home[home.index("LblAppName1_1:"):]
        self.assertIn("Color: =RGBA(255, 255, 255, 1)", title[:title.index("Text: =")])

    def test_nothing_relies_on_a_yes_no_default(self):
        """SharePoint ignores a Yes/No default sent through Graph, so the app and the sample rows set the value."""
        import build_template
        import setup_lists
        self.assertTrue(all(r.get("Retired") is False for r in setup_lists.SAMPLE["projects"]))
        for screen, form in (("CreateFinancialThing", "FinancialThingForm"), ("CreateIncomeThing", "IncomeThingForm")):
            src = (build_template.TEMPLATE / "Src" / f"{screen}.pa.yaml").read_text()
            self.assertIn(f"=If(IsBlank({form}.LastSubmit.Processed), Patch('Financial Submissions', {form}.LastSubmit, {{Processed: false}})); Navigate(", src)

    def test_send_to_bookkeeper_is_in_the_template(self):
        """The green button and its confirm box are standard (D-12). What Send does is the one piece that differs."""
        import build_template as b
        import settings
        src = (b.TEMPLATE / "Src" / "FinancialApprovals.pa.yaml").read_text()
        for name in ("btnFASend:", "crdFASend:", "btnFASendGo:", 'Text: ="Send to bookkeeper"'):
            self.assertIn(name, src)
        self.assertNotIn("icon_processFinancialButton", src)
        self.assertIn('UpdateContext({locFAResult: IfError(__MS_WEEKLY_RUN__, "That did not work: " & FirstError.Message)})', src)
        s, lock = settings.load_dir(settings.ROOT / "template" / "example")
        self.assertFalse(s["weekly"]["runFromApp"])                            # not connected yet: Send opens the flow's page
        self.assertEqual(b.weekly_run(s, lock), 'If(Launch("https://make.powerautomate.com/environments/' + lock["environment"]
                         + "/flows/shared/" + lock["flows"]["weekly"] + '/details"), "The page of the weekly flow has opened. Press Run there.")')
        s["weekly"]["runFromApp"] = True                                       # switched on without the flow connected: stop
        with self.assertRaises(ValueError):
            b.tokens(s, lock)

    def test_package_names_the_apps_own_site(self):
        """A wrapper borrowed from another organization must not leave that organization's site in the package."""
        import apppkg
        wrapper = {"appDefinitionTemplate": {"properties": {"connectionReferences": {
            "k": {"displayName": "SharePoint", "dataSets": {"https://other.example/sites/Theirs": {}}}}}}}
        with tempfile.TemporaryDirectory() as tmp:
            mine = {"https://mine.example/sites/Ours": {"dataSources": {}}}
            (Path(tmp) / "Properties.json").write_text(json.dumps({"LocalConnectionReferences": json.dumps({"k": {"datasets": mine}})}))
            notes = []
            out = json.loads(apppkg._sync_datasets(json.dumps(wrapper).encode(), Path(tmp), notes))
            self.assertEqual(out["appDefinitionTemplate"]["properties"]["connectionReferences"]["k"]["dataSets"], mine)
            self.assertEqual(len(notes), 1)
            again = json.dumps(out).encode()
            self.assertEqual(apppkg._sync_datasets(again, Path(tmp), notes), again)       # already agrees: untouched

    def test_bank_deposit_saves_its_own_type(self):
        import build_template
        src = (build_template.TEMPLATE / "Src" / "CreateFinancialThing.pa.yaml").read_text()
        self.assertIn('If(gblDeposit, {Value: "Deposit"}, ', src)

    def test_a_missing_value_stops_the_build(self):
        import build_template
        import settings
        s, lock = settings.load_dir(settings.ROOT / "template" / "example")
        lock["groups"].pop("income")
        with self.assertRaisesRegex(ValueError, "__MS_IS_INCOME_APPROVER__"):
            build_template.tokens(s, lock)

    def test_approvers_from_a_list(self):
        import build_template
        import settings
        s, lock = settings.load_dir(settings.ROOT / "template" / "example")
        s["approvers"] = {"source": "list", "list": "MySubmissions Approvers"}
        s["sharepoint"]["lists"]["approvers"] = "MySubmissions Approvers"
        with self.assertRaisesRegex(ValueError, "__MS_LIST_APPROVERS__"):          # the list's ID has to be in the lock
            build_template.tokens(s, lock)
        lock["lists"]["approvers"] = "00000000-0000-4000-8000-000000000014"
        lock.pop("groups")                                                         # no groups needed
        with tempfile.TemporaryDirectory() as tmp:
            out = build_template.stamp_settings(s, lock, Path(tmp) / "app")
            self.assertEqual(check_app.check_dir(out, "example"), [])
            src = "".join(p.read_text() for p in (out / "Src").glob("*.pa.yaml"))
            app = json.loads(next(p for p in (out / "Controls").glob("*.json")
                                  if json.loads(p.read_text(encoding="utf-8-sig"))["TopParent"]["Name"] == "App").read_text(encoding="utf-8-sig"))
            sources = json.loads((out / "References/DataSources.json").read_text(encoding="utf-8-sig"))["DataSources"]
            refs = json.loads(json.loads((out / "Properties.json").read_text(encoding="utf-8-sig"))["LocalConnectionReferences"])
            everything = b"".join(p.read_bytes() for p in out.rglob("*") if p.suffix in (".json", ".yaml"))
        self.assertNotIn("ListGroupMembers(", src)
        self.assertNotIn(b"__MS_", everything)
        self.assertIn("OnVisible: =If(!(IsApprover), Notify(", src)
        self.assertIn("Visible: =IsApprover", src)
        formulas = next(r["InvariantScript"] for r in app["TopParent"]["Rules"] if r["Property"] == "Formulas")
        self.assertIn(build_template.LIST_CHECK, formulas)
        self.assertIn("      " + build_template.LIST_CHECK, src)
        entry = next(d for d in sources if d["Name"] == "Approvers")
        self.assertEqual((entry["DatasetName"], entry["TableName"]), (s["sharepoint"]["listsSite"], lock["lists"]["approvers"]))
        sp = next(r for r in refs.values() if "shared_sharepointonline" in r["connectionInstanceId"])
        self.assertIn("Approvers", sp["dataSources"])
        self.assertEqual(sp["datasets"][s["sharepoint"]["listsSite"]]["dataSources"]["Approvers"], {"tableName": lock["lists"]["approvers"]})

class SetupLists(unittest.TestCase):
    """The list setup tool makes lists the template app can read: the column names the template's data sources name."""

    class FakeSite:
        def __init__(self, lists=None):
            self.have = dict(lists or {})          # name -> {"id":..., "columns": [...]}
            self.calls = []

        def lists(self):
            return {n.lower(): l["id"] for n, l in self.have.items()}

        def _by_id(self, lid):
            return next(l for l in self.have.values() if l["id"] == lid)

        def call(self, method, path, body=None):
            self.calls.append((method, path))
            parts = path.strip("/").split("/")
            if method == "POST" and parts == ["lists"]:
                new = {"id": f"id-{len(self.have) + 1}", "columns": [{"id": "t", "name": "Title", "displayName": "Title"}]}
                self.have[body["displayName"]] = new
                return {"id": new["id"]}
            lst = self._by_id(parts[1])
            if method == "GET":
                return {"value": lst["columns"]}
            if method == "POST":
                lst["columns"].append({"id": body["name"], "name": body["name"], "displayName": body["displayName"], **body})
                return {}
            next(c for c in lst["columns"] if c["id"] == parts[3]).update(body)
            return {}

    def test_environment_comes_from_the_sign_in(self):
        """A new install's lock file gets the tenant's Default environment, read from the token, so the flow builder
        and the connection lender agree on where to create the flow (found on the fresh install walkthrough)."""
        import base64
        import setup_lists
        payload = base64.urlsafe_b64encode(json.dumps({"tid": "3f188048-0000-4000-8000-000000000001", "upn": "x"}).encode()).decode().rstrip("=")
        self.assertEqual(setup_lists.tenant_id(f"hdr.{payload}.sig"), "3f188048-0000-4000-8000-000000000001")

    def test_makes_what_is_missing_and_nothing_twice(self):
        import setup_lists
        site, said = self.FakeSite(), []
        ids, problems = setup_lists.ensure(site, {}, apply=False, say=said.append)
        self.assertEqual((ids, problems, [c for c in site.calls if c[0] != "GET"]), ({}, [], []))     # dry run changes nothing
        ids, problems = setup_lists.ensure(site, {"costAccounts": "Cost Accounts"}, apply=True, say=said.append)
        self.assertEqual(problems, [])
        self.assertEqual(sorted(site.have), ["Chargeable Projects", "Cost Accounts", "Financial Submissions", "Payment Reminders"])
        for key in setup_lists.SPECS:
            names = [c["name"] for c in site._by_id(ids[key])["columns"]]
            self.assertEqual(sorted(names), sorted(setup_lists.expected_names(key)))
        subs = {c["name"]: c for c in site._by_id(ids["submissions"])["columns"]}
        self.assertEqual(subs["Project"]["lookup"]["listId"], ids["projects"])
        self.assertEqual(subs["Title"]["displayName"], "Vendor")
        before = len([c for c in site.calls if c[0] != "GET"])
        again, problems = setup_lists.ensure(site, {"costAccounts": "Cost Accounts"}, apply=True, say=said.append)
        self.assertEqual((again, problems), (ids, []))
        self.assertEqual(len([c for c in site.calls if c[0] != "GET"]), before)                       # second run adds nothing

    def test_weekly_template_has_the_flows_columns(self):
        import build_weekly_flow
        import setup_lists
        from openpyxl import load_workbook
        d = json.loads(build_weekly_flow.TEMPLATE.read_text())["definition"]
        row = d["actions"]["Pull_out_attachments_and_prepare"]["actions"]["Compose_Excel_Row"]["inputs"]
        self.assertEqual(list(row), setup_lists.WEEKLY_COLUMNS)
        wb = load_workbook(io.BytesIO(setup_lists.weekly_template()))
        table = next(iter(wb.active.tables.values()))
        self.assertEqual(table.displayName, setup_lists.WEEKLY_TABLE)
        self.assertEqual([c.value for c in wb.active[1]], setup_lists.WEEKLY_COLUMNS)
        self.assertIn(f'"table": "{setup_lists.WEEKLY_TABLE}"', json.dumps(d))

    def test_weekly_flow_template(self):
        import build_weekly_flow as b
        import settings
        es, elock = settings.load_dir(settings.ROOT / "template" / "example")
        with self.assertRaisesRegex(ValueError, "__MS_EXCEL_DRIVE__"):                # the library's IDs have to be in the lock
            b.definition(es, elock)
        elock["graph"] = {"weeklyExcelSource": "sites/example,1,2", "weeklyDrive": "b!example"}
        es["weekly"].update(runFromApp=True, testMode=True)
        d = b.definition(es, elock)
        out = json.dumps(d)
        self.assertNotIn("__MS_", out)
        self.assertNotIn("sharepoint.com/sites/", out.replace("example.sharepoint.com/sites/", ""))     # no other organization's site
        self.assertEqual(d["triggers"]["manual"]["kind"], "PowerAppV2")
        self.assertIn("Reply_to_the_app", d["actions"])
        self.assertTrue(d["actions"]["Initialize_TestMode"]["inputs"]["variables"][0]["value"])
        self.assertIn("EHT Weekly Financials", d["actions"]["Send_an_email_(V2)"]["inputs"]["parameters"]["emailMessage/Subject"])


    def test_own_connections_are_picked_by_connector(self):
        """A new install's flow uses the person's own connections: one per connector the template needs, the person's own
        and the newest first; a missing connector stops it with a message that says what to make."""
        import build_weekly_flow as b
        def conn(name, api, who="me@example.org", when="2026-01-01", status="Connected"):
            return {"name": name, "properties": {"apiId": f"/providers/Microsoft.PowerApps/apis/{api}", "createdTime": when,
                                                 "createdBy": {"userPrincipalName": who}, "statuses": [{"status": status}]}}
        mine = [conn("sp-old", "shared_sharepointonline", when="2025-01-01"), conn("sp-new", "shared_sharepointonline"),
                conn("sp-theirs", "shared_sharepointonline", who="other@example.org", when="2026-06-01"),
                conn("xl", "shared_excelonlinebusiness"), conn("mail-broken", "shared_office365", status="Error"),
                conn("mail", "shared_office365", when="2024-01-01")]
        refs = b.pick_connections(mine, user="me@example.org")
        self.assertEqual(set(refs), set(json.loads(b.TEMPLATE.read_text())["connections"]))
        self.assertEqual({r["id"].rsplit("/", 1)[-1]: r["connectionName"] for r in refs.values()},
                         {"shared_sharepointonline": "sp-new", "shared_excelonlinebusiness": "xl", "shared_office365": "mail"})
        self.assertTrue(all(r["source"] == "Invoker" for r in refs.values()))
        with self.assertRaisesRegex(ValueError, "no connection for Excel Online \\(Business\\), Office 365 Outlook"):
            b.pick_connections([conn("sp", "shared_sharepointonline")])

    def test_package_for_a_settings_folder(self):
        import build_template
        import settings
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "example.zip"
            notes = build_template.package(settings.ROOT / "template" / "example", out, None)
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
                manifest_bytes = z.read("manifest.json")
                manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
                app_json = json.loads(z.read(next(n for n in names if n.startswith("Microsoft.PowerApps/apps/")
                                                  and n.endswith(".json") and "identity" not in n)).decode("utf-8-sig"))
                msapp = zipfile.ZipFile(io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(".msapp")))))
                props = json.loads(msapp.read("Properties.json").decode("utf-8-sig"))
                everything = b"".join(msapp.read(n) for n in msapp.namelist() if n.endswith((".json", ".yaml")))
        self.assertEqual(manifest["details"]["displayName"], "example")
        self.assertEqual(props["Name"], "EHT Submissions")
        self.assertIn("EHT Submissions", [r["details"]["displayName"] for r in manifest["resources"].values()])
        self.assertNotIn(b"__MS_", everything)
        # The template's wrapper carries no flow, no connections and no account; the package must not either.
        self.assertFalse([n for n in names if n.startswith("Microsoft.Flow/")], names)
        self.assertEqual([r["type"] for r in manifest["resources"].values() if r["type"] != "Microsoft.PowerApps/apis"], ["Microsoft.PowerApps/apps"])
        self.assertNotIn("@", json.dumps(manifest))
        self.assertEqual(manifest["details"]["creator"], "")
        refs = app_json["appDefinitionTemplate"]["properties"]["connectionReferences"]
        self.assertEqual(sorted(r["displayName"] for r in refs.values()), ["Office 365 Groups", "Power Automate Management", "SharePoint"])
        self.assertFalse(any("@" in n for n in notes), notes)


class AppEdit(unittest.TestCase):
    """tools/app_edit.py on a copy of the template app: rename and delete a screen, and refuse what would break the app."""

    def _copy(self, tmp):
        import shutil
        import build_template
        base = Path(tmp) / "app-edit"
        base.mkdir()
        shutil.copytree(build_template.TEMPLATE, base / "app")
        shutil.copy(build_template.WRAPPER / apppkg.LAYOUT, base / apppkg.LAYOUT)
        return base

    def test_rename_screen_touches_every_copy(self):
        import app_edit
        with tempfile.TemporaryDirectory() as tmp:
            base = self._copy(tmp)
            app_edit.rename_screen(base, "SuccessScreen", "DoneScreen")
            self.assertEqual(check_app.check_dir(base / "app", "template"), [])
            self.assertFalse(app_edit._mentions(base / "app", "SuccessScreen"))
            self.assertTrue((base / "app/Src/DoneScreen.pa.yaml").exists())
            self.assertIn("DoneScreen", app_edit._screen_order(base / "app")[3])
            layout = json.loads((base / apppkg.LAYOUT).read_text())["msapp_entries"]
            self.assertIn("Src\\DoneScreen.pa.yaml", layout)
            self.assertNotIn("Src\\SuccessScreen.pa.yaml", layout)
            self.assertIn("Navigate(DoneScreen", (base / "app/Src/CreateFinancialThing.pa.yaml").read_text())

    def test_delete_screen_refuses_one_in_use(self):
        import app_edit
        with tempfile.TemporaryDirectory() as tmp:
            base = self._copy(tmp)
            with self.assertRaises(ValueError):
                app_edit.delete_screen(base, "SuccessScreen")           # CreateFinancialThing navigates to it
            self.assertIn("SuccessScreen", app_edit.screens(base / "app"))
            self.assertEqual(check_app.check_dir(base / "app", "template"), [])

    def test_screens_are_numbered_in_editor_order(self):
        import app_edit
        import build_template
        app = build_template.TEMPLATE
        for i, name in enumerate(app_edit._screen_order(app)[3]):
            self.assertEqual(app_edit._load(app_edit.screens(app)[name])["TopParent"]["Index"], i, name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
