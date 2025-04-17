import httpx
import getpass
import argparse
import sys
import json
import csv
import os
from typing import Dict, Any, Set, Optional, List
from datetime import datetime


# --- Configuration ---
DEFAULT_BASE_URL = "http://candig.docker.internal:5080"
DEFAULT_TIMEOUT = 60.0
DATA_OUTPUT_DIR = f"{datetime.now().strftime("%Y%m%d%H%M")}-download"
CLINICAL_DATA_OUTPUT_DIR = f"{DATA_OUTPUT_DIR}/clinical_downloads"
VARIANT_OUTPUT_DIR = f"{DATA_OUTPUT_DIR}/variant_downloads"

# Federation Endpoint
FEDERATION_PATH = "/federation/v1/fanout"

# htsget service config
HTSGET_PATH = "/genomics/htsget/v1/reads/data/"
BEACON_PATH = "beacon/v2/g_variants"
HTSGET_SERIVCE = "htsget"

# katsu service config
KATSU_PATH = "v3/download/clinical_data/"
KATSU_SERVICE = "katsu"


# --- Helper Functions ---

def get_auth_token(token_arg: Optional[str]) -> Optional[str]:
    """Gets the authentication token (simple version)."""
    if token_arg:
        print("Using token from command-line argument.")
        return token_arg
    try:
        token = getpass.getpass("Enter your authentication token: ")
        return token
    except EOFError:
        print("Error: Could not read token.")
        return None


def parse_coord_string(coord_string: str):
    chromosomes = [str(x) for x in list(range(1, 23))] + ['X', 'Y']
    chr_chromosomes = ['chr' + x for x in chromosomes]
    all_chromosomes = chromosomes + chr_chromosomes
    try:
        split_chrom = coord_string.split(":")
        chrom = split_chrom[0]
        split_pos = split_chrom[1].split("-")
        start = split_pos[0]
        end = split_pos[1]
    except IndexError:
        print(f"Coordinate string invalid: `{coord_string}` is not formatted correctly, please ensure it follows the pattern <chrom>:<start>-<end>.")
        sys.exit()
    if chrom not in all_chromosomes:
        print("Chromosome invalid: indicate chromosome with [chr]1-22, X, Y")
        sys.exit()
    if int(start) > int(end):
        print("Coordinates invalid: start coordinate cannot be larger than end coordinate. Please ensure it follows the pattern <chrom>:<start>-<end>.")
        sys.exit()
    return {"chrom": chrom,
            "start": start,
            "end": end}


