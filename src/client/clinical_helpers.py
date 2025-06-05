import csv
import json
import logging
import os
from typing import Any, Dict, List, Optional

from client import config
from tqdm import tqdm

logger = logging.getLogger(__name__)


def build_clinical_request_payload(
    biosample_ids: Optional[List[str]] = None,
    treatment_types: Optional[List[str]] = None,
    primary_sites: Optional[List[str]] = None,
    drug_names: Optional[List[str]] = None,
    program_ids: Optional[List[str]] = None,
    summary_only: bool = False,
) -> Dict[str, Any]:
    """Builds the payload for the clinical data download request."""
    clinical_target_payload: Dict[str, List[str]] = {}
    filter_descriptions = []

    if biosample_ids:
        clinical_target_payload["biosample_id"] = biosample_ids
        filter_descriptions.append(f"{len(biosample_ids)} BioSample IDs")
    if treatment_types:
        clinical_target_payload["treatment_type"] = treatment_types
        filter_descriptions.append(f"{len(treatment_types)} Treatment Types")
    if primary_sites:
        clinical_target_payload["primary_site"] = primary_sites
        filter_descriptions.append(f"{len(primary_sites)} Primary Sites")
    if drug_names:
        clinical_target_payload["systemic_therapy_drug_name"] = drug_names
        filter_descriptions.append(f"{len(drug_names)} Drug Names")
    if program_ids:
        clinical_target_payload["program_id"] = program_ids
        filter_descriptions.append(f"{len(program_ids)} Program IDs")
    # for dry run mode
    if summary_only:
        clinical_target_payload["summary_only"] = True
        filter_descriptions.append("Summary Only")

    if filter_descriptions:
        logger.info(
            f"Building clinical data request with filters: {', '.join(filter_descriptions)}..."
        )
    else:
        logger.info("Building request for ALL clinical data (no filters)...")

    return {
        "path": config.CLINICAL_SERVICE_ENDPOINT,
        "payload": clinical_target_payload,
        "method": "POST",
        "service": config.CLINICAL_SERVICE,
    }


