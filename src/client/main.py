import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from client import auth
from client import clinical_helpers
from client import config
from client import download_helpers
from client import genomics_helpers
from colorama import Fore, Style, init
from tqdm import tqdm

init()
logger = logging.getLogger(__name__)


# --- Logging Setup ---

def setup_logging(session_dir: Path, log_level: int = config.LOG_LEVEL) -> None:
    log_format = "%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    formatter = logging.Formatter(log_format)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(Path(session_dir, "download-client.log"))
    plain_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(plain_formatter)
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    # Suppress httpx info logs unless log_level is DEBUG
    httpx_log_level = logging.WARNING if log_level > logging.DEBUG else logging.DEBUG
    logging.getLogger("httpx").setLevel(httpx_log_level)


# --- Helper : Session Setup ---
def _setup_session_download(log_level, resume_path_str: Optional[str]) -> Path:
    if resume_path_str:
        session_dir = Path(resume_path_str)
        if not session_dir.is_dir():
            logger.critical(f"Resume directory {session_dir} does not exist. Exiting.")
            sys.exit(1)
        print(f"Resuming download session in: {session_dir}")
    else:
        session_dir = download_helpers.get_download_session_dir()
        print(f"New download folder created at: {session_dir}")

    setup_logging(session_dir, log_level)
    run_log_file_path = session_dir / "download-client.log"
    # Filter out token from argv
    filtered_argv = [
        arg
        for i, arg in enumerate(sys.argv)
        if arg != "--token"
        and (i == 0 or sys.argv[i - 1] != "--token")
        and not arg.startswith("--token=")
    ]
    logger.info(f"Run command: {' '.join(filtered_argv)}\n")
    logger.info(f"Run command details saved to {run_log_file_path}")
    return session_dir


# --- Helper: Beacon Search ---
def _fetch_beacon_biosample_ids(
    args: argparse.Namespace, headers: Dict[str, str], federation_url: str
) -> List[str]:
    logger.info("Applying genomic filters via Beacon search...")
    beacon_payload = None
    if args.gene_id:
        beacon_payload = genomics_helpers.build_beacon_request_payload(
            gene_id=args.gene_id, assembly="hg38"
        )
    elif args.coord:
        parsed_coord = genomics_helpers.parse_coord_string(args.coord)
        if not parsed_coord:
            logger.critical(f"Invalid coordinate string: {args.coord}. Exiting.")
            sys.exit(1)
        beacon_payload = genomics_helpers.build_beacon_request_payload(
            assembly="hg38",
            chrom=parsed_coord["chrom"],
            start=parsed_coord["start"],
            end=parsed_coord["end"],
        )

    # Call federation nodes with beacon payload
    beacon_results = download_helpers.execute_federation_call(
        federation_url, headers, beacon_payload
    )
    if beacon_results is None:
        logger.critical("Beacon request failed (API error or no response). Exiting.")
        sys.exit(1)

    program_sample_ids = (
        genomics_helpers.extract_unique_program_sample_ids_from_beacon_results(
            beacon_results
        )
    )
    if not program_sample_ids:
        logger.critical("Beacon search yielded 0 results. Exiting.")
        sys.exit(1)

    logger.info(f"Beacon search yielded {len(program_sample_ids)} sample IDs.")
    return program_sample_ids


