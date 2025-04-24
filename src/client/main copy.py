#!/usr/bin/env python3
# candig_cli.py
"""
CanDIG data client: Download clinical data (optionally filtered by genomics)
or download genomic data directly via HTSget. Uses API strategies for live/tests-run modes.
"""

import argparse
import sys
import os
from typing import Dict, Any, Optional, List

# Import components from other modules
import config
import auth
import genomics_helpers
import clinical_helpers
from router import CandigRouter, TestRunRouter

def main():
    parser = argparse.ArgumentParser(
        description="CanDIG data client: Download clinical or genomic data. Runs in test-run mode (using mock data) if no token is provided.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # --- Mode Selection ---
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--clinical-download", action='store_true', help="Mode: Download clinical data.")
    mode_group.add_argument("--htsget-download", action='store_true', help="Mode: Download genomic data via HTSget.")

    # --- Filters ---
    genomic_search_group = parser.add_argument_group('Genomic Search Filters (for --clinical-download)')
    genomic_search_group.add_argument("--gene-id", help="Gene ID (e.g., BRCA1) to find relevant biosamples.")
    genomic_search_group.add_argument("--assembly", help="Assembly ID (e.g., GRCh38) for coordinate search.")
    parser.add_argument("--chrom", help="Chromosome/contig name (e.g., chr1, 1).")
    parser.add_argument("--start", type=int, help="Start position (0-based).")
    parser.add_argument("--end", type=int, help="End position.")
    clinical_filter_group = parser.add_argument_group('Clinical Data Filters (for --clinical-download)')
    clinical_filter_group.add_argument("--treatment-type", nargs='+', help="Filter by one or more treatment types.")
    clinical_filter_group.add_argument("--primary-site", nargs='+', help="Filter by one or more primary sites.")
    clinical_filter_group.add_argument("--drug-name", nargs='+', help="Filter by one or more drug names.")
    clinical_filter_group.add_argument("--program-id", nargs='+', help="Filter by one or more program IDs.")
    htsget_group = parser.add_argument_group('HTSget Download Options (for --htsget-download)')
    htsget_group.add_argument("--sample-id", help="Sample ID required for HTSget download.")

    # --- General Options ---
    parser.add_argument("--base-url", default=config.DEFAULT_BASE_URL, help="CanDIG server base URL.")
    parser.add_argument("--token", help="Authentication bearer token. If omitted, activates test-run mode.")
    parser.add_argument("--timeout", type=float, default=config.DEFAULT_TIMEOUT, help="Request timeout in seconds.")
    parser.add_argument("--output-dir", default=config.CLINICAL_OUTPUT_DIR, help="Directory for Katsu CSV output.")
    parser.add_argument("--htsget-output-dir", default=config.GENOMIC_OUTPUT_DIR, help="Directory for HTSget file output.")
    parser.add_argument("--mock-dir", default="mock", help="Directory containing mock JSON files for test-run mode.")


    args = parser.parse_args()

    # --- Input Validation ---
    if args.clinical_download:
        is_gene_search = args.gene_id is not None
        is_coord_search = all([args.assembly, args.chrom, args.start is not None, args.end is not None])
        has_some_coord_search_args = any([args.assembly, args.chrom, args.start is not None, args.end is not None])
        if is_gene_search and has_some_coord_search_args: parser.error("Cannot use --gene-id with coordinate search arguments.")
        if has_some_coord_search_args and not is_coord_search: parser.error("For coordinate search, must provide all of --assembly, --chrom, --start, and --end.")
        if args.sample_id: parser.error("Cannot use --sample-id with --clinical-download mode.")
    elif args.htsget_download:
        if not args.sample_id: parser.error("--sample-id is required for --htsget-download mode.")
        has_some_htsget_coords = any([args.chrom, args.start is not None, args.end is not None])
        has_all_htsget_coords = all([args.chrom, args.start is not None, args.end is not None])
        if has_some_htsget_coords and not has_all_htsget_coords: parser.error("If providing HTSget coordinates, must provide all of --chrom, --start, and --end.")
        if any([args.gene_id, args.assembly, args.treatment_type, args.primary_site, args.drug_name, args.program_id]): parser.error("Genomic search and clinical filters are not used with --htsget-download mode.")


    # --- Setup ---
    auth_token = auth.get_auth_token(args.token)

    # *** Select Router ***
    if auth_token is None:
        # Use Test Run mode, pass the mock directory path
        router = TestRunRouter(mock_dir=args.mock_dir)
        headers = {} 
        federation_url = "test-run-url" 
    else:
        print("\n--- Live Mode Activated (Token provided) ---")
        router = CandigRouter()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers["Authorization"] = f"Bearer {auth_token}"
        federation_url = f"{args.base_url.rstrip('/')}{config.FEDERATION_PATH}"


    # --- Mode Execution ---
    if args.clinical_download:
        print("\n=== Mode: Clinical Data Download ===")
        biosample_ids_for_katsu: Optional[List[str]] = None

        is_beacon_search_needed = args.gene_id or all([args.assembly, args.chrom, args.start is not None, args.end is not None])
        if is_beacon_search_needed:
            print("\n--- Step 1: Querying Beacon for relevant BioSamples ---")
            beacon_payload = genomics_helpers.build_beacon_request_payload(
                gene_id=args.gene_id, assembly=args.assembly, chrom=args.chrom, start=args.start, end=args.end
            )
            if not beacon_payload: sys.exit(1)

            # *** Use the selected router ***
            beacon_results = router.execute_federation_call(
                federation_url=federation_url, 
                headers=headers,           
                payload=beacon_payload,
                timeout=args.timeout      
            )

            if beacon_results is None:
                print("Beacon request/simulation failed. Cannot proceed.", file=sys.stderr)
                sys.exit(1)
            biosample_ids_for_katsu = genomics_helpers.extract_unique_biosample_ids(beacon_results)
            if not biosample_ids_for_katsu:
                 print("No biosamples found matching the genomic search criteria. Exiting.")
                 sys.exit(0)
            elif not biosample_ids_for_katsu:
                 print("[TEST RUN] Mock Beacon data did not yield any biosample IDs. Proceeding with Katsu request.")
        else:
             print("\n--- Step 1: Skipping Beacon Search (no genomic filters provided) ---")

        print("\n--- Step 2: Querying Katsu for Clinical Data ---")
        katsu_payload = clinical_helpers.build_clinical_request_payload(
            biosample_ids=biosample_ids_for_katsu, treatment_types=args.treatment_type,
            primary_sites=args.primary_site, drug_names=args.drug_name, program_ids=args.program_id
        )

        katsu_federation_results = router.execute_federation_call(
            federation_url=federation_url, headers=headers, payload=katsu_payload, timeout=args.timeout
        )
        if katsu_federation_results is None:
             print("Katsu request/simulation failed.", file=sys.stderr)
             sys.exit(1)

        print("\n--- Step 3: Processing and Writing Katsu Results ---")
        aggregated_clinical_data = clinical_helpers.aggregate_katsu_results(katsu_federation_results)
        if aggregated_clinical_data:
            clinical_helpers.write_katsu_csvs(aggregated_clinical_data, args.output_dir)
            print(f"\nClinical data CSVs written to: {os.path.abspath(args.output_dir)}")
        else:
            print("No clinical data aggregated (check mock files if in test-run mode).")

        print("\n=== Clinical Download Process Finished ===")

    elif args.htsget_download:
        print("\n=== Mode: HTSget Genomic Data Download ===")

        success = router.execute_htsget_download(
            htsget_base_url=args.base_url,
            sample_id=args.sample_id,
            output_dir=args.htsget_output_dir,
            token=auth_token, 
            timeout=args.timeout, 
            chrom=args.chrom,
            start=args.start,
            end=args.end
        )
        # --- Processing remains the same ---
        if success:
            if isinstance(router, TestRunRouter):
                 print(f"\n=== HTSget Download Simulated Successfully ===")
            else:
                 print(f"\n=== HTSget Download Complete. File(s) in: {os.path.abspath(args.htsget_output_dir)} ===")
        else:
            print("\nHTSget download/simulation failed.", file=sys.stderr)
            sys.exit(1)

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