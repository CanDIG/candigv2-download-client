import hashlib
import json
import logging
import sys
import csv
import pandas as pd
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from client import config
from client import genomics_helpers
import httpx
from tqdm import tqdm

logger = logging.getLogger(__name__)

VARIANT_METADATA_FILENAME = "variant_metadata.jsonl"


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[str] = None
    error: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None

    def as_dict(self):
        return {"success": self.success,
                "file_path": self.file_path,
                "error": self.error,
                "status": self.status,
                "message": self.message}


# --- Checksum Utilities ---
def calculate_checksum(file_path: Path, hash_type: str = "md5") -> Optional[str]:
    """
    Calculate checksum for a file. Supports 'md5' and 'sha256'.
    Returns hex digest or None if error or unsupported type.
    """
    hash_type = hash_type.lower()
    if hash_type not in ["md5", "sha256"]:
        logger.warning(f"Unsupported hash type for checksum calculation: {hash_type}")
        return None

    hasher = hashlib.new(hash_type)
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        logger.error(f"File not found for checksum calculation: {file_path}")
        return None
    except IOError as e:
        logger.error(f"IOError calculating checksum for {file_path}: {e}")
        return None


def verify_local_file(
    local_file_path: Path,
    expected_size: Optional[int],
    expected_checksums: List[Dict[str, str]],
) -> Tuple[str, str]:
    """
    Verifies a local file against expected size and checksums.
    Returns a status string and a message.
    Statuses:
        "MATCH_CHECKSUM"
        "MATCH_SIZE"
        "MISMATCH_CHECKSUM"
        "MISMATCH_SIZE"
        "VERIFICATION_ERROR"
        "NO_VALIDATION_CRITERIA" (no checksums and no size in metadata to verify against)
    """
    if not local_file_path.exists():
        return (
            "VERIFICATION_ERROR",
            f"Local file {local_file_path} reported as existing but not found for verification.",
        )

    try:
        local_file_size = local_file_path.stat().st_size
    except OSError as e:
        return (
            "VERIFICATION_ERROR",
            f"Could not get size of local file {local_file_path}: {e}",
        )

    # 1. Checksum validation
    if expected_checksums:
        has_verifiable_checksum_type_in_metadata = False
        for cs_entry in expected_checksums:
            cs_type = cs_entry.get("type", "").lower()
            expected_cs_value = cs_entry.get("checksum")

            if not cs_type or not expected_cs_value:
                logger.debug(
                    f"Skipping invalid checksum entry: {cs_entry} for {local_file_path}"
                )
                continue

            if cs_type in ["md5", "sha256"]:  # Supported types
                has_verifiable_checksum_type_in_metadata = True
                logger.debug(f"Verifying {local_file_path} with {cs_type} checksum...")
                local_cs_value = calculate_checksum(local_file_path, cs_type)

                if local_cs_value is None:
                    # Error during checksum calculation, can't verify with this checksum
                    logger.warning(
                        f"Could not calculate {cs_type} checksum for {local_file_path}. Skipping this checksum type."
                    )
                    continue

                if local_cs_value == expected_cs_value:
                    return (
                        "MATCH_CHECKSUM",
                        f"Validated with {cs_type} checksum: {expected_cs_value}",
                    )
            else:
                logger.debug(
                    f"Checksum type '{cs_type}' from metadata is not currently supported for verification of {local_file_path}."
                )

        if has_verifiable_checksum_type_in_metadata:
            return (
                "MISMATCH_CHECKSUM",
                "Checksums provided in metadata, but no match found with local file.",
            )

    # 2. Size validation (fallback or if no checksums)
    if expected_size is not None:
        if local_file_size == expected_size:
            return (
                "MATCH_SIZE",
                f"Local file size {local_file_size} matches expected size.",
            )
        else:
            return (
                "MISMATCH_SIZE",
                f"Local file size {local_file_size} does not match expected size {expected_size}.",
            )

    # 3. No validation
    if not expected_checksums and expected_size is None:
        return (
            "NO_VALIDATION_CRITERIA",
            "No checksums or size provided in metadata for validation.",
        )
    elif (
        expected_checksums
        and not has_verifiable_checksum_type_in_metadata
        and expected_size is None
    ):
        return (
            "NO_VALIDATION_CRITERIA",
            "Checksum types in metadata are unsupported, and no size provided.",
        )

    return "VERIFICATION_ERROR", "Verification process failed."


