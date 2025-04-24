# htsget_utils.py
"""Utilities for HTSget/Beacon payload building and result processing."""

# Remove httpx, json imports if no longer used here
import os
import sys
from typing import Dict, Any, Optional, List, Set
import config # Import config for service name/paths


def build_beacon_request_payload(
    gene_id: Optional[str] = None,
    assembly: Optional[str] = None,
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    request_parameters: Dict[str, Any] = {}
    if gene_id:
        request_parameters["gene_id"] = gene_id
    elif assembly and chrom and start is not None and end is not None:
        request_parameters = {
            'assemblyId': assembly, 'referenceName': chrom,
            'start': [start], 'end': [end]
        }
    else:
        print("Error: Invalid parameters for building Beacon search request.", file=sys.stderr)
        return None

    return {
        "path": config.BEACON_PATH,
        "payload": {"meta": {"apiVersion": "v2"}, "query": {"requestParameters": request_parameters}},
        "method": "POST",
        "service": config.HTSGET_SERVICE,
    }


def extract_unique_biosample_ids(beacon_results: Optional[List[Dict[str, Any]]]) -> List[str]:
    if not beacon_results:
        print("No Beacon results received to extract biosamples from.")
        return []
    unique_samples: Set[str] = set()
    print("Extracting biosample_ids from Beacon results...")
    processed_sources = 0
    for source_data in beacon_results:
        if source_data.get("error"):
            print(f"Skipping source due to reported error: {source_data.get('source', 'Unknown Source')}")
            continue
        results = source_data.get('results')
        if not results: continue
        response_summary = results.get("responseSummary", {})
        if not response_summary.get("exists"):
            processed_sources += 1
            continue
        response_list = results.get("response", [])
        if not isinstance(response_list, list): continue
        for response_item in response_list:
            if not isinstance(response_item, dict): continue
            case_level_data = response_item.get("caseLevelData", [])
            if not isinstance(case_level_data, list): continue
            for sample_info in case_level_data:
                if isinstance(sample_info, dict):
                    biosample_id = sample_info.get("biosampleId")
                    if isinstance(biosample_id, str) and biosample_id:
                        unique_samples.add(biosample_id)
        processed_sources += 1
    print(f"Processed {processed_sources} source(s). Found {len(unique_samples)} unique sample IDs.")
    return list(unique_samples)

# def download_htsget_data(
#     htsget_base_url: str,
#     sample_id: str,
#     output_dir: str,
#     token: Optional[str],
#     timeout: float,
#     chrom: Optional[str] = None,
#     start: Optional[int] = None,
#     end: Optional[int] = None
# ) -> bool:
#     """
#     Downloads data from an HTSget endpoint and saves it to a file.
#     Coordinates (chrom, start, end) are optional. If not provided, downloads the whole file.
#     """
#     base = htsget_base_url.rstrip('/')
#     path = HTSGET_PATH.lstrip('/')
#     url = f"{base}/{path}{sample_id}"
#
#     params = {
#         "class": "body",
#     }
#     # Conditionally add coordinate parameters
#     if chrom:
#         params["referenceName"] = chrom
#     if start is not None:
#         params["start"] = start
#     if end is not None:
#         params["end"] = end
#
#     headers = {"Accept": "*/*"} # Accept any format the server provides (BAM, CRAM, etc.)
#     if token:
#         headers["Authorization"] = f"Bearer {token}"
#
#     os.makedirs(output_dir, exist_ok=True)
#     file_ext = ".txt" # Default extension
#     if chrom and start is not None and end is not None:
#         output_filename_base = f"{sample_id}_{chrom}_{start}-{end}"
#     else:
#         output_filename_base = f"{sample_id}"
#     output_filename = os.path.join(output_dir, f"{output_filename_base}{file_ext}")
#
#     try:
#         response = httpx.get(url, params=params, headers=headers, timeout=timeout)
#         response.raise_for_status()
#
#         content_disposition = response.headers.get("Content-Disposition")
#         if content_disposition:
#             parts = content_disposition.split('filename=')
#             if len(parts) > 1:
#                 potential_filename = parts[1].strip('"\' ')
#                 safe_filename = "".join(c if c.isalnum() or c in ('_', '-', '.') else '_' for c in potential_filename)
#                 if safe_filename:
#                      if chrom and start is not None and end is not None:
#                           base_name, _ = os.path.splitext(output_filename_base)
#                           _, ext = os.path.splitext(safe_filename)
#                           if ext:
#                                output_filename = os.path.join(output_dir, f"{base_name}{ext}")
#                           else:
#                                output_filename = os.path.join(output_dir, f"{output_filename_base}{file_ext}")
#                      else:
#                           output_filename = os.path.join(output_dir, safe_filename)
#
#         print(f"Saving file to: {output_filename}")
#
#         # Write the entire response content
#         with open(output_filename, "wb") as f:
#             f.write(response.content)
#
#         return True
#
#     except httpx.HTTPStatusError as e:
#         print(f"\nHTTP error occurred during HTSget download: {e.response.status_code} - {e.request.url}")
#         try:
#             error_details = e.response.read()
#             print(f"Error details: {error_details.decode(errors='ignore')}")
#         except Exception as read_err:
#             print(f"Could not read error details from response body (Error: {read_err}).")
#         if os.path.exists(output_filename):
#             try:
#                 os.remove(output_filename)
#                 print(f"Removed potentially incomplete file: {output_filename}")
#             except OSError as rm_err:
#                 print(f"Error removing incomplete file {output_filename}: {rm_err}")
#         return False
#     except httpx.RequestError as e:
#         print(f"\nNetwork or connection error during HTSget download: {e}")
#         if os.path.exists(output_filename):
#              try:
#                  os.remove(output_filename)
#                  print(f"Removed potentially incomplete file: {output_filename}")
#              except OSError as rm_err:
#                  print(f"Error removing incomplete file {output_filename}: {rm_err}")
#         return False
#     except IOError as e:
#          print(f"\nError writing file {output_filename}: {e}")
#          # No need to remove file here as open likely failed before writing
#          return False
#     except Exception as e:
#         print(f"\nAn unexpected error occurred during HTSget download: {e}")
#         if os.path.exists(output_filename):
#              try:
#                  os.remove(output_filename)
#                  print(f"Removed potentially incomplete file: {output_filename}")
#              except OSError as rm_err:
#                  print(f"Error removing incomplete file {output_filename}: {rm_err}")
#         return False