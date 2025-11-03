"""
This script provides a pipeline for downloading variant genomic data.

The process is divided into two main phases:
1.  Metadata Collection: It communicates with a federated service to gather
    metadata about experiments, analyses, and downloadable variant files
    based on provided program and sample IDs. This metadata,
    including download URLs and checksums, is saved to local files
    (e.g., variant_metadata.jsonl, experiment_data.csv).

2.  File Processing: It reads the collected metadata and proceeds to download
    the files. It supports resuming downloads, verifying existing files against
    checksums/size, and re-downloading corrupt files. A dry-run mode is
    available to estimate download size without actually downloading any files.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd
from tqdm import tqdm

from client import config

logger = logging.getLogger(__name__)

# --- Constants ---
VARIANT_METADATA_FILENAME = "variant_metadata.jsonl"
EXPERIMENT_DATA_FILENAME = "experiment_data.csv"
ANALYSIS_DATA_FILENAME = "analysis_data.csv"
VARIANTS_OUTPUT_PARENT_DIR_NAME = "variant_data"

DRS_OBJECTS_PATH = "ga4gh/drs/v1/objects"
SUPPORTED_EXPERIMENT_TYPES = ["wgs", "wts"]
VARIANT_ANALYSIS_TYPE = "sequence_variation"


# ============================
# === Data & Result Models ===
# ============================


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[str] = None
    error: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None

    def as_dict(self):
        return {
            "success": self.success,
            "file_path": self.file_path,
            "error": self.error,
            "status": self.status,
            "message": self.message,
        }


@dataclass
class CollectedMetadata:
    files: List[Dict[str, Any]]
    experiments: Dict[str, Any]
    analyses: Dict[str, Any]


# =========================
# === Utility Functions ===
# =========================


def calculate_checksum(file_path: Path, hash_type: str = "md5") -> Optional[str]:
    """
    Calculates the checksum for a file.
    """
    hash_type = hash_type.lower()
    if hash_type not in ["md5", "sha256"]:
        logger.warning(f"Unsupported hash type for checksum: {hash_type}")
        return None

    hasher = hashlib.new(hash_type)
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except IOError as e:
        logger.error(f"IOError calculating checksum for {file_path}: {e}")
        return None


def verify_local_file(
    local_file_path: Path,
    expected_size: Optional[int],
    expected_checksums: List[Dict[str, str]],
) -> Tuple[str, str]:
    """
    Verifies a local file against expected checksums or size.
    """
    if not local_file_path.exists():
        return (
            "VERIFICATION_ERROR",
            f"Local file not found for verification: {local_file_path}",
        )

    try:
        local_file_size = local_file_path.stat().st_size
    except OSError as e:
        return "VERIFICATION_ERROR", f"Could not get size of {local_file_path}: {e}"

    # 1. Checksum validation (preferred)
    if expected_checksums:
        verifiable_checksum_found = False
        for cs_entry in expected_checksums:
            cs_type = cs_entry.get("type", "").lower()
            expected_value = cs_entry.get("checksum")

            if cs_type in ["md5", "sha256"] and expected_value:
                verifiable_checksum_found = True
                logger.debug(f"Verifying {local_file_path} with {cs_type} checksum...")
                local_value = calculate_checksum(local_file_path, cs_type)

                if local_value and local_value == expected_value:
                    return "MATCH_CHECKSUM", f"Validated with {cs_type} checksum."
            else:
                logger.debug(
                    f"Unsupported checksum type '{cs_type}' in metadata for {local_file_path}."
                )

        if verifiable_checksum_found:
            return (
                "MISMATCH_CHECKSUM",
                "Local file checksum does not match any verifiable checksum in metadata.",
            )

    # 2. Size validation (fallback)
    if expected_size is not None:
        if local_file_size == expected_size:
            return (
                "MATCH_SIZE",
                f"Local file size ({local_file_size}) matches expected size.",
            )
        else:
            return (
                "MISMATCH_SIZE",
                f"Size mismatch: local is {local_file_size}, expected {expected_size}.",
            )

    return (
        "NO_VALIDATION_CRITERIA",
        "No verifiable checksum or size provided in metadata.",
    )


def execute_federation_call(
    federation_url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """
    Makes a POST request to the federation endpoint and handles errors.

    Returns:
        A list of responses from federated servers, or None on failure.
    """
    service = payload.get("service", "unknown_service")
    path = payload.get("path", "N/A")
    logger.debug(f"Sending request to federation service for path: {path} with payload {payload}")

    try:
        with httpx.Client(timeout=config.TIMEOUT) as client:
            response = client.post(federation_url, headers=headers, json=payload)
            response.raise_for_status()
            logger.debug(
                f"Request to {path} successful (Status: {response.status_code})"
            )
            for node in response.json():
                try:
                    if 'failed' in node['message']:
                        logger.warning(f"Failure contacting node {node['location']['name']}")
                        logger.warning(f"Error message: {node['message']}")
                except KeyError as e:
                    continue
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(
            f"HTTP error during '{service}' request for {path}: {e.response.status_code}"
        )
        try:
            details = e.response.json()
            logger.error(f"Error details: {json.dumps(details)}")
            if details.get("error") == "Key not authorised":
                logger.error(
                    "Authorization error: Your token may have expired. Please retry with a new token."
                )
        except json.JSONDecodeError:
            logger.error(f"Non-JSON error response: {e.response.text[:500]}...")
        return None
    except httpx.RequestError as e:
        logger.error(
            f"Network/connection error during '{service}' request for {path}: {e}"
        )
        return None


def download_file(
    url: str,
    headers: Dict[str, str],
    output_dir: Path,
    filename: str,
) -> DownloadResult:
    """Downloads a file with a progress bar."""
    output_path = output_dir / filename
    logger.debug(f"Downloading from {url} to {output_path}")

    try:
        with httpx.stream(
            "GET", url, headers=headers, timeout=config.TIMEOUT
        ) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))

            with (
                open(output_path, "wb") as f,
                tqdm(
                    total=total_size,
                    unit="iB",
                    unit_scale=True,
                    desc=f" {filename[:25]:<25}",
                    leave=False,
                    position=1,
                ) as pbar,
            ):
                for chunk in response.iter_raw():
                    bytes_written = f.write(chunk)
                    pbar.update(bytes_written)

        logger.debug(f"Successfully downloaded {filename}")
        return DownloadResult(
            success=True, file_path=str(output_path), status="DOWNLOADED_SUCCESS"
        )
    except Exception as e:
        # Clean up partially downloaded file
        if output_path.exists():
            try:
                output_path.unlink()
                logger.debug(f"Removed partial file: {output_path}")
            except OSError as unlink_e:
                logger.warning(
                    f"Failed to remove partial file {output_path}: {unlink_e}"
                )
        return DownloadResult(
            success=False,
            error=f"Failed to download {filename}: {e}",
            status="FAILED_DOWNLOAD",
        )


# ===========================
# === File I/O Operations ===
# ===========================


def write_records_to_csv(records: Dict[str, Any], file_path: Path) -> None:
    """
    Writes a dictionary of records to a CSV file, overwriting it if it exists.
    """
    if not records:
        logger.info(f"No records to write to {file_path}.")
        return

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame.from_dict(records, orient="index")
        df.to_csv(file_path, index=False)
        logger.debug(f"Wrote {len(records)} records to {file_path}")
    except (OSError, IOError) as e:
        logger.error(f"Failed to write to CSV file {file_path}: {e}")


def append_records_to_jsonl(records: List[Dict[str, Any]], file_path: Path) -> None:
    """Appends a list of dictionary records to a .jsonl file."""
    if not records:
        logger.info(f"No new records to append to {file_path}.")
        return

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "a") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
        logger.debug(f"Appended {len(records)} records to {file_path}")
    except (OSError, IOError) as e:
        logger.error(f"Failed to append to jsonl file {file_path}: {e}")


def read_metadata_from_file(metadata_file_path: Path) -> List[Dict[str, Any]]:
    """Reads all records from a .jsonl file."""
    if not metadata_file_path.is_file():
        return []

    records = []
    try:
        with open(metadata_file_path, "r") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Skipping malformed JSON line {i + 1} in {metadata_file_path}: {e}"
                    )
        logger.debug(f"Read {len(records)} records from {metadata_file_path}")
        return records
    except IOError as e:
        logger.error(f"Failed to read metadata file {metadata_file_path}: {e}")
        return []


# ====================================
# === Phase 1: Metadata Collection ===
# ====================================


def _parse_and_group_ids(program_sample_ids: List[str]) -> Dict[str, List[str]]:
    """Parses 'program~sample-id' strings into a dictionary."""
    programs_to_samples = {}
    for ps_id in program_sample_ids:
        try:
            program_id, sample_id = ps_id.split("~", 1)
            programs_to_samples.setdefault(program_id, []).append(sample_id)
        except ValueError:
            logger.error(f"Invalid program_sample_id format: '{ps_id}'. Skipping.")
    return programs_to_samples


def _process_drs_objects_from_node(
    drs_objects: List[Dict], program_id: str, sample_ids: List[str]
) -> CollectedMetadata:
    """
    Processes DRS objects:
    - Find experiment objects that has wts or wgs description.
    - Find analysis objects linked from experiments.
    - For each analysis object, collect metadata and downloadable file info.
    """
    file_meta, exp_meta, analysis_meta = [], {}, {}
    if not drs_objects:
        return CollectedMetadata(files=[], experiments={}, analyses={})

    # make a lookup dict by name for quick access later
    drs_objects_by_name = {obj["name"]: obj for obj in drs_objects if "name" in obj}

    # 1. Find relevant experiment objects
    # by filtering description type and sample IDs
    experiment_objects = []
    for obj in drs_objects:
        if obj.get("description") in SUPPORTED_EXPERIMENT_TYPES:
            if any(sample_id in obj.get("name", "") for sample_id in sample_ids):
                experiment_objects.append(obj)
                program_sample_id = f"{program_id}~{obj['name']}"
                exp_meta[program_sample_id] = obj.get("metadata", {})
                exp_meta[program_sample_id].update(
                    {
                        "experiment_id": obj.get("id"),
                        "program_id": program_id,
                        "submitter_sample_id": obj.get("name"),
                    }
                )

    # 2. Find analysis objects linked from experiments
    analysis_objects = []
    for exp_obj in experiment_objects:
        for content in exp_obj.get("contents", []):
            if analysis_obj := drs_objects_by_name.get(content.get("name")):
                analysis_objects.append(analysis_obj)

    # 3. Process analysis objects
    for analysis_obj in analysis_objects:
        analysis_id = analysis_obj.get("id")
        metadata = analysis_obj.get("metadata", {})
        analysis_files_list = []
        analysis_samples_list = []

        # the contents of an analysis object can be files or samples
        # the files are typically variant files, while samples are linked to experiments
        for content in analysis_obj.get("contents", []):
            content_obj = drs_objects_by_name.get(content.get("name"))
            if not content_obj:
                continue

            if content_obj.get("description") in SUPPORTED_EXPERIMENT_TYPES:
                sample_dict = {
                    "submitter_sample_id": content_obj.get("name"),
                    "analysis_sample_id": content.get("id"),
                    "experiment_id": content_obj.get("id"),
                }
                analysis_samples_list.append(sample_dict)
            else:
                analysis_files_list.append(content_obj.get("name"))

        final_analysis_data = metadata.copy()
        final_analysis_data.update(
            {
                "file_id": analysis_id,
                "program": analysis_obj.get("program"),
                "reference_genome": analysis_obj.get("reference_genome"),
                "files": analysis_files_list,
                "samples": analysis_samples_list,
            }
        )

        analysis_meta[analysis_id] = final_analysis_data

        # if the file is sequence variation, it can be downloaded
        if metadata.get("analysis_type") == VARIANT_ANALYSIS_TYPE:
            for content in analysis_obj.get("contents", []):
                if not (file_obj := drs_objects_by_name.get(content.get("name"))):
                    continue

                if file_obj.get("description") in SUPPORTED_EXPERIMENT_TYPES:
                    continue

                # build download URL
                host = file_obj.get("self_uri", "").split("/")[2]
                protocol = config.DEFAULT_BASE_URL.split("://")[0]
                download_url = f"{protocol}://{host}/genomics/{DRS_OBJECTS_PATH}/{file_obj['id']}/download"

                file_metadata = {
                    "filename": file_obj.get("name"),
                    "size": file_obj.get("size"),
                    "checksums": file_obj.get("checksums", []),
                    "program_id": program_id,
                    "download_url": download_url,
                }

                # skip files that exceed the maximum download size
                if (
                    file_metadata.get("size") is not None
                    and file_metadata["size"] > config.DOWNLOAD_MAX_SIZE
                ):
                    logger.warning(
                        f"File {file_metadata['filename']} ({file_metadata['size']} bytes) "
                        f"exceeds size limit ({config.DOWNLOAD_MAX_SIZE} bytes). Skipping from download list."
                    )
                else:
                    file_meta.append(file_metadata)

    return CollectedMetadata(
        files=file_meta, experiments=exp_meta, analyses=analysis_meta
    )


def collect_metadata(
    program_sample_ids: List[str],
    federation_headers: Dict[str, str],
    federation_url: str,
) -> CollectedMetadata:
    """
    Collect file metadata from federated services based on program-sample IDs.
    """
    # group sample by program
    programs_to_samples = _parse_and_group_ids(program_sample_ids)
    if not programs_to_samples:
        logger.warning("No valid program-sample IDs to process.")
        return CollectedMetadata(files=[], experiments={}, analyses={})

    logger.info(
        f"Querying for {len(programs_to_samples)} programs: {list(programs_to_samples.keys())}"
    )

    all_files, all_experiments, all_analyses = [], {}, {}

    for program_id, sample_ids in tqdm(
        programs_to_samples.items(), desc="Processing Programs", unit="program"
    ):
        payload = {
            "path": DRS_OBJECTS_PATH,
            "payload": {"program_id": program_id},
            "method": "GET",
            "service": config.GENOMICS_SERVICE,
        }
        # this endpoint gets all objects for a program
        federated_responses = execute_federation_call(
            federation_url, federation_headers, payload
        )
        if not federated_responses:
            logger.error(
                f"Failed to get DRS objects for program {program_id}. Skipping."
            )
            continue

        for resp in federated_responses:
            if resp.get("status") != 200:
                logger.warning(
                    f"Skipping node {resp.get('location', {}).get('name')} due to status {resp.get('status')}"
                )
                continue

            node_metadata = _process_drs_objects_from_node(
                resp.get("results", []), program_id, sample_ids
            )
            all_files.extend(node_metadata.files)
            all_experiments.update(node_metadata.experiments)
            all_analyses.update(node_metadata.analyses)

    # remove duplicates
    seen_urls = set()
    unique_files = []
    for f_meta in all_files:
        if (url := f_meta.get("download_url")) and url not in seen_urls:
            unique_files.append(f_meta)
            seen_urls.add(url)

    return CollectedMetadata(
        files=unique_files, experiments=all_experiments, analyses=all_analyses
    )


# =======================================
# === Phase 2: File Download & Verify ===
# =======================================


def determine_file_action(
    meta_entry: Dict[str, Any], session_dir: Path
) -> Tuple[str, str, Optional[Path]]:
    """
    Determines the action for a file based on its metadata and local state.
    Action: "DOWNLOAD", "REDOWNLOAD", "SKIP_VALIDATED", "SKIP_INCOMPLETE_METADATA", "FAIL_MKDIR".
    """
    # check variant metadata is ok
    filename = meta_entry.get("filename")
    if not all(
        [meta_entry.get("download_url"), filename, meta_entry.get("program_id")]
    ):
        return (
            "SKIP_INCOMPLETE_METADATA",
            "Incomplete metadata (URL, filename, or program_id missing).",
            None,
        )

    program_id = meta_entry["program_id"]
    target_dir = session_dir / VARIANTS_OUTPUT_PARENT_DIR_NAME / program_id
    target_path = target_dir / filename

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return (
            "FAIL_MKDIR",
            f"Failed to create output directory {target_dir}: {e}",
            target_path,
        )

    if not target_path.exists():
        return "DOWNLOAD", "File does not exist locally.", target_path

    # check local files for partial downloads
    status, msg = verify_local_file(
        target_path, meta_entry.get("size"), meta_entry.get("checksums", [])
    )
    if status in ["MATCH_CHECKSUM", "MATCH_SIZE"]:
        return "SKIP_VALIDATED", msg, target_path
    else:
        # Any other status (MISMATCH, ERROR, NO_VALIDATION) triggers re-download
        return "REDOWNLOAD", f"Verification failed ({status}): {msg}", target_path


def download_files_from_collected_metadata(
    files_metadata_list: List[Dict[str, Any]],
    download_headers: Dict[str, str],
    session_dir: Path,
) -> List[DownloadResult]:
    """
    Processes a list of file metadata, deciding whether to download, re-download, or skip.
    """
    all_results: List[DownloadResult] = []
    if not files_metadata_list:
        logger.info("No files to process.")
        return []

    for meta_entry in tqdm(
        files_metadata_list, desc="Overall File Progress", unit="file", position=0
    ):
        action, message, target_path = determine_file_action(meta_entry, session_dir)
        filename = meta_entry.get("filename", "unknown_file")

        if action in ["DOWNLOAD", "REDOWNLOAD"]:
            if action == "REDOWNLOAD":
                logger.warning(f"Re-downloading {filename}. Reason: {message}")

            result = download_file(
                meta_entry["download_url"],
                download_headers,
                target_path.parent,
                filename,
            )

            if action == "REDOWNLOAD":
                if result.success:
                    result.status, result.message = (
                        "REDOWNLOADED_SUCCESS",
                        f"Successfully re-downloaded after: {message}",
                    )
                else:
                    result.status, result.error = (
                        "FAILED_REDOWNLOAD",
                        f"Re-download failed. Original issue: {message}. New error: {result.error}",
                    )
                    logger.error(f"Failed to re-download {filename}: {result.error}")
            elif not result.success:
                logger.error(f"Failed to download {filename}: {result.error}")
            all_results.append(result)

        elif action == "SKIP_VALIDATED":
            status = (
                "SKIPPED_CHECKSUM_MATCH"
                if "checksum" in message
                else "SKIPPED_SIZE_MATCH"
            )
            all_results.append(
                DownloadResult(
                    success=True,
                    file_path=str(target_path),
                    status=status,
                    message=message,
                )
            )

        else:  # SKIP_INCOMPLETE_METADATA or FAIL_MKDIR
            logger.error(f"Skipping {filename}: {message}")
            all_results.append(
                DownloadResult(
                    success=False, status=action, error=message, message=message
                )
            )

    return all_results


# =============================================
# === Pipeline Controllers and Entry Points ===
# =============================================


def get_download_session_dir(base_download_path_str: str = "candig_downloads") -> Path:
    """Creates and returns a unique, timestamped session folder for downloads."""
    session_dir = Path(base_download_path_str) / datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Download session directory created: {session_dir}")
    except OSError as e:
        logger.critical(f"FATAL: Failed to create session directory {session_dir}: {e}")
        raise
    return session_dir


def _perform_dry_run(metadata_list: List[Dict], session_dir: Path):
    """Prints a summary without downloading."""
    logger.info("\n--- DRY RUN: Analysis Started ---")
    counts = {"DOWNLOAD": 0, "REDOWNLOAD": 0, "SKIP_VALIDATED": 0, "ERROR": 0}
    total_size = 0

    for item in metadata_list:
        action, message, _ = determine_file_action(item, session_dir)
        filename = item.get("filename", "N/A")
        size_mb = round((item.get("size") or 0) / 1_000_000, 2)

        if action == "DOWNLOAD":
            counts["DOWNLOAD"] += 1
            total_size += item.get("size", 0)
            logger.info(f"  [DOWNLOAD] {filename} ({size_mb} MB)")
        elif action == "REDOWNLOAD":
            counts["REDOWNLOAD"] += 1
            total_size += item.get("size", 0)
            logger.info(
                f"  [RE-DOWNLOAD] {filename} ({size_mb} MB) - Reason: {message}"
            )
        elif action == "SKIP_VALIDATED":
            counts["SKIP_VALIDATED"] += 1
            logger.info(f"  [SKIP] {filename} - Reason: {message}")
        else:
            counts["ERROR"] += 1
            logger.warning(f"  [ERROR] {filename} - Reason: {message}")

    print("\n--- DRY RUN Summary ---")
    logger.info(f"  {counts['DOWNLOAD']} files would be downloaded.")
    logger.info(f"  {counts['REDOWNLOAD']} files would be re-downloaded.")
    logger.info(f"  {counts['SKIP_VALIDATED']} valid files would be skipped.")
    logger.info(
        f"  {counts['ERROR']} operations would fail or be skipped due to errors."
    )
    logger.info(
        f"  Estimated total download size: {round(total_size / 1_000_000, 2)} MB."
    )
    print("--- End DRY RUN ---\n")


def _generate_summary_report(results: List[DownloadResult]):
    """Logs a final summary of the download process."""
    success_new = sum(1 for r in results if r.status == "DOWNLOADED_SUCCESS")
    success_redownload = sum(1 for r in results if r.status == "REDOWNLOADED_SUCCESS")
    skipped_cs = sum(1 for r in results if r.status == "SKIPPED_CHECKSUM_MATCH")
    skipped_sz = sum(1 for r in results if r.status == "SKIPPED_SIZE_MATCH")
    failed = sum(1 for r in results if not r.success)

    logger.info("\n--- Pipeline Summary ---")
    logger.info(f"  Successfully downloaded (new): {success_new} files.")
    logger.info(f"  Successfully re-downloaded: {success_redownload} files.")
    logger.info(f"  Skipped (validated by checksum): {skipped_cs} files.")
    logger.info(f"  Skipped (validated by size): {skipped_sz} files.")
    if failed > 0:
        logger.warning(
            f"  Failed/Skipped due to errors: {failed} files. Check logs for details."
        )

        # Break down failure types for better debugging
        failure_counts = {}
        for r in results:
            if not r.success:
                status = r.status or "UNKNOWN_ERROR"
                failure_counts[status] = failure_counts.get(status, 0) + 1

        logger.warning("  Failure breakdown:")
        for status, count in failure_counts.items():
            logger.warning(f"    {status}: {count} files")
    else:
        logger.info("  All operations completed successfully.")
    logger.info("--- End Summary ---\n")


def run_variant_download_pipeline(
    program_sample_ids: Optional[List[str]],
    federation_headers: Dict[str, str],
    download_headers: Dict[str, str],
    federation_url: str,
    session_dir: Path,
    is_dry_run: bool = False,
) -> bool:
    """
    Main controller for the variant download pipeline.
    - Phase 1: Collects metadata and download links.
    - Phase 2: Downloads files based on collected metadata.
    """
    variant_metadata_log_path = session_dir / VARIANT_METADATA_FILENAME
    logger.info(f"Using session directory: {session_dir}")
    logger.info(f"Variant file metadata log: {variant_metadata_log_path}")

    # Phase 1: Collect metadata based on program-sample IDs
    if program_sample_ids:
        logger.info(
            f"PHASE 1: Collecting metadata for {len(program_sample_ids)} program-sample ID(s)..."
        )
        collected_metadata = collect_metadata(
            program_sample_ids, federation_headers, federation_url
        )

        # Save all collected metadata
        append_records_to_jsonl(collected_metadata.files, variant_metadata_log_path)
        write_records_to_csv(
            collected_metadata.experiments, session_dir / EXPERIMENT_DATA_FILENAME
        )
        write_records_to_csv(
            collected_metadata.analyses, session_dir / ANALYSIS_DATA_FILENAME
        )
    else:
        logger.info(
            "No new program_sample_ids provided; processing existing metadata if available."
        )

    # Phase 2: Process download links from the log
    all_pending_metadata = read_metadata_from_file(variant_metadata_log_path)
    if not all_pending_metadata:
        logger.info("No variant metadata found. Nothing to process. Exiting.")
        return True

    logger.info(
        f"PHASE 2: Processing {len(all_pending_metadata)} total file entries from log."
    )

    if is_dry_run:
        _perform_dry_run(all_pending_metadata, session_dir)  # No download
        return True

    # Normal download mode
    download_results = download_files_from_collected_metadata(
        all_pending_metadata, download_headers, session_dir
    )

    _generate_summary_report(download_results)

    failed_ops = sum(1 for r in download_results if not r.success)
    logger.info(f"All variant data and logs are in: {session_dir.resolve()}")
    return failed_ops == 0
