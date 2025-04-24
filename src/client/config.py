# config.py
"""Configuration constants for the CanDIG client."""

# --- General Settings ---
DEFAULT_BASE_URL = "http://candig.docker.internal:5080"
# DEFAULT_BASE_URL = "https://candig-demo.uhndata.io"
DEFAULT_TIMEOUT = 60.0
DATA_OUTPUT_DIR = "candig_downloads"

# --- API Paths ---
FEDERATION_PATH = "/federation/v1/fanout"

# genomics service config
GENOMICS_SERVICE_ENDPOINT = "/genomics/htsget/v1/reads/data/"
GENOMICS_SERVICE = "htsget"
BEACON_ENDPOINT = "beacon/v2/g_variants"        

# clinical service config
CLINICAL_SERVICE_ENDPOINT = "v3/download/clinical_data/" 
CLINICAL_SERVICE = "katsu"
