"""
Create the App Service app the proxy runs on and give it the settings from cred.env. Only needed once, running it
again brings the settings up to date. Then deploy.py uploads the code.

    py deploy_setup.py <resource group> <app name> [cred.env]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

from dotenv import dotenv_values

from deploy import az

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(HERE, "cred.env")
SKU = "B1"
RUNTIME = "PYTHON:3.12"
STARTUP = "gunicorn --bind=0.0.0.0 --timeout 600 proxy:app"
# what the proxy reads, API_URL is left out since it's the bridge's way to the proxy
SETTINGS = ["DATABASE_URL", "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_CONTAINER", "BLOB_PREFIX", "API_KEY"]
# makes App Service install requirements.txt from the upload
BUILD_SETTING = {"SCM_DO_BUILD_DURING_DEPLOYMENT": "true"}


def main():
    parser = argparse.ArgumentParser(description="Create the proxy's App Service app.")
    parser.add_argument("group", help="the Azure resource group, which must exist already")
    parser.add_argument("app", help="the app's name, part of its address so it has to be unique across Azure")
    parser.add_argument("env", nargs="?", default=DEFAULT_ENV, help="env file to take the settings from")
    args = parser.parse_args()

    env = dotenv_values(args.env)
    settings = {name: env[name] for name in SETTINGS if env.get(name)}
    missing = [name for name in SETTINGS if name not in settings and name != "BLOB_PREFIX"]
    if missing:
        print(f"  {args.env} is missing {', '.join(missing)}")
        return 2

    plan = f"{args.app}-plan"
    common = ["--resource-group", args.group, "--name", args.app]
    try:
        az("account", "show")
        az("appservice", "plan", "create", "--resource-group", args.group, "--name", plan, "--is-linux", "--sku", SKU)
        az("webapp", "create", "--resource-group", args.group, "--plan", plan, "--name", args.app,
           "--runtime", RUNTIME, "--startup-file", STARTUP)
        # the key travels in a header, so plain http is never allowed. always on stops the app sleeping between runs
        az("webapp", "update", *common, "--https-only", "true")
        az("webapp", "config", "set", *common, "--always-on", "true")

        # through a file, so the secrets aren't on a command line other programs can see
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "settings.json")
            with open(path, "w") as file:
                json.dump({**settings, **BUILD_SETTING}, file)
            az("webapp", "config", "appsettings", "set", *common, "--settings", f"@{path}", "--output", "none")
    except FileNotFoundError:
        print("  az not found, install the Azure CLI and open a new terminal")
        return 2
    except subprocess.CalledProcessError as error:
        print(error.stderr.strip())
        if "az login" in error.stderr:
            print("  run az login first")
        return 1

    print(f"  set {', '.join(settings)}")
    print(f"  ready, now py deploy.py {args.group} {args.app}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
