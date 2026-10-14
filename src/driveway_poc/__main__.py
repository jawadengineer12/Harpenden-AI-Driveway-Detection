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
from .review import export_review, import_review, report, export_gallery
from .store import check_study, connect, import_candidates
from .results import link_image, import_classification, review_material, export_results


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
    fetch.add_argument("--original", action="store_true", help="Request highest available rendition; use a separate output directory")
    fetch.add_argument("--output", default="outputs/images")
    addresses = commands.add_parser("lookup-addresses", help="One bounded OS lookup; save unverified address candidates")
    addresses.add_argument("--longitude", type=float, required=True)
    addresses.add_argument("--latitude", type=float, required=True)
    addresses.add_argument("--radius", type=float, default=100)
    addresses.add_argument("--maxresults", type=int, default=10)
    addresses.add_argument("--output", required=True)
    linking = commands.add_parser("link-image", help="Add a provisional image/property pair; does not verify ownership")
    linking.add_argument("property_id")
    linking.add_argument("metadata")
    importing = commands.add_parser("import-classification", help="Import saved inference tied to image bytes; no API call")
    importing.add_argument("image_id")
    importing.add_argument("result")
    importing.add_argument("image")
    material = commands.add_parser("review-material", help="Record an actual human material review for one driveway")
    material.add_argument("property_id")
    material.add_argument("image_id")
    material.add_argument("--detection-id", required=True)
    material.add_argument("--class", dest="driveway_class", required=True)
    material.add_argument("--reviewer", required=True)
    material.add_argument("--date", dest="reviewed_on", required=True)
    material.add_argument("--evidence", required=True)
    exporting = commands.add_parser("export-results", help="Write reviewed leads.csv and all-property qa.csv")
    exporting.add_argument("--output", required=True)
    gallery = commands.add_parser("review-gallery", help="Generate an offline gallery of downloaded candidate views")
    gallery.add_argument("--images", default="outputs/images")
    gallery.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "lookup-addresses":
            require_permissions(config, "address_use")
            if not config["client_owned_account"]:
                raise ValueError("Client-owned account must be confirmed")
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                try:
                    client = OSPlaces(os.environ.get("OS_PLACES_API_KEY"))
                    candidates = client.radius(args.longitude, args.latitude, args.radius, args.maxresults)
                    json.dump({"provider": "OS Places", "requests": client.requests,
                               "query": {"longitude": args.longitude, "latitude": args.latitude,
                                         "radius_m": args.radius, "maxresults": args.maxresults},
                               "ownership_verified": False, "candidates": candidates}, handle, indent=2)
                    handle.write("\n")
                except BaseException:
                    handle.close()
                    output.unlink()
                    raise
            print(json.dumps({"status": "success", "requests": client.requests,
                              "candidate_count": len(candidates), "ownership_verified": False}))
            return 0
        if args.command == "fetch-image":
            print(json.dumps(fetch_image(config, os.environ.get("MAPILLARY_ACCESS_TOKEN"),
                                        args.image_id, args.output, original=args.original), indent=2))
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
                except BaseException as exc:
                    handle.close()
                    output.unlink()
                    if isinstance(exc, ApiError) and hasattr(exc, "raw_response"):
                        diagnostic = output.with_suffix(output.suffix + ".rejected.txt")
                        if not diagnostic.exists():
                            with diagnostic.open("x", encoding="utf-8") as rejected:
                                rejected.write(exc.raw_response)
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
                "stage": "imagery, address candidates, classification and reviewed export; live validation in progress",
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
            elif args.command == "link-image":
                image_id = link_image(db, config, args.property_id, args.metadata)
                print(json.dumps({"image_id": image_id, "ownership_verified": False}))
            elif args.command == "import-classification":
                label = import_classification(db, config, args.image_id, args.result, args.image)
                print(json.dumps({"classification": label, "scope": "scene_summary_only", "human_review_required": True}))
            elif args.command == "review-material":
                review_material(db, config, args.property_id, args.image_id, args.driveway_class,
                                args.reviewer, args.reviewed_on, args.evidence, args.detection_id)
                print("Material review saved; address/visibility review is separate.")
            elif args.command == "export-results":
                print(json.dumps(export_results(db, config, args.output), indent=2))
            elif args.command == "discover":
                run = discover(db, config, os.environ.get("MAPILLARY_ACCESS_TOKEN"),
                               retry_incomplete=args.retry_incomplete)
                print(f"Metadata run {run} saved. Use report to inspect completeness and blockers.")
            elif args.command == "review-gallery":
                print(json.dumps(export_gallery(db, config, args.images, args.output), indent=2))
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