# --- Helper: Clinical Data ---
def _process_clinical_data(
    args: argparse.Namespace,
    headers: Dict[str, str],
    federation_url: str,
    session_dir: Path,
    initial_biosample_ids: Optional[List[str]],
) -> List[str]:
    print("Processing clinical data ...")

    clinical_payload = clinical_helpers.build_clinical_request_payload(
        biosample_ids=initial_biosample_ids,
        treatment_types=args.treatment_type,
        primary_sites=args.primary_site,
        drug_names=args.drug_name,
        program_ids=args.program_id,
        summary_only=args.dry_run if args.clinical else False,
    )

    with tqdm(
        desc="Fetching clinical data",
        unit="source",
        disable=args.dry_run or not logger.isEnabledFor(logging.INFO),
    ) as pbar:
        clinical_results = download_helpers.execute_federation_call(
            federation_url,
            headers,
            clinical_payload,
            progress_callback=lambda: pbar.update(1),
        )

    if clinical_results is None:
        logger.critical("Clinical data request failed (API error or no response). Exiting.")
        sys.exit(1)

    # if dry run and only clinical data is requested
    if args.dry_run and args.clinical:
        print("\nClinical Data Summary (Dry Run):")
        summary = clinical_helpers.aggregate_clinical_results(clinical_results, is_clinical_dry_run=True).get("summary")
        if summary:
            print(f"  Message: {summary.get('message', 'N/A')}")
            print("  Record Counts:")
            for cat, count in summary.get("record_counts", {}).items():
                print(f"    {cat}: {count}")
        else:
            print("  No summary information available in dry run results for clinical data.")
    
    # otherwise, save clinical data
    else:
        aggregated_data = clinical_helpers.aggregate_clinical_results(clinical_results)
        clinical_dir = session_dir / "clinical_data"
        clinical_dir.mkdir(exist_ok=True)
        clinical_helpers.write_clinical_csvs(aggregated_data, str(clinical_dir))
        logger.info(f"Clinical data saved to: {clinical_dir}")

        final_ids = clinical_helpers.extract_unique_program_sample_ids_from_clinical_data(aggregated_data)
        logger.info(f"Clinical processing yielded {len(final_ids)} sample IDs for subsequent steps.")
        return final_ids

    return []

# --- Helper: Variant Data Processing ---
def _process_variant_data(
    args: argparse.Namespace,
    headers: Dict[str, str],
    federation_url: str,
    session_dir: Path,
    ids_for_new_metadata: Optional[List[str]],
):
    print("\nProcessing variant data...")
    download_helpers.run_variant_download_pipeline(
        program_sample_ids=ids_for_new_metadata,
        federation_headers=headers,
        download_headers=headers,
        federation_url=federation_url,
        is_dry_run=args.dry_run,
        session_dir=session_dir,
    )
    logger.info(f"Variant data saved to: {session_dir / 'variant_data'}")


