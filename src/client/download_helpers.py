"""
Helper functions for the CanDIG download client.
"""

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
import genomics_helpers
import httpx
from tqdm import tqdm

logger = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[str] = None
    error: Optional[str] = None


def execute_federation_call(
    federation_url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    progress_callback: Optional[callable] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Makes a POST request to the federation endpoint."""
    service = payload.get("service", "unknown")
    path = payload.get("path", "N/A")
    logger.info(f"Sending request to federation service ({path})...")

    try:
        with httpx.Client(timeout=config.TIMEOUT) as client:
            logger.debug(f"Request payload: {json.dumps(payload, indent=2)}")
            response = client.post(federation_url, headers=headers, json=payload)
            response.raise_for_status()
            logger.info(f"Request successful (Status: {response.status_code})")
            # too verbose to show, uncomment if debug needed
            # logger.debug(f"Response: {json.dumps(response.json(), indent=2)}")
            if progress_callback:
                progress_callback()
            return response.json()
    except httpx.HTTPStatusError as e:
        print(
            f"\nHTTP error during {service} request: {e.response.status_code}",
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
            f"\nNetwork/connection error during {service} request: {e}", file=sys.stderr
        )
        return None
    except Exception as e:
        print(f"\nUnexpected error during {service} request: {e}", file=sys.stderr)
        return None


def download_file(
    url: str,
    headers: Dict[str, str],
    output_dir: Path,
    filename: str,
    timeout: int = config.TIMEOUT,
    show_progress: bool = True,
) -> DownloadResult:
    """Download a file from a URL"""
    try:
        output_path = output_dir / filename
        logger.info(f"Downloading from url {url} to {output_path}")

        with httpx.stream("GET", url, headers=headers, timeout=timeout) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))

            with open(output_path, "wb") as f:
                if show_progress and total_size > 0:
                    with tqdm(
                        total=total_size,
                        unit="iB",
                        unit_scale=True,
                        desc=filename,
                        leave=False,
                        position=1,
                    ) as pbar:
                        for chunk in response.iter_bytes():
                            size = f.write(chunk)
                            pbar.update(size)
                else:
                    for chunk in response.iter_bytes():
                        f.write(chunk)

        logger.info(f"Successfully downloaded {filename}")
        return DownloadResult(success=True, file_path=str(output_path))
    except Exception as e:
        error_msg = f"Failed to download {filename}: {str(e)}"
        return DownloadResult(success=False, error=error_msg)


def get_download_access(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract download access method from a drs object."""
    try:
        access_methods = obj.get("results", {}).get("access_methods", [])
        if not access_methods:
            logger.info("No access methods found in object")
            return None

        download_method = next(
            (method for method in access_methods if method.get("type") == "download"),
            None,
        )
        if not download_method:
            logger.info("No download access method found")
            return None

        access_url = download_method.get("access_url", {}).get("url")
        if not access_url:
            logger.info("No access URL found in download method")
            return None

        return download_method
    except Exception as e:
        logger.error(f"Error extracting download access: {e}")
        return None


def should_skip_file(
    obj: Dict[str, Any],
) -> tuple[bool, Optional[str]]:
    """Check if a file should be skipped based on size."""
    try:
        size = obj.get("results", {}).get("size", 0)
        if size > config.DOWNLOAD_MAX_SIZE:
            error_msg = (
                f"File {obj.get('results', {}).get('name', 'unknown')} "
                f"({size} bytes) exceeds size limit ({config.DOWNLOAD_MAX_SIZE} bytes)"
            )
            return True, error_msg
        return False, None
    except Exception as e:
        logger.warning(f"Error checking file size: {e}")
        return False, None


def process_file_download(
    file_obj: Dict[str, Any],
    file_type: str,
    headers: Dict[str, str],
    output_dir: Path,
    show_progress: bool = True,
    is_dry_run: bool = False,
) -> DownloadResult:
    """Check for file size and download access, then download the file."""
    try:
        should_skip, skip_error = should_skip_file(file_obj)
        if should_skip:
            return DownloadResult(success=False, error=skip_error)

        # In dry run mode, just log the file size and return
        if is_dry_run:
            size = file_obj.get("results", {}).get("size", 0)
            filename = file_obj["results"]["name"]
            print(f"File {filename} : {size} bytes")
            return DownloadResult(success=True)

        download_access = get_download_access(file_obj)
        if not download_access:
            error_msg = f"No download access method found for {file_type} file"
            return DownloadResult(success=False, error=error_msg)

        download_url = download_access["access_url"]["url"]
        filename = file_obj["results"]["name"]
        logger.info(f"Processing {file_type} file: {filename}")

        result = download_file(
            url=download_url,
            headers=headers,
            output_dir=output_dir,
            filename=filename,
            show_progress=show_progress,
        )

        return result
    except Exception as e:
        error_msg = f"Error processing {file_type} file: {str(e)}"
        return DownloadResult(success=False, error=error_msg)


def process_file_object(
    file_item: Dict[str, Any],
    file_type: str,
    headers: Dict[str, str],
    federation_url: str,
    output_dir: Path,
    show_progress: bool = True,
    is_dry_run: bool = False,
) -> None:
    """Process a FileDRS object by getting its download access and download it"""
    if not file_item:
        return

    filename = file_item.get("name", "unknown")
    logger.info(f"Processing {file_type} file: {filename}")
    
    payload = genomics_helpers.build_file_drs_request_payload(filename)
    objs_response = execute_federation_call(
        federation_url=federation_url,
        headers=headers,
        payload=payload,
    )

    if objs_response:
        for obj in objs_response:
            result = process_file_download(
                file_obj=obj,
                file_type=file_type,
                headers=headers,
                output_dir=output_dir,
                show_progress=show_progress,
                is_dry_run=is_dry_run,
            )
            if not result.success:
                logger.warning(
                    f"Failed to download {file_type} file: {filename} - {result.error}"
                )


def process_analysis_drs_objects(
    analysis_drs_objs_response: List[Dict[str, Any]],
    headers: Dict[str, str],
    federation_url: str,
    variants_dir: Path,
    show_progress: bool = True,
    is_dry_run: bool = False,
) -> None:
    """Process a AnalysisDRS object by getting its analysis and index files"""
    if not analysis_drs_objs_response:
        logger.warning("No AnalysisDRS objects to process")
        return

    logger.info(f"Processing {len(analysis_drs_objs_response)} AnalysisDRS objects")

    for analysis_drs_obj in tqdm(
        analysis_drs_objs_response,
        desc="Processing AnalysisDRS objects",
        leave=False,
        position=0,
    ):
        contents = analysis_drs_obj.get("results", {}).get("contents", [])
        
        # Process analysis file
        analysis_file = next(
            (item for item in contents if item.get("id") == "analysis"),
            None,
        )
        process_file_object(
            file_item=analysis_file,
            file_type="analysis",
            headers=headers,
            federation_url=federation_url,
            output_dir=variants_dir,
            show_progress=show_progress,
            is_dry_run=is_dry_run,
        )

        # Process index file
        index_file = next(
            (item for item in contents if item.get("id") == "index"),
            None,
        )
        process_file_object(
            file_item=index_file,
            file_type="index",
            headers=headers,
            federation_url=federation_url,
            output_dir=variants_dir,
            show_progress=show_progress,
            is_dry_run=is_dry_run,
        )


def get_download_session_dir() -> Path:
    """Create and return a timestamped directory for this download session."""
    base_dir = Path("candig_downloads")
    timestamp = datetime.now().strftime("%Y_%m_%d_%H%M")
    session_dir = base_dir / timestamp
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_programs_in_genomics(
    federation_url: str,
    headers: Dict[str, str],
) -> List[str]:
    """Get list of valid program IDs from federation service."""
    payload = {
        "path": "ga4gh/drs/v1/programs",
        "payload": {},
        "method": "GET",
        "service": "htsget"
    }
    
    response = execute_federation_call(
        federation_url=federation_url,
        headers=headers,
        payload=payload,
    )
    
    if not response:
        logger.error("Failed to get valid programs from federation service")
        return []
        
    valid_programs = set()
    for location_data in response:
        if location_data.get("status") == 200 and location_data.get("results"):
            valid_programs.update(location_data["results"])
            
    return list(valid_programs)


def download_variant_data(
    program_sample_ids: List[str],
    headers: Dict[str, str],
    federation_url: str,
    session_dir: Path,
    is_dry_run: bool = False,
) -> None:
    """Download variant data using program and sample IDs."""
    if not program_sample_ids:
        logger.warning(
            "\nNo program sample ID available for variant download. Please check your input parameters."
        )
        return

    # Get list of valid programs
    genomics_programs = get_programs_in_genomics(federation_url, headers)
    if not genomics_programs:
        logger.error("No valid programs found. Aborting download.")
        return
        
    logger.info(f"Found {len(genomics_programs)} valid programs: {', '.join(genomics_programs)}")

    variants_dir = session_dir / "genomic_data"
    variants_dir.mkdir(exist_ok=True)

    # Filter out invalid program_sample_ids
    valid_program_sample_ids = []
    for program_sample_id in program_sample_ids:
        try:
            program_id, sample_id = program_sample_id.split("~")
            if program_id in genomics_programs:
                valid_program_sample_ids.append(program_sample_id)
            else:
                logger.info(f"Skipping program ID not in genomics: {program_sample_id}")
        except ValueError:
            logger.error(f"\nInvalid program_sample_id format: {program_sample_id}")
            continue

    if not valid_program_sample_ids:
        logger.warning("No valid program-sample IDs to process after filtering")
        return

    for program_sample_id in tqdm(
        valid_program_sample_ids, desc="Processing samples", unit="sample", position=0
    ):
        try:
            program_id, sample_id = program_sample_id.split("~")
        except ValueError:
            logger.error(f"\nInvalid program_sample_id format: {program_sample_id}")
            continue

        experiment_payload = genomics_helpers.build_experiment_request_payload(
            program_id=program_id, submitter_sample_id=sample_id
        )
        
        # get experiment objects, which contain the object names
        # and then get the analysis drs objects to find out the download link
        experiment_objs_response = execute_federation_call(
            federation_url=federation_url,
            headers=headers,
            payload=experiment_payload,
        )

        has_data_to_download = False
        if experiment_objs_response:
            for location_data in experiment_objs_response:
                if not location_data.get("results"):
                    continue

                for result in location_data["results"]:
                    for content in result.get("contents", []):
                        analysis_drs_name = content.get("name")
                        if not analysis_drs_name:
                            continue

                        analysis_drs_payload = (
                            genomics_helpers.build_analysis_drs_request_payload(
                                analysis_drs_name
                            )
                        )
                        analysis_drs_objs_response = execute_federation_call(
                            federation_url=federation_url,
                            headers=headers,
                            payload=analysis_drs_payload,
                        )

                        if analysis_drs_objs_response:
                            has_data_to_download = True
                            # Create directory only when we have data to download
                            sample_dir = variants_dir / f"{program_id}-{sample_id}"
                            sample_dir.mkdir(exist_ok=True)
                            logger.info(
                                f"Created directory for sample {program_sample_id} at {sample_dir}"
                            )
                            
                            process_analysis_drs_objects(
                                analysis_drs_objs_response=analysis_drs_objs_response,
                                headers=headers,
                                federation_url=federation_url,
                                variants_dir=sample_dir,
                                is_dry_run=is_dry_run,
                            )

        if not has_data_to_download:
            logger.info(
                f"No data available for download for sample {program_sample_id}"
            )