def aggregate_clinical_results(
    clinical_federation_results: Optional[List[Dict[str, Any]]],
    is_clinical_dry_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """Aggregates results from multiple federated sources.
    For dry runs, aggregates summary counts from all sources.
    For normal runs, aggregates the actual data records.
    """

    if is_clinical_dry_run:
        total_counts = {
            "biomarkers": 0,
            "comorbidities": 0,
            "donors": 0,
            "exposures": 0,
            "follow_ups": 0,
            "primary_diagnoses": 0,
            "radiations": 0,
            "sample_registrations": 0,
            "specimens": 0,
            "surgeries": 0,
            "systemic_therapies": 0,
            "treatments": 0,
        }
        sources_with_data = 0

        for clinical_source_response in clinical_federation_results:
            source_name = clinical_source_response.get("location", {}).get(
                "name", "Unknown Source"
            )
            if clinical_source_response.get("error"):
                logger.warning(
                    f"Skipping source {source_name} due to reported error: {clinical_source_response.get('source', 'Unknown Source')}"
                )
                continue

            source_summary = clinical_source_response.get("results", {}).get(
                "summary", {}
            )
            if not source_summary:
                logger.warning(
                    f"Warning: Skipping source {source_name}, no summary data found"
                )
                continue

            record_counts = source_summary.get("record_counts", {})
            if record_counts:
                for category, count in record_counts.items():
                    total_counts[category] += count
                sources_with_data += 1
                logger.info(
                    f"Source {source_name} summary: {source_summary.get('message', 'No message')}"
                )

        if sources_with_data > 0:
            logger.info(f"Aggregated summary from {sources_with_data} source(s).")
            return {
                "summary": {
                    "message": f"Total records across {sources_with_data} sources",
                    "record_counts": total_counts,
                }
            }
        else:
            logger.info(
                "No summary data found matching clinical data criteria across all sources."
            )
            return None

    aggregated_results: Dict[str, List[Dict[str, Any]]] = {}
    sources_with_data = 0
    for clinical_source_response in clinical_federation_results:
        source_name = clinical_source_response.get("location", {}).get(
            "name", "Unknown Source"
        )
        if clinical_source_response.get("error"):
            logger.warning(
                f"Skipping source {source_name} due to reported error: {clinical_source_response.get('source', 'Unknown Source')}"
            )
            continue

        source_results = clinical_source_response.get("results", {}).get("data", {})
        if not source_results:
            logger.warning(f"Warning: Skipping source {source_name}, data is empty")
            continue

        data_found_in_source = False
        for category, records in source_results.items():
            if isinstance(records, list) and records:
                if category not in aggregated_results:
                    aggregated_results[category] = []
                aggregated_results[category].extend(
                    r for r in records if isinstance(r, dict)
                )
                data_found_in_source = True

        if data_found_in_source:
            sources_with_data += 1

    if sources_with_data > 0:
        logger.info(f"Aggregated data from {sources_with_data} source(s).")
        return aggregated_results
    else:
        logger.warning(
            "No data found matching clinical data criteria across all sources."
        )
        return None


def write_clinical_csvs(
    clinical_payload: Dict[str, List[Dict[str, Any]]], output_dir: str
):
    """Writes aggregated clinical data into CSV files."""
    logger.info(f"\nWriting clinical data to CSV files in '{output_dir}'...")
    if not clinical_payload:
        logger.info("No aggregated clinical data provided to write.")
        return

    os.makedirs(output_dir, exist_ok=True)
    files_written = 0

    categories = [cat for cat, records in clinical_payload.items() if records]
    with tqdm(total=len(categories), desc="Writing CSV files", unit="file") as pbar:
        for category, records_list in clinical_payload.items():
            if not records_list:  # Skip empty categories after aggregation
                continue
            valid_records = records_list

            # Sanitize category name for filename
            safe_category = "".join(
                c if c.isalnum() or c in ("_", "-") else "_" for c in category
            )
            filename = os.path.join(output_dir, f"{safe_category}.csv")

            # Determine fieldnames from all valid records in the list
            fieldnames_set = set()
            for record in valid_records:
                fieldnames_set.update(record.keys())
            sorted_fieldnames = sorted(list(fieldnames_set))
            if not sorted_fieldnames:
                logger.info(
                    f"Skipping category '{category}': No fields found in records."
                )
                pbar.update(1)
                continue

            logger.debug(
                f"Writing {len(valid_records)} records for category '{category}' to {filename}..."
            )
            try:
                with open(filename, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.DictWriter(
                        csvfile, fieldnames=sorted_fieldnames, extrasaction="ignore"
                    )
                    writer.writeheader()
                    for record in tqdm(
                        valid_records,
                        desc=f"Writing {category}",
                        leave=False,
                        unit="record",
                    ):
                        processed_record = {}
                        for key in sorted_fieldnames:
                            value = record.get(key)
                            if isinstance(value, (dict, list)):
                                try:
                                    processed_record[key] = json.dumps(value)
                                except TypeError:
                                    processed_record[key] = str(value)
                            elif value is not None:
                                processed_record[key] = value
                            else:
                                processed_record[key] = (
                                    ""  # Write empty string for None/missing values
                                )
                        writer.writerow(processed_record)
                files_written += 1
            except IOError as e:
                logger.error(f"Error writing file {filename}: {e}")
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred while writing {filename}: {e}"
                )
            finally:
                pbar.update(1)

    if files_written > 0:
        logger.info(
            f"--- Finished writing {files_written} CSV file(s) from clinical data ---"
        )
    else:
        logger.info("--- No CSV files were written ---")


def extract_unique_program_sample_ids_from_clinical_data(
    clinical_data: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    if not clinical_data or "sample_registrations" not in clinical_data:
        logger.warning("No clinical data provided or data is not in expected format.")
        return []

    program_sample_ids = {
        f"{sample.get('program_id')}~{sample.get('submitter_sample_id')}"
        for sample in clinical_data.get("sample_registrations", [])
        if sample.get("program_id") is not None
        and sample.get("submitter_sample_id") is not None
    }
    return list(program_sample_ids)
