"""Bring an app export into the repo: unpack it into clients/<client>/.

Usage:
    python3 tools/pull_app.py eht ~/Downloads/EHT-MySubs_20261110.zip   full package
    python3 tools/pull_app.py eht ~/Downloads/My Submissions.msapp       app file only

A package comes from Power Apps: Apps > ... > Export package. It contains the *published*
version. To bring in a saved but unpublished draft, use Studio: File > Save as > This
computer, which downloads an .msapp; that replaces only the app folder. This replaces clients/<client>/app
and clients/<client>/package, so afterwards `git diff` shows exactly what changed since the
last pull. Run it whenever someone has edited the app in Power Apps Studio.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apppkg import pull, pull_msapp  # noqa: E402
from check_app import check  # noqa: E402


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client, zip_path = sys.argv[1], Path(sys.argv[2]).expanduser()
    if zip_path.suffix.lower() == ".msapp":
        n_app = pull_msapp(zip_path, client)
        print(f"Pulled {zip_path.name} into clients/{client}/app: {n_app} app files (package unchanged)")
    else:
        n_pkg, n_app = pull(zip_path, client)
        print(f"Pulled {zip_path.name} into clients/{client}: {n_app} app files, {n_pkg} package files")
    problems = check(client)
    print("Check: OK" if not problems else "Check:\n" + "\n".join(problems))
    print(f"Next: git diff -- clients/{client}")


if __name__ == "__main__":
    main()
