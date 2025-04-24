# katsu_utils.py
"""Utilities for Katsu interactions."""

import csv
import json
import os
import sys
from typing import Dict, Any, Optional, List
import config 


def build_clinical_request_payload(
    biosample_ids: Optional[List[str]] = None,
    treatment_types: Optional[List[str]] = None,
    primary_sites: Optional[List[str]] = None,
    drug_names: Optional[List[str]] = None,
    program_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Builds the payload for the Katsu clinical data download request."""
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
        print(f"Building clinical data request with filters: {', '.join(filter_descriptions)}...")
    else:
        print("Building request for ALL clinical data (no filters)...")

    return {
        "path": config.CLINICAL_SERVICE_ENDPOINT,
        "payload": katsu_target_payload,
        "method": "POST",
        "service": config.CLINICAL_SERVICE,
    }


def aggregate_katsu_results(
    katsu_federation_results: Optional[List[Dict[str, Any]]]
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Aggregates Katsu results from multiple federated sources."""
    if not katsu_federation_results:
        return None

    aggregated_results: Dict[str, List[Dict[str, Any]]] = {}
    sources_with_data = 0
    for katsu_source_response in katsu_federation_results:
        if katsu_source_response.get("error"):
            # Error already logged by fetch_federation_data
            print(f"Skipping source due to reported error: {katsu_source_response.get('source', 'Unknown Source')}")
            continue

        source_results = katsu_source_response.get("results")
        if not isinstance(source_results, dict):
            # print(f"Warning: Skipping source, 'results' is not a dictionary ({type(source_results)}).")
            continue # Silently skip if results isn't a dict or is empty

        data_found_in_source = False
        for category, records in source_results.items():
            if isinstance(records, list) and records: # Only process non-empty lists
                if category not in aggregated_results:
                    aggregated_results[category] = []
                aggregated_results[category].extend(r for r in records if isinstance(r, dict)) # Ensure records are dicts
                data_found_in_source = True
            # else: # Optional: Warn about non-list categories
            #    print(f"Warning: Category '{category}' in source response is not a list, skipping.")

        if data_found_in_source:
            sources_with_data += 1

    if sources_with_data > 0:
        print(f"Aggregated data from {sources_with_data} source(s).")
        return aggregated_results
    else:
        print("No data found matching clinical data criteria across all sources.")
        return None


def write_katsu_csvs(clinical_payload: Dict[str, List[Dict[str, Any]]], output_dir: str):
    """Writes aggregated Katsu clinical data into multiple CSV files."""
    print(f"\nWriting clinical data to CSV files in '{output_dir}'...")
    if not clinical_payload:
        print("No aggregated clinical data provided to write.")
        return

    os.makedirs(output_dir, exist_ok=True)
    files_written = 0

    for category, records_list in clinical_payload.items():
        if not records_list: # Skip empty categories after aggregation
            continue

        # Assume records are already filtered to be dicts by aggregate_katsu_results
        valid_records = records_list

        # Sanitize category name for filename
        safe_category = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in category)
        filename = os.path.join(output_dir, f"{safe_category}.csv")

        # Determine fieldnames from all valid records in the list
        fieldnames_set = set()
        for record in valid_records:
            fieldnames_set.update(record.keys())
        sorted_fieldnames = sorted(list(fieldnames_set))
        if not sorted_fieldnames: # Skip if somehow category has dicts but they are all empty
             print(f"Skipping category '{category}': No fields found in records.")
             continue

        print(f"Writing {len(valid_records)} records for category '{category}' to {filename}...")
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=sorted_fieldnames, extrasaction='ignore')
                writer.writeheader()
                for record in valid_records:
                    # Process record: flatten dicts/lists to JSON strings for CSV compatibility
                    processed_record = {}
                    for key in sorted_fieldnames:
                        value = record.get(key) # Use .get() for safety
                        if isinstance(value, (dict, list)):
                            try:
                                processed_record[key] = json.dumps(value)
                            except TypeError:
                                # print(f"Warning: Could not JSON serialize value for key '{key}' in {filename}. Writing as string.")
                                processed_record[key] = str(value) # Fallback to string representation
                        elif value is not None:
                            processed_record[key] = value
                        else:
                            processed_record[key] = "" # Write empty string for None/missing values
                    writer.writerow(processed_record)
            files_written += 1
        except IOError as e:
            print(f"Error writing file {filename}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"An unexpected error occurred while writing {filename}: {e}", file=sys.stderr)

    if files_written > 0:
        print(f"--- Finished writing {files_written} CSV file(s) from clinical data ---")
    else:
        print("--- No CSV files were written (payload might have been empty or contained no processable data) ---")