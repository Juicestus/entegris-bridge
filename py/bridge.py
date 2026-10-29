"""
The bridge: collect from every pump in the pump table, each as often as its frequency says, until stopped.

    python bridge.py [--config pumps.csv] [--out out] [--save {0,1,2}] [--env cred.env] [--nopush] [--once]

Everything goes to files under --out, and rows and profiles also go to the cloud database and bucket the env file
names unless --nopush is given. Default paths are the files next to this script, so it runs with no arguments.
"""

import argparse
import os
import sys
import time
from dataclasses import asdict

import file_writer
from extract import SAVE_CAPTURE, SAVE_COMMS, SAVE_OUTPUTS, Options, extract_pump
from file_writer import FileDBWriter, write_json
from pump_config import PumpTable

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "pumps.csv")
DEFAULT_OUT = os.path.join(HERE, "out")
DEFAULT_ENV = os.path.join(HERE, "cred.env")


def run_pumps(pumps, due, options):
    """
    Collect from the pumps that are due, a bus at a time, and record the run in run.json.
    """
    results = []
    for group in pumps.bus_groups():
        for config in group:
            if config in due:
                results.append(extract_pump(config, options))

    run_dir = file_writer.run_dir(options.out, options.run_time)
    os.makedirs(run_dir, exist_ok=True)
    write_json(os.path.join(run_dir, file_writer.RUN_FILE_NAME),
               {"run_time": options.run_time, "save": options.save, "pumps": [asdict(r) for r in results]})

    failed = [result for result in results if result.error is not None]
    print(f"\n  {len(results) - len(failed)} of {len(results)} pumps done")
    print(f"  {os.path.join(run_dir, file_writer.RUN_FILE_NAME)}")
    return results


def main():
    parser = argparse.ArgumentParser(description="Collect dispense profiles from the pumps in the pump table.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="CSV of address,frequency_s,name")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where outputs go")
    parser.add_argument("--save", type=int, choices=[SAVE_OUTPUTS, SAVE_CAPTURE, SAVE_COMMS], default=SAVE_CAPTURE,
                        help="0 outputs only, 1 also raw captures, 2 also every NCS request and reply")
    parser.add_argument("--env", default=DEFAULT_ENV, help="env file with the cloud database and bucket details")
    parser.add_argument("--nopush", action="store_true", help="files only, nothing sent to the cloud")
    parser.add_argument("--once", action="store_true", help="collect from every pump once and exit")
    args = parser.parse_args()

    pumps = PumpTable()
    try:
        pumps.load_from_csv(args.config)
    except (OSError, ValueError) as error:
        print(f"  config: {error}")
        return 2
    writers = [FileDBWriter(args.out)]
    if not args.nopush:
        try:
            # imported only here, so a files only run doesn't need the cloud packages installed
            from gcp_writer import GCPConfig, GCPConnection, GCPDBWriter
            writers.append(GCPDBWriter(GCPConnection(GCPConfig.from_env(args.env))))
        except (ImportError, OSError, KeyError) as error:
            print(f"  env: {type(error).__name__}: {error}")
            print("  --nopush to run with files only")
            return 2

    # every pump is due straight away, then again its frequency after each time it started
    next_due = {config.address: time.time() for config in pumps.configs}
    try:
        while True:
            now = time.time()
            due = [config for config in pumps.configs if next_due[config.address] <= now]
            options = Options(args.out, args.save, time.strftime(file_writer.RUN_TIME_FORMAT), writers)
            print(f"\n{options.run_time}")
            results = run_pumps(pumps, due, options)
            if args.once:
                return 1 if any(result.error is not None for result in results) else 0

            # a run that took longer than a pump's frequency makes it due now, not several times over
            for config in due:
                next_due[config.address] = max(now + config.frequency_s, time.time())
            wait = max(min(next_due.values()) - time.time(), 0)
            print(f"  next in {wait:.0f}s")
            time.sleep(wait)
    except KeyboardInterrupt:
        print("\n  stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
