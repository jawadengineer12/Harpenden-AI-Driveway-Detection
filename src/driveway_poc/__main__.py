"""Command-line entry point for the first feasibility milestone."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from contextlib import closing

from .addresses import OSPlaces
from .gemini import Gemini
from .download import fetch_image
from .config import PERMISSIONS, load_config, require_permissions
from .imagery import ApiError, discover
from .review import export_review, import_review, report
from .store import check_study, connect, import_candidates


def main(argv=None):
    parser = argparse.ArgumentParser(description=(
        "Audit street imagery and review Gemini driveway classification."
    ))
    parser.add_argument("--config", default="data/config.json")
    parser.add_argument("--db", default="data/poc.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show permissions and token presence; makes no network calls")
    candidate_parser = commands.add_parser("import-candidates", help="Validate and freeze an agreed sample")
    candidate_parser.add_argument("csv")
    discovery = commands.add_parser("discover", help="Search Mapillary metadata; never downloads images")
    discovery.add_argument("--retry-incomplete", action="store_true",
                           help="Explicitly retry failed/limited properties using a new request budget")
    review_template = commands.add_parser("review-template", help="Export discovered image links for review")
    review_template.add_argument("--output", default="outputs/review.csv")
    review_import = commands.add_parser("import-review", help="Validate and save manual feasibility checks")
    review_import.add_argument("csv")
    reporting = commands.add_parser("report", help="Write internal feasibility.csv and summary.json")
    reporting.add_argument("--output", default="outputs/feasibility")
    os_check = commands.add_parser("check-os", help="One OS request, one result; prints no addresses")
    os_check.add_argument("--longitude", type=float, required=True)
    os_check.add_argument("--latitude", type=float, required=True)
    commands.add_parser("check-gemini", help="One small generation request; no automatic retries")
    classify = commands.add_parser("classify-image", help="Analyze one permitted existing street image")
    classify.add_argument("image")
    classify.add_argument("--output", required=True)
    fetch = commands.add_parser("fetch-image", help="Fetch one Mapillary JPEG and provenance; two requests")
    fetch.add_argument("image_id")
    fetch.add_argument("--output", default="outputs/images")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "fetch-image":
            print(json.dumps(fetch_image(config, os.environ.get("MAPILLARY_ACCESS_TOKEN"),
                                        args.image_id, args.output), indent=2))
            return 0
        if args.command == "check-gemini":
            print(json.dumps(Gemini(os.environ.get("GEMINI_API_KEY")).check(), indent=2))
            return 0
        if args.command == "classify-image":
            require_permissions(config, "image_use", "image_storage", "external_ai")
            if not config["client_owned_account"]:
                raise ValueError("Client-owned account must be confirmed")
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            # Reserve before inference to avoid paying for a result that cannot be saved.
            with output.open("x", encoding="utf-8") as handle:
                try:
                    result = Gemini(os.environ.get("GEMINI_API_KEY")).classify(args.image)
                    json.dump(result, handle, indent=2)
                    handle.write("\n")
                except BaseException:
                    handle.close()
                    output.unlink()
                    raise
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "check-os":
            client = OSPlaces(os.environ.get("OS_PLACES_API_KEY"))
            candidates = client.radius(args.longitude, args.latitude)
            print(json.dumps({"provider": "OS Places", "status": "success",
                              "requests": client.requests, "returned_records": len(candidates),
                              "addresses_saved": False, "ownership_verified": False}))
            return 0
        if args.command == "status":
            print(json.dumps({
                "stage": "existing-imagery feasibility and single-image AI classification; lead export pending",
                "provider": config["provider"],
                "study_area_set": config["bbox"] is not None,
                "client_owned_account": config["client_owned_account"],
                "mapillary_token_present": bool(os.environ.get("MAPILLARY_ACCESS_TOKEN")),
                "os_places_key_present": bool(os.environ.get("OS_PLACES_API_KEY")),
                "gemini_key_present": bool(os.environ.get("GEMINI_API_KEY")),
                "permissions": {scope: config["permissions"][scope]["status"] for scope in PERMISSIONS},
                "freshness_policy_set": config["max_image_age_days"] is not None,
            }, indent=2))
            return 0
        with closing(connect(args.db, create=args.command == "import-candidates")) as db:
            if args.command == "import-candidates":
                count = import_candidates(db, args.csv, config)
                print(f"Frozen sample: {count} properties. No discovery has been inferred.")
            elif args.command == "discover":
                run = discover(db, config, os.environ.get("MAPILLARY_ACCESS_TOKEN"),
                               retry_incomplete=args.retry_incomplete)
                print(f"Metadata run {run} saved. Use report to inspect completeness and blockers.")
            elif args.command == "review-template":
                require_permissions(config, "metadata_access", "address_use")
                check_study(db, config)
                if Path(args.output).exists():
                    raise ValueError("Review output already exists; choose a new filename to preserve manual edits")
                count = export_review(db, args.output)
                print(f"Review template written: {count} candidate property/image pairs.")
            elif args.command == "import-review":
                count = import_review(db, args.csv, config)
                print(f"Validated {count} manual review rows.")
            elif args.command == "report":
                print(json.dumps(report(db, config, args.output), indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, ApiError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; saved work is retained.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
