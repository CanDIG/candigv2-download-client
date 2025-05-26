import argparse
import logging
import sys
from typing import List, Optional

import auth
import clinical_helpers
import config
import genomics_helpers
import download_helpers
from tqdm import tqdm
from colorama import Fore, Style, init

init()

logger = logging.getLogger(__name__)

class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log messages based on level."""
    
    COLORS = {
        logging.DEBUG: Fore.BLUE,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        # For WARNING and ERROR levels, color the entire message
        if record.levelno in (logging.WARNING, logging.ERROR, logging.CRITICAL):
            color = self.COLORS[record.levelno]
            record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
            record.levelname = f"{color}{record.levelname}{Style.RESET_ALL}"
        else:
            # For other levels, only color the level name
            if record.levelno in self.COLORS:
                record.levelname = f"{self.COLORS[record.levelno]}{record.levelname}{Style.RESET_ALL}"
        return super().format(record)

def setup_logging(log_level: int = config.LOG_LEVEL) -> None:
    """Set up logging configuration based on numeric log level.

    Args:
        log_level: Numeric log level (10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL)
    """
    log_format = "%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    formatter = ColoredFormatter(log_format)
    
    # Create console handler with the custom formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)

def main():
    
    # ===================================================
    #                   CLI PARSER SETUP
    # ===================================================

    parser = argparse.ArgumentParser(
        description="CanDIG download client: Download data from CanDIG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ===== Logging Configuration =====
    parser.add_argument(
        "-ll","--log-level",
        type=int,
        default=logging.WARNING,
        help="Set the logging level (10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL)",
    )

    # ===== Data Download Options =====
    # Controls what data will be downloaded
    output_group = parser.add_argument_group("Output data options")
    output_group.add_argument(
        "-a", "--all", action="store_true", help="Download all available data types"
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
    donor_group.add_argument(
        "--gene-id",
        help="Filter to donors with mutations in the given gene. (e.g. SLX9)",
    )
    donor_group.add_argument(
        "--coord",
        help="Filter to donors with mutations in a specific chromosomal region (e.g. chr1:10000-20000)",
    )
    donor_group.add_argument(
        "--treatment-type",
        nargs="+",
        help="Filter to donors treated with one or more treatment types, donors are returned if they match at least one of the types.",
    )
    donor_group.add_argument(
        "--primary-site",
        nargs="+",
        help="Filter to donors diagnosed with tumours at one or more primary sites, donors are returned if they match at least one of the sites.",
    )
    donor_group.add_argument(
        "--drug-name",
        nargs="+",
        help="Filter to donors treated with one or more systemic therapy drugs, donors are returned if they match at least one of the drug names.",
    )
    donor_group.add_argument(
        "--program-id", nargs="+", help="Filter to donors by one or more program IDs."
    )

    # ===== Authentication and Configuration =====
    # Settings for authentication and client behavior
    configuration_group = parser.add_argument_group("Configuration options")
    configuration_group.add_argument(
        "--token", help="Authentication bearer token (prompts if not provided)"
    )
    configuration_group.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        help="Run in dry run mode, no data is downloaded but the client tells you how much data would be downloaded given the provided parameters.",
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

    # ===== Setup Logging =====
    setup_logging(args.log_level)

    # ===== Dry Run Warning =====
    if args.dry_run:
        logger.warning("DRY RUN MODE ENABLED")

    # ===== Validate Input Arguments =====
    # Require at least one output data option
    if not (args.all or args.clinical or args.variant):
        parser.error(
            "You must specify at least one output data option:\n"
            "  -a, --all      Download all available data types\n"
            "  -c, --clinical Download clinical data\n"
            "  -v, --variant  Download variant data"
        )

    # Validate that gene_id and coord are not used together
    if args.gene_id is not None and args.coord is not None:
        parser.error(
            "Cannot use both --gene-id and --coord parameters together.\n"
            "Please specify either --gene-id or --coord, but not both."
        )

    # ===== Get Token =====
    auth_token = auth.get_auth_token(args.token)
    if auth_token is None:
        logger.error("No token provided. Exiting.")
        sys.exit(1)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {auth_token}",
    }

    # ===== Setup =====
    federation_url = f"{config.DEFAULT_BASE_URL.rstrip('/')}{config.FEDERATION_PATH}"
    clinical_payload = None
    program_sample_ids: Optional[List[str]] = None

    # ===== Create Download Session Directory =====
    session_dir = download_helpers.get_download_session_dir()
    print(f"Download session folder created at: {session_dir}")

    # ===== Beacon Search =====
    is_beacon_search_gene = args.gene_id is not None
    is_beacon_search_coords = args.coord is not None
    if is_beacon_search_coords:
        beacon_search_coords = genomics_helpers.parse_coord_string(args.coord)
        logger.debug(f"Parsed coordinate string: {beacon_search_coords}")
    is_beacon_search = is_beacon_search_gene or is_beacon_search_coords
    if is_beacon_search:
        if is_beacon_search_gene:
            logger.debug(f"Beacon search with gene_id: {args.gene_id}")
            beacon_payload = genomics_helpers.build_beacon_request_payload(
                gene_id=args.gene_id,
                assembly="hg38",
            )
        elif is_beacon_search_coords:
            logger.debug(f"Beacon search with coordinates: {args.coord}")
            beacon_payload = genomics_helpers.build_beacon_request_payload(
                assembly="hg38",
                chrom=beacon_search_coords["chrom"],
                start=beacon_search_coords["start"],
                end=beacon_search_coords["end"],
            )

        if not beacon_payload:
            logger.error("No beacon payload built. Exiting.")
            sys.exit(1)

        logger.info("Executing beacon federation call")
        beacon_results = download_helpers.execute_federation_call(
            federation_url=federation_url,
            headers=headers,
            payload=beacon_payload,
        )

        if beacon_results is None:
            logger.error("Beacon request failed. Cannot proceed.")
            sys.exit(1)

        program_sample_ids = genomics_helpers.extract_unique_program_sample_ids_from_beacon_results(
            beacon_results
        )
        logger.debug(
            f"Found {len(program_sample_ids)} program_sample ids from beacon search: {program_sample_ids}"
        )

        if not program_sample_ids:
            logger.warning(
                "No program_sample ids found matching the genomic search criteria. Exiting."
            )
            sys.exit(0)

    # ===== Download Clinical Data =====
    if args.clinical or args.all:
        print("\nDownloading clinical data...")
        # Determine if this is a clinical-only dry run
        is_clinical_dry_run = args.dry_run and args.clinical and not args.all
        
        clinical_payload = clinical_helpers.build_clinical_request_payload(
            biosample_ids=program_sample_ids,
            treatment_types=args.treatment_type,
            primary_sites=args.primary_site,
            drug_names=args.drug_name,
            program_ids=args.program_id,
            summary_only=is_clinical_dry_run,
        )

        with tqdm(desc="Fetching clinical data", unit="source") as pbar:
            clinical_federation_results = download_helpers.execute_federation_call(
                federation_url=federation_url,
                headers=headers,
                payload=clinical_payload,
                progress_callback=lambda: pbar.update(1),
            )
            if clinical_federation_results is None:
                logger.error("Clinical data request failed.")
                sys.exit(1)


        aggregated_clinical_data = clinical_helpers.aggregate_clinical_results(
            clinical_federation_results,
            is_clinical_dry_run=is_clinical_dry_run
        )
        if aggregated_clinical_data:
            if is_clinical_dry_run:
                print("\nClinical Data Summary:")
                print(f"Message: {aggregated_clinical_data['summary']['message']}")
                print("\nRecord Counts:")
                for category, count in aggregated_clinical_data['summary']['record_counts'].items():
                    print(f"  {category}: {count}")
            else:
                clinical_dir = session_dir / "clinical_data"
                clinical_dir.mkdir(exist_ok=True)
                clinical_helpers.write_clinical_csvs(
                    aggregated_clinical_data, str(clinical_dir)
                )
                print(f"Clinical data saved to: {clinical_dir}")
                # use the program_sample_ids from the clinical data if available
                program_sample_ids = clinical_helpers.extract_unique_program_sample_ids_from_clinical_data(
                    aggregated_clinical_data
                )
        else:
            logger.warning("No clinical data found")

        

    # ===== Download Variant Data =====
    if args.variant or args.all:
        print("\nDownloading variant data...")
        download_helpers.download_variant_data(
            program_sample_ids=program_sample_ids,
            headers=headers,
            federation_url=federation_url,
            session_dir=session_dir,
            is_dry_run=args.dry_run,
        )
        print(f"Variant data saved to: {session_dir}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected top-level error occurred: {e}", file=sys.stderr)
        sys.exit(1)