def build_gene_search_request_payload(
    gene_id: Optional[str] = None,
    assembly: Optional[str] = None,
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Builds the payload for htsget gene search federation POST request."""
    request_parameters: Dict[str, Any] = {}
    if gene_id:
        request_parameters["gene_id"] = gene_id
    elif assembly and chrom and start is not None and end is not None:
        request_parameters = {
            'assemblyId': assembly, 'referenceName': chrom,
            'start': [start], 'end': [end]
        }
    else:
        print("Error: Invalid parameters for building gene search request.")
        return None

    payload = {
        "path": BEACON_PATH,
        "payload": {"meta": {"apiVersion": "v2"}, "query": {"requestParameters": request_parameters}},
        "method": "POST",
        "service": HTSGET_SERIVCE,
    }
    return payload

def build_clinical_request_payload(
    biosample_ids: Optional[List[str]] = None,
    treatment_types: Optional[List[str]] = None,
    primary_sites: Optional[List[str]] = None,
    drug_names: Optional[List[str]] = None,
    program_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Builds the payload for the katsu federation request.
    Filters can be applied using the provided optional arguments.
    If no arguments are provided, it requests all clinical data.
    """
    katsu_target_payload: Dict[str, List[str]] = {}
    filter_descriptions = []

    if biosample_ids:
        katsu_target_payload["biosample_id"] = biosample_ids
        filter_descriptions.append(f"{len(biosample_ids)} BioSample IDs")
    if treatment_types:
        katsu_target_payload["treatment_type"] = treatment_types
        filter_descriptions.append(f"{len(treatment_types)} Treatment Types")
    if primary_sites:
        katsu_target_payload["primary_site"] = primary_sites
        filter_descriptions.append(f"{len(primary_sites)} Primary Sites")
    if drug_names:
        katsu_target_payload["systemic_therapy_drug_name"] = drug_names
        filter_descriptions.append(f"{len(drug_names)} Drug Names")
    if program_ids:
        katsu_target_payload["program_id"] = program_ids
        filter_descriptions.append(f"{len(program_ids)} Program IDs")

    if filter_descriptions:
        print(f"Building Katsu request with filters: {', '.join(filter_descriptions)}...")
    else:
        print("Building Katsu request for ALL clinical data (no filters)...")

    payload = {
        "path": KATSU_PATH,
        "payload": katsu_target_payload,
        "method": "POST",
        "service": KATSU_SERVICE,
    }
    return payload


def fetch_federation_data(
    federation_url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: float
) -> Optional[List[Dict[str, Any]]]:
    """Fetches data from the federation endpoint."""
    service = payload.get("service", "unknown service")
    path = payload.get("path", "unknown path")
    target_info = ""

    inner_payload = payload.get("payload", {})
    if service == KATSU_SERVICE and isinstance(inner_payload, dict):
        filters_applied = []
        if "biosample_id" in inner_payload and isinstance(inner_payload["biosample_id"], list):
            filters_applied.append(f"{len(inner_payload['biosample_id'])} samples")
        if "treatment_type" in inner_payload and isinstance(inner_payload["treatment_type"], list):
             filters_applied.append(f"{len(inner_payload['treatment_type'])} treatments")
        if "primary_site" in inner_payload and isinstance(inner_payload["primary_site"], list):
             filters_applied.append(f"{len(inner_payload['primary_site'])} sites")
        if "systemic_therapy_drug_name" in inner_payload and isinstance(inner_payload["systemic_therapy_drug_name"], list):
             filters_applied.append(f"{len(inner_payload['systemic_therapy_drug_name'])} drugs")
        if "program_id" in inner_payload and isinstance(inner_payload["program_id"], list):
             filters_applied.append(f"{len(inner_payload['program_id'])} programs")

        if filters_applied:
            target_info = f" ({', '.join(filters_applied)})"
        elif not inner_payload:
             target_info = " (all data)"

    elif service == HTSGET_SERIVCE:
        beacon_query = inner_payload.get("query", {}).get("requestParameters", {})
        if beacon_query.get("gene_id"):
            target_info = f" (gene: {beacon_query['gene_id']})"
        elif beacon_query.get("referenceName"):
            target_info = f" (coords: {beacon_query.get('assemblyId', 'N/A')}:{beacon_query['referenceName']}:{beacon_query.get('start', ['?'])[0]}-{beacon_query.get('end', ['?'])[0]})"
        else:
            target_info = " (unknown Beacon query)"


    print(f"Sending request to federation for {service} ({path}){target_info}...")
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(federation_url, headers=headers, json=payload)
            response.raise_for_status()
            print(f"Request for {service}{target_info} successful.")
            return response.json()
    except httpx.HTTPStatusError as e:
        print(f"HTTP error occurred during {service}{target_info} request: {e.response.status_code} - {e.request.url}")
        try:
            error_details = e.response.json()
            print(f"Error details: {json.dumps(error_details, indent=2)}")
        except json.JSONDecodeError:
            print(f"Response body: {e.response.text}")
        return None
    except httpx.RequestError as e:
        print(f"Network or connection error during {service}{target_info} request: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during {service}{target_info} request: {e}")
        return None

def extract_unique_biosample_ids(beacon_results: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Extracts unique biosample IDs from the Beacon response."""
    if not beacon_results:
        print("No results received to extract biosamples from.")
        return []
    unique_samples: Set[str] = set()
    print("Extracting biosample_ids from results...")
    processed_sources = 0
    for location_data in beacon_results:
        if location_data.get("error"):
            print(f"Warning: Skipping source due to error: {location_data['error']}")
            continue

        results = location_data.get('results')
        if not results:
            print(f"Warning: Skipping source, no 'results' field found.")
            continue

        response_summary = results.get("responseSummary")
        if not isinstance(response_summary, dict):
             print(f"Warning: Skipping source, 'responseSummary' is not a dictionary.")
             continue

        exists = response_summary.get("exists")
        if exists is None:
             print(f"Warning: Skipping source, 'exists' field missing in responseSummary.")
             continue
        if not exists:
             processed_sources += 1
             continue

        # If exists is true, proceed to extract sample IDs
        response_list = results.get("response")
        if not isinstance(response_list, list):
            print(f"Warning: Skipping source, 'response' field is not a list even though exists=true.")
            continue

        for response_item in response_list:
            if not isinstance(response_item, dict): 
                continue
            case_level_data = response_item.get("caseLevelData")
            if not isinstance(case_level_data, list): 
                continue
            for sample_info in case_level_data:
                if not isinstance(sample_info, dict): 
                    continue
                biosample_id = sample_info.get("biosampleId")
                if isinstance(biosample_id, str) and biosample_id:
                    unique_samples.add(biosample_id)
        processed_sources += 1

    print(f"Processed {processed_sources} source(s) from responses.")
    print(f"Found {len(unique_samples)} unique sample IDs.")
    return list(unique_samples)

def convert_json_to_csv(clinical_payload: Dict[str, List[Dict[str, Any]]], output_dir: str):
    """
    Writes the Katsu clinical data payload into multiple CSV files.
    """
    print(f"\n--- Writing Katsu Data to CSV Files in '{output_dir}' ---")

    if not clinical_payload or not isinstance(clinical_payload, dict):
        print("No valid Katsu clinical data payload received to write.")
        return

    os.makedirs(output_dir, exist_ok=True)
    files_written = 0

    for category, records_list in clinical_payload.items():
        if not isinstance(records_list, list):
            print(f"Skipping category '{category}': Value is not a list ({type(records_list)}).")
            continue
        if not records_list:
            continue

        valid_records = [rec for rec in records_list if isinstance(rec, dict)]
        if len(valid_records) != len(records_list):
            print(f"Warning: Category '{category}' contains non-dictionary items. Only writing dictionary items.")
        if not valid_records:
             print(f"Skipping category '{category}': No valid dictionary records found after filtering.")
             continue

        # Sanitize category name for filename
        safe_category = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in category)
        filename = os.path.join(output_dir, f"{safe_category}.csv")

        # Determine fieldnames from all valid records in the list
        fieldnames = set()
        for record in valid_records:
            fieldnames.update(record.keys())
        sorted_fieldnames = sorted(list(fieldnames))

        print(f"Writing {len(valid_records)} records to {filename}...")

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=sorted_fieldnames, extrasaction='ignore')
                writer.writeheader()
                for record in valid_records:
                    # Process record: flatten dicts/lists to JSON strings for CSV compatibility
                    processed_record = {}
                    for key in sorted_fieldnames:
                        value = record.get(key)
                        if isinstance(value, (dict, list)):
                            try:
                                processed_record[key] = json.dumps(value)
                            except TypeError:
                                print(f"Warning: Could not JSON serialize value for key '{key}' in {filename}. Writing as string.")
                                processed_record[key] = str(value)
                        elif value is not None:
                            processed_record[key] = value
                        else:
                            processed_record[key] = "" # Write empty string for None/missing values
                    writer.writerow(processed_record)
            files_written += 1
        except IOError as e:
            print(f"Error writing file {filename}: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while writing {filename}: {e}")

    if files_written > 0:
        print(f"--- Finished writing {files_written} CSV file(s) from Katsu data ---")
    else:
        print("--- No CSV files were written (payload might have been empty or contained no processable data) ---")


def download_htsget_data(
    htsget_base_url: str,
    sample_id: str,
    output_dir: str,
    token: Optional[str],
    timeout: float,
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None
) -> bool:
    """
    Downloads data from an HTSget endpoint and saves it to a file.
    Coordinates (chrom, start, end) are optional. If not provided, downloads the whole file.
    """
    base = htsget_base_url.rstrip('/')
    path = HTSGET_PATH.lstrip('/')
    url = f"{base}/{path}{sample_id}"

    params = {
        "class": "body",
    }
    # Conditionally add coordinate parameters
    if chrom:
        params["referenceName"] = chrom
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end

    headers = {"Accept": "*/*"} # Accept any format the server provides (BAM, CRAM, etc.)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    os.makedirs(output_dir, exist_ok=True)
    file_ext = ".txt" # Default extension
    if chrom and start is not None and end is not None:
        output_filename_base = f"{sample_id}_{chrom}_{start}-{end}"
    else:
        output_filename_base = f"{sample_id}"
    output_filename = os.path.join(output_dir, f"{output_filename_base}{file_ext}")

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()

        content_disposition = response.headers.get("Content-Disposition")
        if content_disposition:
            parts = content_disposition.split('filename=')
            if len(parts) > 1:
                potential_filename = parts[1].strip('"\' ')
                safe_filename = "".join(c if c.isalnum() or c in ('_', '-', '.') else '_' for c in potential_filename)
                if safe_filename:
                     if chrom and start is not None and end is not None:
                          base_name, _ = os.path.splitext(output_filename_base)
                          _, ext = os.path.splitext(safe_filename)
                          if ext:
                               output_filename = os.path.join(output_dir, f"{base_name}{ext}")
                          else:
                               output_filename = os.path.join(output_dir, f"{output_filename_base}{file_ext}")
                     else:
                          output_filename = os.path.join(output_dir, safe_filename)

        print(f"Saving file to: {output_filename}")

        # Write the entire response content
        with open(output_filename, "wb") as f:
            f.write(response.content)

        return True

    except httpx.HTTPStatusError as e:
        print(f"\nHTTP error occurred during HTSget download: {e.response.status_code} - {e.request.url}")
        try:
            error_details = e.response.read()
            print(f"Error details: {error_details.decode(errors='ignore')}")
        except Exception as read_err:
            print(f"Could not read error details from response body (Error: {read_err}).")
        if os.path.exists(output_filename):
            try:
                os.remove(output_filename)
                print(f"Removed potentially incomplete file: {output_filename}")
            except OSError as rm_err:
                print(f"Error removing incomplete file {output_filename}: {rm_err}")
        return False
    except httpx.RequestError as e:
        print(f"\nNetwork or connection error during HTSget download: {e}")
        if os.path.exists(output_filename):
             try:
                 os.remove(output_filename)
                 print(f"Removed potentially incomplete file: {output_filename}")
             except OSError as rm_err:
                 print(f"Error removing incomplete file {output_filename}: {rm_err}")
        return False
    except IOError as e:
         print(f"\nError writing file {output_filename}: {e}")
         # No need to remove file here as open likely failed before writing
         return False
    except Exception as e:
        print(f"\nAn unexpected error occurred during HTSget download: {e}")
        if os.path.exists(output_filename):
             try:
                 os.remove(output_filename)
                 print(f"Removed potentially incomplete file: {output_filename}")
             except OSError as rm_err:
                 print(f"Error removing incomplete file {output_filename}: {rm_err}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="CanDIG data client: Download data from CanDIG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    donor_group = parser.add_argument_group('Donor filtering Options')
    donor_group.add_argument("--gene-id", help="Filter to donors with mutations in the given gene. (e.g. SLX9)")
    donor_group.add_argument("--coord", help="Filter to donors with mutations in a specific chromosomal region (e.g. chr1:10000-20000)")
    donor_group.add_argument("--treatment-type", nargs='+', help="Filter to donors treated with one or more treatment types, donors are returned if they match at least one of the types.")
    donor_group.add_argument("--primary-site", nargs='+', help="Filter to donors diagnosed with tumours at one or more primary sites, donors are returned if they match at least one of the sites.")
    donor_group.add_argument("--drug-name", nargs='+', help="Filter to donors treated with one or more systemic therapy drugs, donors are returned if they match at least one of the drug names.")
    donor_group.add_argument("--program-id", nargs='+', help="Filter to donors by one or more program IDs.")

    variant_group = parser.add_argument_group('Variant filtering options')
    variant_group.add_argument("--filter-variants", action="store_true", help="Filter the output variants by the same genomic filters as used for the donor filtering.")
    variant_group.add_argument("--filter-gene", type=str, help="Filter the output variants by a specific gene.")
    variant_group.add_argument("--filter-coord", type=str, help="Filter the output variants to those within a specific region (e.g. `chr1:10000-20000`).")
    #variant_group.add_argument("--sample-id", help="Sample ID for HTSget download (e.g., SAMPLE_001). Use this specific flag for HTSget mode.")

    output_group = parser.add_argument_group('Output data options')
    output_group.add_argument("-a", "--all", action="store_true", help="Download all available data types. Currently Clinical and Variants.")
    output_group.add_argument("-c", "--clinical", action="store_true", help="Download clinical data")
    output_group.add_argument("-v", "--variant", action="store_true", help="Download variant data")
    output_group.add_argument("--variant-format", type=str, default="vcf", help="Return variants in beacon or vcf format")
    #output_group.add_argument("-m", "--matrix", action="store_true", help="Download gene expression matrix")

    configuration_group = parser.add_argument_group('Configuration options')
    configuration_group.add_argument("--base-url", default=DEFAULT_BASE_URL, help="CanDIG server base URL")
    configuration_group.add_argument("--token", help="Authentication bearer token (prompts if not provided)")
    configuration_group.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Request timeout in seconds")
    configuration_group.add_argument("--output-dir", default=DATA_OUTPUT_DIR, help="Directory to save output files")
    #configuration_group.add_argument("--htsget-output-dir", default=GENOMIC_OUTPUT_DIR, help="Directory to save HTSget download files")

    args = parser.parse_args()

    is_beacon_search_gene = args.gene_id is not None
    is_beacon_search_coords = args.coord is not None
    if is_beacon_search_coords:
        parse_coord_string(args.coord)
    is_beacon_search = is_beacon_search_gene or is_beacon_search_coords

    if args.filter_variants is not None:
        if not is_beacon_search_gene and not is_beacon_search_coords:
            parser.error("Variants cannot be filtered if no filters have been indicated. Specify filters with --gene-id or --coord or disable the --filter-variants option.")

        if any([args.filter_gene is not None, args.filter_coord is not None]):
            parser.error("Variants can only be filtered by one method, please choose --filter-variants, --filter-gene OR --filter-coord.")

    if args.filter_gene is not None and args.filter_coord is not None:
        parser.error("Variants can only be filtered by one method, please choose --filter-variants, --filter-gene OR --filter-coord.")

    if args.filter_coord is not None:
        parse_coord_string(args.filter_coord)

    # --- Get Token ---
    auth_token = get_auth_token(args.token)

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    federation_url = f"{args.base_url.rstrip('/')}{FEDERATION_PATH}"

    katsu_payload = None

    if args.all or args.clinical:
        if is_beacon_search:
            print("\n=== Mode: Clinical with Gene Search filters ===")
            # --- Stage 1: Beacon Query ---
            print("\n--- Step 1: Querying HTSGet for BioSamples ---")
            beacon_payload = None
            search_criteria_str = ""
            # Only search by gene or position
            if args.gene_id:
                beacon_payload = build_gene_search_request_payload(gene_id=args.gene_id)
                search_criteria_str = f"Gene ID: {args.gene_id}"
            else:
                beacon_payload = build_gene_search_request_payload(assembly=args.assembly, chrom=args.chrom, start=args.start, end=args.end)
                search_criteria_str = f"{args.assembly}:{args.chrom}:{args.start}-{args.end}"

            if not beacon_payload:
                 print("Error: Failed to build Beacon request payload based on provided arguments.")
                 sys.exit(1)

            beacon_results = fetch_federation_data(federation_url, headers, beacon_payload, args.timeout)
            if beacon_results is None:
                print("\nBeacon API request failed. Cannot proceed.")
                sys.exit(1)

            unique_biosamples = extract_unique_biosample_ids(beacon_results)
            if not unique_biosamples:
                print(f"\nNo biosamples found matching Beacon criteria: {search_criteria_str}")
                sys.exit(0)

            # katsu_payload = build_clinical_request_payload(sample_ids=unique_biosamples)
            katsu_payload = build_clinical_request_payload(
                biosample_ids=unique_biosamples,
                treatment_types=args.treatment_type,
                primary_sites=args.primary_site,
                drug_names=args.drug_name,
                program_ids=args.program_id
            )

        else:
            # --- Direct Katsu Query ---
            print("\n=== Mode: Clinical filters only ===")
            katsu_payload = build_clinical_request_payload(
                treatment_types=args.treatment_type,
                primary_sites=args.primary_site,
                drug_names=args.drug_name,
                program_ids=args.program_id
            )

        # --- Execute Katsu Query (Common logic) ---
        if not katsu_payload:
            print("Error: Failed to build a valid Katsu request payload.")
            sys.exit(1)

        print("\n--- Step 2: Querying Katsu ---")
        katsu_federation_results = fetch_federation_data(federation_url, headers, katsu_payload, args.timeout)

        # --- Process Katsu Response ---
        clinical_payload = None
        if katsu_federation_results is None:
            print("Katsu request via federation failed. No CSV files written.")
        elif not katsu_federation_results:
             print("Katsu federation response list is empty (no sources reported data?). No CSV files written.")
        else:
            aggregated_results: Dict[str, List[Dict[str, Any]]] = {}
            sources_with_data = 0
            for katsu_source_response in katsu_federation_results:
                if katsu_source_response.get("error"):
                    print(f"Warning: Skipping source due to error: {katsu_source_response['error']}")
                    continue

                source_results = katsu_source_response.get("results")
                if not isinstance(source_results, dict):
                    print(f"Warning: Skipping source, 'results' is not a dictionary ({type(source_results)}).")
                    continue
                if not source_results:
                    continue

                sources_with_data += 1
                # Merge results by category
                for category, records in source_results.items():
                    if isinstance(records, list):
                        if category not in aggregated_results:
                            aggregated_results[category] = []
                        aggregated_results[category].extend(records)
                    else:
                        print(f"Warning: '{category}: {records}' in source response is not a list, skipping.")

            if sources_with_data > 0:
                 clinical_payload = aggregated_results
                #  print(f"Aggregated data from {sources_with_data} source(s).")
            elif not aggregated_results:
                 print("No data found matching Katsu criteria across all sources.")


        # --- Write CSVs if data was retrieved ---
        if clinical_payload:
            convert_json_to_csv(clinical_payload, args.output_dir)
        else:
            pass

        print("\n=== Download Complete ===")
        if clinical_payload:
            print(f"Katsu CSV files written to: {os.path.abspath(args.output_dir)}")


    elif args.htsget_download:
        print("\n=== Mode: HTSget Download ===")
        success = download_htsget_data(
            htsget_base_url=args.base_url,
            sample_id=args.sample_id,
            output_dir=args.htsget_output_dir,
            token=auth_token,
            timeout=args.timeout,
            chrom=args.chrom,
            start=args.start,
            end=args.end
        )
        
        if success:
            print("\n=== Downloading Complete ===")
        else:
            print("HTSget download failed.")
            sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)