def main():
    # ===================================================
    #                   CLI PARSER SETUP
    # ===================================================
    parser = argparse.ArgumentParser(
        description="CanDIG download client.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ===== Logging Configuration =====
    parser.add_argument(
        "-ll",
        "--log-level",
        type=int,
        default=logging.INFO,
        help="Logging level (10=DEBUG ... 50=CRITICAL)",
    )

    # ===== Data Download Options =====
    # Controls what data will be downloaded
    output_group = parser.add_argument_group("Output data options")
    output_group.add_argument(
        "-a", "--all", action="store_true", help="Download all data types"
    )
    output_group.add_argument(
        "-c", "--clinical", action="store_true", help="Download clinical data"
    )
    output_group.add_argument(
        "-v", "--variant", action="store_true", help="Download variant data"
    )

    # ===== Donor Filtering Options =====
    # Filters to select specific donors based on various criteria
    donor_group = parser.add_argument_group("Donor filtering Options")
    donor_group.add_argument("--gene-id", help="Filter by gene ID (e.g., SLX9)")
    donor_group.add_argument(
        "--coord", help="Filter by region (e.g., chr1:10000-20000)"
    )
    donor_group.add_argument(
        "--treatment-type", nargs="+", help="Filter by treatment types"
    )
    donor_group.add_argument(
        "--primary-site", nargs="+", help="Filter by primary tumor sites"
    )
    donor_group.add_argument("--drug-name", nargs="+", help="Filter by drug names")
    donor_group.add_argument("--program-id", nargs="+", help="Filter by program IDs")

    # ===== Authentication and Configuration =====
    # Settings for authentication and client behavior
    configuration_group = parser.add_argument_group("Configuration options")
    configuration_group.add_argument("--token", help="Authentication bearer token")
    configuration_group.add_argument(
        "-d", "--dry-run", action="store_true", help="Dry run mode"
    )
    configuration_group.add_argument(
        "-r", "--resume", type=str, help="Path to resume download session"
    )

    args = parser.parse_args()
    # ===================================================
    #                   DATA PROCESSING
    # ===================================================
    # This section handles the core data processing workflow:
    #   1. Validate input arguments and setup client
    #   2. Beacon search (if provided)
    #   3. Download and process clinical data (if requested)
    #   4. Download and process variant data (if requested)
    # ===================================================
    session_dir = _setup_session_download(args.log_level, args.resume)

    # ===== Setup Logging =====


    if args.dry_run:
        logger.warning("DRY RUN MODE ENABLED")

    # ===== Resume Mode =====
    if args.resume:
        # Disallowed arguments when --resume is active
        disallowed_flags_with_resume = []
        if args.all:
            disallowed_flags_with_resume.append("--all")
        if args.clinical:
            disallowed_flags_with_resume.append("--clinical")

        filter_arg_names = [
            "gene_id",
            "coord",
            "treatment_type",
            "primary_site",
            "drug_name",
            "program_id",
        ]
        for arg_name in filter_arg_names:
            if getattr(args, arg_name) is not None:
                disallowed_flags_with_resume.append(f"--{arg_name.replace('_', '-')}")

        if disallowed_flags_with_resume:
            parser.error(
                f"With --resume, data type flags (except implicitly --variant) or filter arguments "
                f"({', '.join(disallowed_flags_with_resume)}) are not allowed. "
                "Resume mode only accepts --log-level and --token."
            )

        # Resume mode should be use for variant download only
        args.variant = True
        args.clinical = False
        args.all = False
        for arg_name in filter_arg_names:
            setattr(args, arg_name, None)

    # ===== Normal Mode =====
    elif not (args.all or args.clinical or args.variant):
        parser.error("Specify at least one data type (-a, -c, -v) or use --resume.")

    if args.gene_id and args.coord:
        parser.error("Cannot use both --gene-id and --coord. Exiting.")
    
    if args.variant and args.dry_run and not (args.gene_id or args.coord):
        parser.error(
            "When using --variant with --dry-run, you must specify either --gene-id or --coord."
        )

    # ===== Authentication & Session =====
    auth_token = auth.get_auth_token(args.token)  # Prompts if args.token is None
    if not auth_token:
        logger.critical(
            "Authentication token is required and was not provided. Exiting."
        )
        sys.exit(1)
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    federation_url = f"{config.DEFAULT_BASE_URL.rstrip('/')}{config.FEDERATION_PATH}"

    # --- Biosample IDs ---
    new_biosample_ids: Optional[List[str]] = None
    if not args.resume:
        if args.gene_id or args.coord:
            new_biosample_ids = _fetch_beacon_biosample_ids(
                args, headers, federation_url
            )

        # Clinical processing if:
        # 1. -c or -a specified (want clinical files)
        # 2. Any clinical filters are specified (want to use biosample IDs for filtering variant)
        has_clinical_filters = any(
            [args.treatment_type, args.primary_site, args.drug_name, args.program_id]
        )
        should_process_clinical = args.clinical or args.all or has_clinical_filters

        if should_process_clinical:
            new_biosample_ids = _process_clinical_data(
                args,
                headers,
                federation_url,
                session_dir,
                initial_biosample_ids=new_biosample_ids,  # Pass IDs from beacon, if any
            )

    # --- Variant Data Processing ---
    if args.variant or args.all:
        _process_variant_data(
            args, headers, federation_url, session_dir, new_biosample_ids
        )

    logger.info(
        f"\nAll operations finished. Output data and logs are in: {session_dir.resolve()}"
    )
    if args.dry_run:
        logger.info(
            "DRY RUN COMPLETED."
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except SystemExit as e:
        sys.exit(e.code)
    except Exception:
        if not logging.getLogger().hasHandlers():
            setup_logging(logging.DEBUG)
        logger.critical("An unexpected top-level error occurred:", exc_info=True)
        print(
            "\nAn unexpected top-level error occurred. Check logs for details.",
            file=sys.stderr,
        )
        sys.exit(1)
