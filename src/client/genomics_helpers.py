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
