"""
Copy the proxy's files into the proxy folder and upload them to the App Service app. Only the files listed here go,
so cred.env and anything else in py never leave this machine. Run deploy_setup.py once first.

    py deploy.py <resource group> <app name>
"""

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(HERE, "..", "proxy")
FILES = ["proxy.py", "api_writer.py", "azure_writer.py", "postgres_writer.py", "writer_base.py", "requirements.txt"]


def az(*args):
    # az is a batch file on Windows, which only runs found by its full name
    command = [shutil.which("az") or "az", *args]
    print("  az " + " ".join(args))
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def missing_imports():
    """
    Our own modules the listed files import but the list leaves out, which the app would crash on at startup.
    """
    ours = {name[:-len(".py")] for name in os.listdir(HERE) if name.endswith(".py")}
    listed = {name[:-len(".py")] for name in FILES if name.endswith(".py")}
    imported = set()
    for name in listed:
        with open(os.path.join(HERE, name + ".py"), encoding="utf-8") as file:
            # only imports at the top run at startup, ones inside functions only when those are called
            for node in ast.parse(file.read()).body:
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
    return sorted((imported & ours) - listed)


def build():
    missing = missing_imports()
    if missing:
        raise SystemExit(f"  FILES is missing {', '.join(name + '.py' for name in missing)}")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    os.makedirs(BUILD_DIR)
    for name in FILES:
        shutil.copy2(os.path.join(HERE, name), BUILD_DIR)
    print(f"  {os.path.abspath(BUILD_DIR)}: {', '.join(FILES)}")


def main():
    parser = argparse.ArgumentParser(description="Upload the proxy to its App Service app.")
    parser.add_argument("group", help="the Azure resource group")
    parser.add_argument("app", help="the App Service app's name")
    args = parser.parse_args()

    build()
    with tempfile.TemporaryDirectory() as temp:
        archive = shutil.make_archive(os.path.join(temp, "proxy"), "zip", BUILD_DIR)
        try:
            az("webapp", "deploy", "--resource-group", args.group, "--name", args.app,
               "--src-path", archive, "--type", "zip")
        except FileNotFoundError:
            print("  az not found, install the Azure CLI and open a new terminal")
            return 2
        except subprocess.CalledProcessError as error:
            print(error.stderr.strip())
            return 1
    host = az("webapp", "show", "--resource-group", args.group, "--name", args.app,
              "--query", "defaultHostName", "--output", "tsv")
    print(f"  deployed, API_URL=https://{host}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
