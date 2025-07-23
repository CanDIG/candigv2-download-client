import logging
import sys
from typing import Any, Dict, List, Optional, Set

from client import config

logger = logging.getLogger(__name__)


def parse_coord_string(coord_string: str):
    """Parse a coordinate string into chromosome, start, and end."""
    chromosomes = [str(x) for x in list(range(1, 23))] + ["X", "Y"]
    chr_chromosomes = ["chr" + x for x in chromosomes]
    all_chromosomes = chromosomes + chr_chromosomes
    try:
        split_chrom = coord_string.split(":")
        chrom = split_chrom[0]
        split_pos = split_chrom[1].split("-")
        start = int(split_pos[0])
        end = int(split_pos[1])
    except IndexError:
        logger.error(
            f"Coordinate string invalid: `{coord_string}` is not formatted correctly, please ensure it follows the pattern <chrom>:<start>-<end>."
        )
        sys.exit()
    except ValueError:
        logger.error(
            f"Coordinate values invalid: start and end coordinates must be integers. Please ensure it follows the pattern <chrom>:<start>-<end>."
        )
        sys.exit()
    if chrom not in all_chromosomes:
        logger.error("Chromosome invalid: indicate chromosome with [chr]1-22, X, Y")
        sys.exit()
    if start > end:
        logger.error(
            "Coordinates invalid: start coordinate cannot be larger than end coordinate. Please ensure it follows the pattern <chrom>:<start>-<end>."
        )
        sys.exit()
    return {"chrom": chrom, "start": start, "end": end}


def build_beacon_request_payload(
    gene_id: Optional[str] = None,
    assembly: Optional[str] = None,
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    request_parameters: Dict[str, Any] = {}
    if gene_id:
        request_parameters["gene_id"] = gene_id
    elif assembly and chrom and start is not None and end is not None:
        request_parameters = {
            "assemblyId": assembly,
            "referenceName": chrom,
            "start": [start],
            "end": [end],
        }
    else:
        logger.error("Error: Invalid parameters for building Beacon search request.")
        return None

    return {
        "path": config.BEACON_ENDPOINT,
        "payload": {
            "meta": {"apiVersion": "v2"},
            "query": {"requestParameters": request_parameters},
        },
        "method": "POST",
        "service": config.GENOMICS_SERVICE,
    }

def build_file_drs_request_payload(
    analysis_name: str,
) -> Optional[Dict[str, Any]]:
    return {
        "path": config.DRS_ENDPOINT + "/" + analysis_name,
        "payload": {},
        "method": "GET",
        "service": config.GENOMICS_SERVICE,
    }


def extract_unique_program_sample_ids_from_beacon_results(
    beacon_results: Optional[List[Dict[str, Any]]],
) -> List[str]:
    unique_samples: Set[str] = set()
    logger.info("Extracting biosample_ids from Beacon results...")
    processed_sources = 0
    for source_data in beacon_results:
        if source_data.get("error"):
            logger.warning(
                f"Skipping source due to reported error: {source_data.get('source', 'Unknown Source')}"
            )
            continue
        results = source_data.get("results")
        if not results:
            continue
        estimated_results = results.get("estimatedResults", {})
        if not estimated_results:
            processed_sources += 1
            continue
        for program_id, sample_list in estimated_results.items():
            if not isinstance(sample_list, list):
                continue
            for sample_info in sample_list:
                if isinstance(sample_info, dict):
                    submitter_sample_id = sample_info.get("submitter_sample_id")
                    if isinstance(submitter_sample_id, str) and submitter_sample_id:
                        biosample_id = f"{program_id}~{submitter_sample_id}"
                        unique_samples.add(biosample_id)
        processed_sources += 1
    logger.info(
        f"Processed {processed_sources} source(s). Found {len(unique_samples)} unique sample IDs: {unique_samples}"
    )
    return list(unique_samples)
