"""
CanDIG download client: Download clinical and genomic data.
"""

import argparse
import sys
import os
from typing import Dict, Any, Set, Optional, List
from datetime import datetime
import auth
import config
import genomics_helpers
import clinical_helpers
from router import CandigRouter, TestRunRouter


def main():
    parser = argparse.ArgumentParser(
        description="CanDIG download client: Download data from CanDIG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    donor_group = parser.add_argument_group('Donor filtering Options')
    donor_group.add_argument("--gene-id", help="Filter to donors with mutations in the given gene. (e.g. SLX9)")
    donor_group.add_argument("--coord", help="Filter to donors with mutations in a specific chromosomal region (e.g. chr1:10000-20000)")
    donor_group.add_argument("--treatment-type", nargs='+', help="Filter to donors treated with one or more treatment types, donors are returned if they match at least one of the types.")
    donor_group.add_argument("--primary-site", nargs='+', help="Filter to donors diagnosed with tumours at one or more primary sites, donors are returned if they match at least one of the sites.")
    donor_group.add_argument("--drug-name", nargs='+', help="Filter to donors treated with one or more systemic therapy drugs, donors are returned if they match at least one of the drug names.")
    donor_group.add_argument("--program-id", nargs='+', help="Filter to donors by one or more program IDs.")

    output_group = parser.add_argument_group('Output data options')
    output_group.add_argument("-a", "--all", action="store_true", help="Download all available data types. Currently Clinical and Variants.")
    output_group.add_argument("-c", "--clinical", action="store_true", help="Download clinical data")
    #output_group.add_argument("-v", "--variant", action="store_true", help="Download variant data")
    #output_group.add_argument("--variant-format", type=str, default="vcf", help="Return variants in beacon or vcf format")
    #output_group.add_argument("-m", "--matrix", action="store_true", help="Download gene expression matrix")

    configuration_group = parser.add_argument_group('Configuration options')
    # configuration_group.add_argument("--base-url", default=config.DEFAULT_BASE_URL, help="CanDIG server base URL")
    configuration_group.add_argument("--token", help="Authentication bearer token (prompts if not provided)")
    # configuration_group.add_argument("--timeout", type=float, default=config.DEFAULT_TIMEOUT, help="Request timeout in seconds")
    # configuration_group.add_argument("--output-dir", default=config.DATA_OUTPUT_DIR, help="Directory to save output files, directory is created if it doesn't already exist and is relative to where you execute the script.")
    configuration_group.add_argument("--demo-mode", action="store_true", help="Run in demo mode, this downloads synthetic clinical data so you can test filtering parameters and understand the format of clinical data.")
    configuration_group.add_argument("--dry-run", "-d", action="store_true", help="Run in dry run mode, no data is downloaded but the client tells you how much data would be downloaded given the provided parameters.")
    #configuration_group.add_argument("--htsget-output-dir", default=GENOMIC_OUTPUT_DIR, help="Directory to save HTSget download files")

    args = parser.parse_args()

    is_beacon_search_gene = args.gene_id is not None
    is_beacon_search_coords = args.coord is not None
    if is_beacon_search_coords:
        beacon_search_coords = genomics_helpers.parse_coord_string(args.coord)
    is_beacon_search = is_beacon_search_gene or is_beacon_search_coords



    # *** Select Router ***
    if args.demo_mode:
        # Use Test Run mode, pass the mock directory path
        router = TestRunRouter()
        headers = {}
        federation_url = "test-run-url"
    else:
        # print("\n--- Live Mode Activated (Token provided) ---")
        # --- Get Token ---
        auth_token = auth.get_auth_token(args.token)
        if auth_token is None:
            print("No token provided. Exiting.", file=sys.stderr)
            sys.exit(1)
        router = CandigRouter()
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "Authorization": f"Bearer {auth_token}"}
        federation_url = f"{config.DEFAULT_BASE_URL.rstrip('/')}{config.FEDERATION_PATH}"

    clinical_payload = None

    if args.all or args.clinical:
        biosample_ids_for_clinical: Optional[List[str]] = None

        if is_beacon_search:
            print("\n--- Step 1: Querying Beacon for relevant BioSamples ---")
            if is_beacon_search_gene:
                beacon_payload = genomics_helpers.build_beacon_request_payload(
                    gene_id=args.gene_id,
                    assembly="hg38",
                )
            elif is_beacon_search_coords:
                beacon_payload = genomics_helpers.build_beacon_request_payload(assembly="hg38",
                                                              chrom=beacon_search_coords["chrom"],
                                                              start=beacon_search_coords["start"],
                                                              end=beacon_search_coords["end"])
            if not beacon_payload:
                sys.exit(1)

            # *** Use the selected router ***
            beacon_results = router.execute_federation_call(
                federation_url=federation_url,
                headers=headers,
                payload=beacon_payload,
                timeout=config.DEFAULT_TIMEOUT,
            )

            if beacon_results is None:
                print(
                    "Beacon request failed. Cannot proceed.", file=sys.stderr
                )
                sys.exit(1)
            biosample_ids_for_clinical = genomics_helpers.extract_unique_biosample_ids(
                beacon_results
            )
            if not biosample_ids_for_clinical:
                print(
                    "No biosamples found matching the genomic search criteria. Exiting."
                )
                sys.exit(0)
        else:
            print(
                "\n--- Step 1: Skipping Genomic Filtering (no filters provided) ---"
            )

        print("\n--- Step 2: Querying Clinical Data ---")
        clinical_payload = clinical_helpers.build_clinical_request_payload(
            biosample_ids=biosample_ids_for_clinical,
            treatment_types=args.treatment_type,
            primary_sites=args.primary_site,
            drug_names=args.drug_name,
            program_ids=args.program_id,
            summary_only=args.dry_run,
        )

        clinical_federation_results = router.execute_federation_call(
            federation_url=federation_url,
            headers=headers,
            payload=clinical_payload,
            timeout=config.DEFAULT_TIMEOUT,
        )
        if clinical_federation_results is None:
            print("Clinical data request failed.", file=sys.stderr)
            sys.exit(1)

        print("\n--- Step 3: Processing and Writing Clinical data Results ---")
        aggregated_clinical_data = clinical_helpers.aggregate_clinical_results(
            clinical_federation_results
        )
        if aggregated_clinical_data:
            clinical_helpers.write_clinical_csvs(aggregated_clinical_data, f"{config.DATA_OUTPUT_DIR}/{datetime.now().strftime("%Y%m%d%H%M")}-clinical_data")
            print(
                f"\nClinical data CSVs written to: {os.path.abspath(config.DATA_OUTPUT_DIR)}"
            )
        else:
            print("No clinical data aggregated (check mock files if in test-run mode).")

        print("\n=== Clinical Download Process Finished ===")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected top-level error occurred: {e}", file=sys.stderr)
        # import traceback
        # traceback.print_exc() # Uncomment for detailed debugging
        sys.exit(1)