def execute_federation_call(
    federation_url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    progress_callback: Optional[callable] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Makes a POST request to the federation endpoint.
    Returns a list of responses from federated servers
    """
    service = payload.get("service", "unknown")
    path = payload.get("path", "N/A")
    logger.debug(f"Sending request to federation service ({path})...")

    try:
        with httpx.Client(timeout=config.TIMEOUT) as client:
            logger.debug(f"Request payload: {json.dumps(payload, indent=2)}")
            response = client.post(federation_url, headers=headers, json=payload)
            response.raise_for_status()
            logger.debug(f"Request successful (Status: {response.status_code})")
            if progress_callback:
                progress_callback()
            return response.json()
    except httpx.HTTPStatusError as e:
        print(
            f"\nHTTP error during {service} request: {e.response.status_code} for path {path}",
            file=sys.stderr,
        )
        try:
            details = e.response.json()
            print(f"Error details: {json.dumps(details, indent=2)}", file=sys.stderr)
        except json.JSONDecodeError:
            print(f"Response body: {e.response.text[:500]}...", file=sys.stderr)
        return None
    except httpx.RequestError as e:
        print(
            f"\nNetwork/connection error during {service} request for {path}: {e}",
            file=sys.stderr,
        )
        return None
    except Exception as e:
        print(
            f"\nUnexpected error during {service} request for {path}: {e}",
            file=sys.stderr,
        )
        return None


def download_file(
    url: str,
    headers: Dict[str, str],
    output_dir: Path,
    filename: str,
    show_progress: bool = True,
) -> DownloadResult:
    """Download a file from a URL."""
    try:
        output_path = output_dir / filename
        logger.debug(f"Downloading from url {url} to {output_path}")

        with httpx.stream(
            "GET", url, headers=headers, timeout=config.TIMEOUT
        ) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))

            with open(output_path, "wb") as f:
                if show_progress and total_size > 0:
                    with tqdm(
                        total=total_size,
                        unit="iB",
                        unit_scale=True,
                        desc=f" {filename[:25]:<25}..",
                        leave=False,
                        position=1,
                    ) as pbar:
                        for chunk in response.iter_raw():
                            size = f.write(chunk)
                            pbar.update(size)
                else:
                    for chunk in response.iter_raw():
                        f.write(chunk)

        logger.debug(f"Successfully downloaded {filename} to {output_path}")
        return DownloadResult(
            success=True, file_path=str(output_path), status="DOWNLOADED_SUCCESS"
        )
    except Exception as e:
        if output_path.exists():
            try:
                output_path.unlink()
                logger.debug(f"Removed partially downloaded file: {output_path}")
            except OSError as unlink_e:
                logger.warning(
                    f"Could not remove partial file {output_path}: {unlink_e}"
                )
        error_msg = f"Failed to download {filename}: {str(e)}"
        return DownloadResult(success=False, error=error_msg, status="FAILED_DOWNLOAD")


def get_download_access(drs_object_results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract download [access methods] from a DRS object [results] field."""
    try:
        access_methods = drs_object_results.get("access_methods", [])
        if not access_methods:
            logger.warning("No access methods found in DRS object results.")
            return None

        download_method = next(
            (method for method in access_methods if method.get("type") == "download"),
            None,
        )
        if not download_method:
            logger.warning("No download type found in DRS object results.")
            return None

        # access_url_obj = download_method.get("access_url", {})
        # if not access_url_obj or not access_url_obj.get("url"):
        #     logger.warning("No access URL found in download method.")
        #     return None

        return download_method
    except Exception as e:
        logger.error(f"Error extracting download access: {e}")
        return None


def should_skip_file(drs_object_results: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Check if a file download should be skipped based on size, using DRS objects results."""
    try:
        size = drs_object_results.get("size", 0)
        filename = drs_object_results.get("name", "unknown")
        if size > config.DOWNLOAD_MAX_SIZE:
            error_msg = f"File {filename} ({size} bytes) exceeds size limit ({config.DOWNLOAD_MAX_SIZE} bytes)."
            return True, error_msg
        return False, None
    except Exception as e:
        logger.warning(
            f"Error checking file size for {drs_object_results.get('name', 'unknown')}: {e}"
        )
        return False, None


def extract_file_metadata_from_drs_results(
    drs_object_results: Dict[str, Any], target_relative_dir: Path
) -> Dict[str, Any]:
    """
    Extracts metadata needed for download from a DRS objects results field.
    """
    metadata_entry: Dict[str, Any] = {
        "filename": drs_object_results.get("name", "unknown_file"),
        "size": drs_object_results.get("size"),  # Keep as None if not present
        "checksums": drs_object_results.get("checksums", []),
        "target_output_dir": str(target_relative_dir),  # Relative to session_dir
        "download_url": None,
        "error": None,
    }

    if metadata_entry["size"] is not None:
        try:
            metadata_entry["size"] = int(metadata_entry["size"])
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid size '{drs_object_results.get('size')}' for {metadata_entry['filename']}. Setting size to None."
            )
            metadata_entry["size"] = None

    skip, skip_error = should_skip_file(drs_object_results)
    if skip:
        metadata_entry["error"] = skip_error
        logger.warning(
            f"Marking file for skip: {metadata_entry['filename']} - {skip_error}"
        )
        return metadata_entry

    download_access_method = get_download_access(drs_object_results)
    if not download_access_method:
        error_msg = f"No suitable download access method found for {metadata_entry['filename']}."
        metadata_entry["error"] = error_msg
        logger.warning(error_msg)
        return metadata_entry

    access_url = download_access_method.get("access_url", {}).get("url")
    if not access_url:
        error_msg = (
            f"No download URL in access method for {metadata_entry['filename']}."
        )
        metadata_entry["error"] = error_msg
        logger.warning(error_msg)
        return metadata_entry

    metadata_entry["download_url"] = access_url
    return metadata_entry


def collect_metadata_for_file_item(
    file_item_info: Dict[str, Any],
    file_type_label: str,
    federation_headers: Dict[str, str],
    federation_url: str,
    target_relative_dir: Path,
    is_dry_run: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Sends a request to the federation endpoint to retrieve DRS object
    for the specified file item. It processes the federation responses, extracts relevant
    file metadata (such as filename, size, checksums, and download URL)
    """
    if not file_item_info or not file_item_info.get("name"):
        logger.warning(f"Invalid file_item_info provided for {file_type_label}.")
        return None

    drs_object_name = file_item_info["name"]
    logger.debug(f"Collecting metadata for {file_type_label} file: {drs_object_name}")

    payload = genomics_helpers.build_file_drs_request_payload(drs_object_name)
    federation_responses = execute_federation_call(
        federation_url=federation_url,
        headers=federation_headers,
        payload=payload,
    )

    if not federation_responses:
        logger.warning(f"No federation response for FileDRS object: {drs_object_name}.")
        return None

    for fed_response_item in federation_responses:
        drs_object_results = fed_response_item.get("results")
        location = fed_response_item.get("location", "unknown location")

        if not drs_object_results or not isinstance(drs_object_results, dict):
            logger.debug(
                f"Skipping federation response from {location} for {drs_object_name}: missing/invalid 'results' field."
            )
            continue

        if drs_object_results.get("name") != drs_object_name:
            logger.debug(
                f"Skipping DRS object from {location}: name mismatch ('{drs_object_results.get('name')}' vs '{drs_object_name}')."
            )
            continue

        file_metadata = extract_file_metadata_from_drs_results(
            drs_object_results=drs_object_results,
            target_relative_dir=target_relative_dir,
        )

        if file_metadata.get("error"):
            logger.warning(
                f"Error processing metadata for {drs_object_name} from {location}: {file_metadata['error']}"
            )
            continue

        if is_dry_run:
            size_bytes = file_metadata.get("size", "N/A")
            url = file_metadata.get("download_url", "N/A")
            logger.debug(
                f"DRY-RUN (metadata collection): Candidate {file_metadata['filename']} ({size_bytes} bytes). URL: {url}. Target_Rel_Dir: {target_relative_dir}"
            )

        return file_metadata

    logger.error(
        f"Failed to collect valid metadata for {file_type_label} file: {drs_object_name} from any source."
    )
    return None


def collect_metadata_for_analysis_drs_objects(
    analysis_drs_federation_response: List[Dict[str, Any]],
    federation_headers: Dict[str, str],
    federation_url: str,
    target_relative_dir_for_sample: Path,
    is_dry_run: bool = False,
    tqdm_position: int = 1,
) -> tuple[List[Dict[str, Any]], bool]:
    collected_files_metadata = []
    any_sequence_variation_found_in_responses = False

    for fed_resp_item_for_analysis_drs in tqdm(
        analysis_drs_federation_response,
        desc=" Checking AnalysisDRS sources",
        leave=False,
        position=tqdm_position,
    ):
        analysis_drs_results = fed_resp_item_for_analysis_drs.get("results")
        location = fed_resp_item_for_analysis_drs.get("location", "unknown location")

        if not analysis_drs_results or not isinstance(analysis_drs_results, dict):
            logger.debug(
                f"Skipping AnalysisDRS from {location}: missing/invalid 'results'."
            )
            continue

        if (
            analysis_drs_results.get("metadata", {}).get("analysis_type")
            != "sequence_variation"
        ):
            logger.debug(
                f"Skipping non-sequence variation AnalysisDRS object '{analysis_drs_results.get('name', 'N/A')}' from {location}."
            )
            continue

        any_sequence_variation_found_in_responses = True
        logger.debug(
            f"Processing sequence variation AnalysisDRS '{analysis_drs_results.get('name', 'N/A')}' from {location}."
        )

        contents = analysis_drs_results.get("contents", [])
        processed_content_for_this_source = False

        analysis_file_item = next(
            (item for item in contents if item.get("id") == "analysis"), None
        )
        if analysis_file_item:
            metadata = collect_metadata_for_file_item(
                file_item_info=analysis_file_item,
                file_type_label="analysis",
                federation_headers=federation_headers,
                federation_url=federation_url,
                target_relative_dir=target_relative_dir_for_sample,
                is_dry_run=is_dry_run,
            )
            if metadata:
                collected_files_metadata.append(metadata)
                processed_content_for_this_source = True

        index_file_item = next(
            (item for item in contents if item.get("id") == "index"), None
        )
        if index_file_item:
            metadata = collect_metadata_for_file_item(
                file_item_info=index_file_item,
                file_type_label="index",
                federation_headers=federation_headers,
                federation_url=federation_url,
                target_relative_dir=target_relative_dir_for_sample,
                is_dry_run=is_dry_run,
            )
            if metadata:
                collected_files_metadata.append(metadata)
                processed_content_for_this_source = True

        if processed_content_for_this_source:
            logger.debug(
                f"Collected metadata from AnalysisDRS '{analysis_drs_results.get('name', 'N/A')}' at {location}."
            )
            break

    unique_metadata_list = []
    seen_keys = set()
    for meta_item in collected_files_metadata:
        if not meta_item.get("error"):
            key = (
                meta_item["filename"],
                meta_item["target_output_dir"],
                meta_item["download_url"],
            )
            if key not in seen_keys:
                unique_metadata_list.append(meta_item)
                seen_keys.add(key)
        else:
            unique_metadata_list.append(meta_item)

    return unique_metadata_list, any_sequence_variation_found_in_responses


def get_programs_in_genomics(
    federation_url: str,
    federation_headers: Dict[str, str],
) -> List[str]:
    """
    Queries all federated genomics services for available program names.
    """
    payload = {
        "path": config.DRS_PROGRAM_ENDPOINT,
        "payload": {},
        "method": "GET",
        "service": config.GENOMICS_SERVICE,
    }

    response_list = execute_federation_call(
        federation_url=federation_url,
        headers=federation_headers,
        payload=payload,
    )
    logger.debug(
        f"get_programs_in_genomics response: {json.dumps(response_list, indent=2)}"
    )

    genomic_programs = set()
    for location_data in response_list:
        if location_data.get("status") == 200 and location_data.get("results"):
            programs_from_loc = location_data["results"]
            if isinstance(programs_from_loc, list):
                genomic_programs.update(
                    p for p in programs_from_loc if isinstance(p, str)
                )
            else:
                logger.error(
                    f"Unexpected format for programs from {location_data.get('location', 'N/A')}: {type(programs_from_loc)}"
                )

    return list(genomic_programs)


def collect_all_variant_metadata(
    program_sample_ids: List[str],
    federation_headers: Dict[str, str],
    federation_url: str,
    is_dry_run: bool = False,
):
    """
    Collects metadata for all provided program-sample IDs.

    For each program-sample ID:
      - Verifies the program is available in the genomics federation.
      - Queries the federation for experiment and analysis DRS objects.
      - For each sequence variation analysis found, collects file metadata (e.g., filename, size, checksums, download URL).
    """
    all_files_metadata_accumulator: List[Dict[str, Any]] = []

    genomics_programs = get_programs_in_genomics(federation_url, federation_headers)
    if not genomics_programs:
        logger.error("No drs programs returned from any federated genomic service.")
        return []
    logger.info(
        f"Found {len(genomics_programs)} relevant programs: {', '.join(genomics_programs)}"
    )

    variants_output_parent_dir_name = "variant_data"

    valid_program_ids_to_process = []
    experiment_metadata_dict = {}
    analysis_metadata_dict = {}
    for ps_id in program_sample_ids:
        try:
            program_id, _ = ps_id.split("~", 1)
            if program_id in genomics_programs:
                valid_program_ids_to_process.append(ps_id)
            else:
                logger.debug(
                    f"Skipping '{ps_id}': program '{program_id}' not in fetched genomics programs list."
                )
        except ValueError:
            logger.error(f"Invalid program_sample_id format: '{ps_id}'. Skipping.")

    if not valid_program_ids_to_process:
        logger.warning("No valid program-sample IDs remaining after filtering.")
        return []

    logger.info(
        f"Processing {len(valid_program_ids_to_process)} program-sample IDs for metadata collection."
    )
    for program_sample_id in tqdm(
        valid_program_ids_to_process,
        desc="Samples metadata scan",
        unit="sample",
        position=0,
    ):
        try:
            program_id, sample_id = program_sample_id.split("~", 1)
        except ValueError:
            continue

        exp_payload = genomics_helpers.build_experiment_request_payload(
            program_id, sample_id
        )
        exp_fed_responses = execute_federation_call(
            federation_url, federation_headers, exp_payload
        )

        sample_has_any_seq_var_data = False
        metadata_for_current_sample = []

        if exp_fed_responses:
            for fed_resp_item_exp in exp_fed_responses:
                exp_loc = fed_resp_item_exp.get("location", "N/A")
                experiment_results_list = fed_resp_item_exp.get("results", [])

                for experiment_obj in experiment_results_list:
                    experiment_metadata_dict[program_sample_id] = experiment_obj.get("metadata")
                    experiment_metadata_dict[program_sample_id]["experiment_id"] = experiment_obj.get('id')
                    experiment_metadata_dict[program_sample_id]["program_id"] = experiment_obj.get('program')
                    experiment_metadata_dict[program_sample_id]["submitter_sample_id"] = experiment_obj.get('name')
                    for content_item in experiment_obj.get("contents", []):
                        analysis_drs_name = content_item.get("name")
                        if not analysis_drs_name:
                            continue

                        logger.debug(
                            f"Fetching AnalysisDRS '{analysis_drs_name}' for {program_sample_id} from {exp_loc}"
                        )
                        analysis_drs_payload = (
                            genomics_helpers.build_analysis_drs_request_payload(
                                analysis_drs_name
                            )
                        )
                        analysis_drs_fed_resp = execute_federation_call(
                            federation_url, federation_headers, analysis_drs_payload
                        )

                        if analysis_drs_fed_resp:
                            relative_sample_files_dir = (
                                Path(variants_output_parent_dir_name)
                                / f"{program_id}"
                            )

                            analysis_obj_results = analysis_drs_fed_resp[0]['results']['id']
                            analysis_metadata_dict[analysis_obj_results] = analysis_drs_fed_resp[0]['results']['metadata']
                            analysis_metadata_dict[analysis_obj_results]["file_id"] = analysis_obj_results
                            analysis_metadata_dict[analysis_obj_results]["program"] = analysis_drs_fed_resp[0]['results']['program']
                            analysis_metadata_dict[analysis_obj_results]["reference_genome"] = \
                            analysis_drs_fed_resp[0]['results']['reference_genome']
                            for linked_obj in analysis_drs_fed_resp[0]['results'].get('contents'):
                                if linked_obj['id'] not in ['analysis', 'index']:
                                    try:
                                        (analysis_metadata_dict[analysis_obj_results]['samples']
                                         .append({
                                                    "submitter_sample_id": linked_obj['name'],
                                                    "analysis_sample_id": linked_obj['id'],
                                                    "experiment_id": linked_obj['drs_uri'][0].rsplit('/', 1)[-1]
                                                }))
                                    except KeyError as e:
                                        analysis_metadata_dict[analysis_obj_results]['samples'] = \
                                            [
                                                {
                                                    "submitter_sample_id": linked_obj['name'],
                                                    "analysis_sample_id": linked_obj['id'],
                                                    "experiment_id": linked_obj['drs_uri'][0].rsplit('/', 1)[-1]
                                                }
                                            ]
                                else:
                                    try:
                                        analysis_metadata_dict[analysis_obj_results]['files'].append(linked_obj['drs_uri'][0].rsplit('/', 1)[-1])
                                    except KeyError as e:
                                        analysis_metadata_dict[analysis_obj_results]['files'] = [linked_obj['drs_uri'][0].rsplit('/', 1)[-1]]

                            files_meta, has_seq_var_in_obj = (
                                collect_metadata_for_analysis_drs_objects(
                                    analysis_drs_fed_resp,
                                    federation_headers,
                                    federation_url,
                                    relative_sample_files_dir,
                                    is_dry_run,
                                    tqdm_position=1,
                                )
                            )
                            if has_seq_var_in_obj:
                                sample_has_any_seq_var_data = True
                                metadata_for_current_sample.extend(files_meta)
                                if files_meta:
                                    logger.debug(
                                        f"Collected {len(files_meta)} file metadata entries for AnalysisDRS '{analysis_drs_name}'."
                                    )
                        else:
                            logger.warning(
                                f"No federation response for AnalysisDRS: {analysis_drs_name}"
                            )

        if sample_has_any_seq_var_data and metadata_for_current_sample:
            logger.debug(
                f"Found sequence variation data for {program_sample_id}. Adding {len(metadata_for_current_sample)} file(s)."
            )
            all_files_metadata_accumulator.extend(metadata_for_current_sample)
        elif sample_has_any_seq_var_data:
            logger.debug(
                f"Sequence variation analysis found for {program_sample_id}, but no downloadable files' metadata collected."
            )
        else:
            logger.debug(
                f"No sequence variation data found for sample {program_sample_id}."
            )

    all_exp_metadata = {"experiment_metadata": experiment_metadata_dict,
                        "analysis_metadata": analysis_metadata_dict}
    final_unique_metadata_list = []
    seen_keys_final = set()
    for meta_item in all_files_metadata_accumulator:
        if not meta_item.get("error"):
            key = (
                meta_item["filename"],
                meta_item["target_output_dir"],
                meta_item["download_url"],
            )
            if key not in seen_keys_final:
                final_unique_metadata_list.append(meta_item)
                seen_keys_final.add(key)
        else:
            final_unique_metadata_list.append(meta_item)

    return final_unique_metadata_list, all_exp_metadata


def write_experiment_metadata_to_csv(
        experiment_metadata: Dict, metadata_file_path: Path
) -> None:
    try:
        metadata_file_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(
            f"Could not create parent directory for metadata file {metadata_file_path}: {e}"
        )
        return

    if not experiment_metadata:
        logger.info(f"No new metadata entries to append to {metadata_file_path}.")
        try:
            with open(metadata_file_path, "a") as f:
                pass
        except IOError as e:
            logger.error(
                f"Could not ensure metadata file {metadata_file_path} exists: {e}"
            )
        return
    try:
        df = pd.DataFrame.from_dict(experiment_metadata, orient="index")
        df.to_csv(metadata_file_path, index=False)
        logger.debug(
            f"Successfully wrote experiment metadata entries to {metadata_file_path}"
        )
    except IOError as e:
        logger.error(f"Failed to append metadata to {metadata_file_path}: {e}")


def write_variant_metadata_to_file(
    files_metadata_list: List[Dict[str, Any]], metadata_file_path: Path
) -> None:
    try:
        metadata_file_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(
            f"Could not create parent directory for metadata file {metadata_file_path}: {e}"
        )
        return

    if not files_metadata_list:
        logger.info(f"No new metadata entries to append to {metadata_file_path}.")
        try:
            with open(metadata_file_path, "a") as f:
                pass
        except IOError as e:
            logger.error(
                f"Could not ensure metadata file {metadata_file_path} exists: {e}"
            )
        return

    try:
        with open(metadata_file_path, "a") as f:
            for metadata_entry in files_metadata_list:
                f.write(json.dumps(metadata_entry) + "\n")
        logger.debug(
            f"Successfully appended {len(files_metadata_list)} metadata entries to {metadata_file_path}"
        )
    except IOError as e:
        logger.error(f"Failed to append metadata to {metadata_file_path}: {e}")


# --- Phase 2: Downloading from Metadata ---


def download_files_from_collected_metadata(
    files_metadata_list: List[Dict[str, Any]],
    download_headers: Dict[str, str],
    session_dir: Path,
) -> List[DownloadResult]:
    """
    For each metadata entry, this function:
      - Validates required fields (filename, download_url, target_output_dir).
      - Checks if the file already exists at the target location:
        - If so, verifies the file using checksum and/or size.
        - If verification passes, skips download and records the result.
        - If verification fails, attempts to re-download and updates the result accordingly.
      - If the file does not exist, downloads the file
    """
    all_download_results: List[DownloadResult] = []

    if not files_metadata_list:
        logger.info("No files to download based on provided metadata list.")
        return []

    valid_metadata_to_process = []
    for meta_entry in files_metadata_list:
        filename = meta_entry.get("filename", "unknown_file")
        if meta_entry.get("error"):
            msg = f"Skipping download for '{filename}' due to pre-existing error in metadata: {meta_entry['error']}"
            logger.warning(msg)
            all_download_results.append(
                DownloadResult(
                    success=False,
                    file_path=None,
                    error=meta_entry["error"],
                    status="ERROR_IN_METADATA",
                    message=msg,
                )
            )
            continue
        if not all(
            [
                meta_entry.get("download_url"),
                filename,
                meta_entry.get("target_output_dir"),
            ]
        ):
            error_msg = f"Incomplete metadata for '{filename}'. Skipping."
            logger.error(error_msg)
            all_download_results.append(
                DownloadResult(
                    success=False,
                    file_path=None,
                    error=error_msg,
                    status="INCOMPLETE_METADATA",
                    message=error_msg,
                )
            )
            continue
        valid_metadata_to_process.append(meta_entry)

    if not valid_metadata_to_process:
        logger.info(
            "No valid files to download after filtering metadata based on errors or incompleteness."
        )
        return all_download_results

    logger.debug(
        f"Processing {len(valid_metadata_to_process)} file entries for download/verification..."
    )

    for meta_entry in tqdm(
        valid_metadata_to_process,
        desc="Overall file processing",
        unit="file",
        position=0,
    ):
        download_url = meta_entry["download_url"]
        filename = meta_entry["filename"]
        relative_output_dir_str = meta_entry["target_output_dir"]
        expected_size = meta_entry.get("size")
        expected_checksums = meta_entry.get("checksums", [])

        absolute_output_dir = session_dir / Path(relative_output_dir_str)
        absolute_output_path = absolute_output_dir / filename

        try:
            absolute_output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            error_msg = f"Failed to create output directory {absolute_output_dir} for {filename}: {e}"
            logger.error(error_msg)
            all_download_results.append(
                DownloadResult(
                    success=False,
                    file_path=None,
                    error=error_msg,
                    status="FAILED_MKDIR",
                    message=error_msg,
                )
            )
            continue

        should_download = True
        is_redownload_attempt = False
        verification_failure_reason = ""

        if absolute_output_path.exists():
            is_redownload_attempt = (
                True  # Tentatively, will be set to False if validation passes
            )
            logger.debug(f"File {absolute_output_path} exists. Verifying...")
            verification_status, verification_msg = verify_local_file(
                absolute_output_path, expected_size, expected_checksums
            )
            logger.debug(
                f"Verification result for {filename}: {verification_status} - {verification_msg}"
            )

            if verification_status == "MATCH_CHECKSUM":
                all_download_results.append(
                    DownloadResult(
                        success=True,
                        file_path=str(absolute_output_path),
                        status="SKIPPED_CHECKSUM_MATCH",
                        message=verification_msg,
                    )
                )
                should_download = False
            elif verification_status == "MATCH_SIZE":
                all_download_results.append(
                    DownloadResult(
                        success=True,
                        file_path=str(absolute_output_path),
                        status="SKIPPED_SIZE_MATCH",
                        message=verification_msg,
                    )
                )
                should_download = False
            else:  # MISMATCH_CHECKSUM, MISMATCH_SIZE, VERIFICATION_ERROR
                verification_failure_reason = (
                    f"{verification_status} - {verification_msg}"
                )
                logger.warning(
                    f"Local file {filename} verification failed. Will attempt re-download. Reason: {verification_failure_reason}"
                )

        if should_download:
            download_result = download_file(
                url=download_url,
                headers=download_headers,
                output_dir=absolute_output_dir,
                filename=filename,
            )

            if is_redownload_attempt:
                # Augment status for re-downloads
                if download_result.success:
                    download_result.status = (
                        f"REDOWNLOADED_SUCCESS_AFTER_{verification_status.upper()}"
                    )
                    download_result.message = f"Successfully re-downloaded. Original issue: {verification_failure_reason}"
                else:  # Re-download failed
                    download_result.status = (
                        f"FAILED_REDOWNLOAD_AFTER_{verification_status.upper()}"
                    )
                    download_result.error = f"Re-download attempt failed. Original issue: {verification_failure_reason}. New error: {download_result.error}"

            all_download_results.append(download_result)
            if not download_result.success:
                logger.error(
                    f"Download failed for {filename}. Details: {download_result.error}"
                )

    return all_download_results


def read_metadata_from_file(metadata_file_path: Path) -> List[Dict[str, Any]]:
    """
    Reads metadata entries from a .jsonl file.
    """
    if not metadata_file_path.exists():
        logger.info(
            f"Metadata file {metadata_file_path} not found. Returning empty list."
        )
        return []

    metadata_list = []
    try:
        with open(metadata_file_path, "r") as f:
            for i, line in enumerate(f):
                try:
                    stripped_line = line.strip()
                    if not stripped_line:
                        continue
                    metadata_list.append(json.loads(stripped_line))
                except json.JSONDecodeError as e:
                    logger.error(
                        f"Skipping invalid JSON line {i + 1} in {metadata_file_path}: '{stripped_line}' - Error: {e}"
                    )
        logger.debug(
            f"Read {len(metadata_list)} metadata entries from {metadata_file_path}"
        )
        return metadata_list
    except IOError as e:
        logger.error(f"Failed to read metadata file {metadata_file_path}: {e}")
        return []


# --- Session Management ---
def get_download_session_dir(base_download_path_str: str = "candig_downloads") -> Path:
    """
    Creates and returns a unique session folder for downloads.
    """
    base_dir = Path(base_download_path_str)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = base_dir / timestamp
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Download session directory created: {session_dir}")
    except OSError as e:
        logger.critical(f"FATAL: Failed to create session directory {session_dir}: {e}")
        raise
    return session_dir


def run_variant_download_pipeline(
    program_sample_ids: Optional[List[str]],
    federation_headers: Dict[str, str],
    download_headers: Dict[str, str],
    federation_url: str,
    session_dir: Path,
    is_dry_run: bool = False,
) -> bool:
    """
    This function performs two main phases:
        1. Metadata Collection: collects variant file metadata and appends it to the log file.
        2. Downloading: Reads all pending metadata entries from the log file and downloads

    If is_dry_run is True, simulates the download and verification process, printing and logging the file size
    """
    if not session_dir.exists() or not session_dir.is_dir():
        logger.critical(
            f"Session directory {session_dir} is invalid. Aborting pipeline."
        )
        return False

    variant_metadata_log_path = session_dir / VARIANT_METADATA_FILENAME
    logger.info(f"Variant file metadata log: {variant_metadata_log_path}")

    newly_collected_metadata: List[Dict[str, Any]] = []
    if program_sample_ids:
        logger.debug(
            f"PHASE 1: Collecting new metadata for {len(program_sample_ids)} program-sample ID(s)..."
        )
        all_metadata = collect_all_variant_metadata(
            program_sample_ids=program_sample_ids,
            federation_headers=federation_headers,
            federation_url=federation_url,
            is_dry_run=is_dry_run,
        )
        write_experiment_metadata_to_csv(all_metadata[1]['experiment_metadata'], Path(session_dir, "experiment_data.csv"))
        write_experiment_metadata_to_csv(all_metadata[1]['analysis_metadata'],
                                         Path(session_dir, "analysis_data.csv"))
        newly_collected_metadata = all_metadata[0]
        if newly_collected_metadata:
            write_variant_metadata_to_file(
                files_metadata_list=newly_collected_metadata,
                metadata_file_path=variant_metadata_log_path,
            )
            logger.debug(
                f"Appended {len(newly_collected_metadata)} new entries to {variant_metadata_log_path}."
            )
        else:
            logger.info(
                "No new variant metadata collected for the provided IDs in this run."
            )
    else:
        logger.info(
            "No new program_sample_ids provided; proceeding to process existing metadata if any."
        )

    all_pending_metadata = read_metadata_from_file(variant_metadata_log_path)

    if not all_pending_metadata:
        logger.info(
            f"No variant metadata found in {variant_metadata_log_path}. Nothing to process."
        )
        return True

    logger.debug(
        f"PHASE 2: Processing {len(all_pending_metadata)} total entries from {variant_metadata_log_path}."
    )

    if is_dry_run:
        logger.info("DRY RUN MODE: Simulating downloads and verifications...")
        files_would_download = 0
        files_would_redownload = 0
        files_would_skip_validated = 0
        files_already_exist_no_validate_criteria = 0
        files_with_errors_in_meta = 0
        total_size_bytes_would_download = 0

        print(
            f"\n--- DRY RUN: Processing of {variant_metadata_log_path} ---"
        )
        for item in all_pending_metadata:
            filename = item.get("filename", "N/A")
            if item.get("error"):
                files_with_errors_in_meta += 1
                print(
                    f"  - Would skip {filename} due to metadata error: {item['error']}"
                )
                continue
            if not all(
                [item.get("download_url"), filename, item.get("target_output_dir")]
            ):
                files_with_errors_in_meta += 1
                print(f"  - Would skip {filename} due to incomplete metadata.")
                continue

            target_path = session_dir / item["target_output_dir"] / filename
            expected_size = item.get("size")
            expected_checksums = item.get("checksums", [])

            if target_path.exists():
                verification_status, verification_msg = verify_local_file(
                    target_path, expected_size, expected_checksums
                )
                if (
                    verification_status == "MATCH_CHECKSUM"
                    or verification_status == "MATCH_SIZE"
                ):
                    files_would_skip_validated += 1
                    print(
                        f"  - {filename} ({expected_size or 'N/A'} bytes) -> {target_path} (WOULD SKIP, {verification_status}: {verification_msg})"
                    )
                elif verification_status == "NO_VALIDATION_CRITERIA":
                    files_already_exist_no_validate_criteria += 1
                    print(
                        f"  - {filename} ({expected_size or 'N/A'} bytes) -> {target_path} (EXISTS, {verification_status}: {verification_msg}. Policy implies re-download if not strictly validated.)"
                    )
                    # re-download
                    files_would_redownload += 1
                    total_size_bytes_would_download += (
                        expected_size if isinstance(expected_size, int) else 0
                    )
                else:  # Mismatch or verification error
                    files_would_redownload += 1
                    total_size_bytes_would_download += (
                        expected_size if isinstance(expected_size, int) else 0
                    )
                    print(
                        f"  - {filename} ({expected_size or 'N/A'} bytes) -> {target_path} (WOULD BE RE-DOWNLOADED due to {verification_status}: {verification_msg})"
                    )
            else:  # File does not exist
                files_would_download += 1
                total_size_bytes_would_download += (
                    expected_size if isinstance(expected_size, int) else 0
                )
                print(
                    f"  - {filename}:{expected_size or 'N/A'} bytes"
                )

        print("\n--- DRY RUN Summary ---")
        logger.info(
            f"DRY RUN: {len(newly_collected_metadata)} new metadata entries were added in this run (if any)."
        )
        logger.info(f"DRY RUN: From {variant_metadata_log_path}:")
        logger.info(f"  {files_would_download} files would be downloaded (new).")
        logger.info(
            f"  {files_would_redownload} existing files would be re-downloaded (validation failed/inconclusive)."
        )
        logger.info(
            f"  {files_would_skip_validated} existing files would be skipped (validated)."
        )
        if files_already_exist_no_validate_criteria > 0:
            logger.info(
                f"  {files_already_exist_no_validate_criteria} existing files had no validation criteria in metadata (would lead to re-download by current policy)."
            )
        logger.info(
            f"  {files_with_errors_in_meta} files have errors/incomplete metadata and would be skipped."
        )
        logger.info(
            f"  Estimated total download size (new + re-downloads): {total_size_bytes_would_download} bytes."
        )
        print("--- End DRY RUN ---")
        return True

    download_results = download_files_from_collected_metadata(
        files_metadata_list=all_pending_metadata,
        download_headers=download_headers,
        session_dir=session_dir,
    )

    with open(variant_metadata_log_path, "a") as variant_log:
        for result in download_results:
            json.dump(result.as_dict(), variant_log)
            variant_log.write("\n")

    succeeded_new_downloads = sum(
        1 for r in download_results if r.status == "DOWNLOADED_SUCCESS"
    )
    succeeded_redownloads = sum(
        1
        for r in download_results
        if r.status and r.status.startswith("REDOWNLOADED_SUCCESS")
    )
    skipped_checksum = sum(
        1 for r in download_results if r.status == "SKIPPED_CHECKSUM_MATCH"
    )
    skipped_size = sum(1 for r in download_results if r.status == "SKIPPED_SIZE_MATCH")
    failed_ops = sum(
        1
        for r in download_results
        if not r.success
        and r.status
        not in [
            "SKIPPED_CHECKSUM_MATCH",
            "SKIPPED_SIZE_MATCH",
            "ERROR_IN_METADATA",
            "INCOMPLETE_METADATA",
        ]
    )
    meta_errors = sum(
        1
        for r in download_results
        if r.status in ["ERROR_IN_METADATA", "INCOMPLETE_METADATA"]
    )

    logger.info("Variant download pipeline processing complete.")
    logger.info(f"  Successfully downloaded (new): {succeeded_new_downloads} files.")
    logger.info(
        f"  Successfully re-downloaded (after validation failure): {succeeded_redownloads} files."
    )
    logger.info(f"  Skipped (validated by checksum): {skipped_checksum} files.")
    logger.info(f"  Skipped (validated by size): {skipped_size} files.")
    logger.info(f"  Skipped (metadata error/incomplete): {meta_errors} files.")
    if failed_ops > 0:
        logger.warning(
            f"  Failed operations (download/setup/verification): {failed_ops} files. Check logs for details."
        )

    logger.info(f"All variant data and logs are in: {session_dir}")
    logger.info(f"Master variant metadata log: {variant_metadata_log_path}")
    return True
