"""Configuration constants for the CanDIG client."""

# ====================================
# External Settings (User-editable)
# ====================================
DEFAULT_BASE_URL = "http://candig.docker.internal:5080"
TIMEOUT = 60.0
DATA_OUTPUT_DIR = "candig_downloads"
LOG_LEVEL = 30
DOWNLOAD_MAX_SIZE = 500_000_000  

# ====================================
# Internal Settings (Do not edit)
# ====================================

# API Paths
FEDERATION_PATH = "/federation/v1/fanout"

# Genomics Service Config
GENOMICS_SERVICE_ENDPOINT = "/genomics/htsget/v1/reads/data/"
GENOMICS_SERVICE = "htsget"
BEACON_ENDPOINT = "beacon/v2/g_variants"
DRS_ENDPOINT = "ga4gh/drs/v1/objects"

# Clinical Service Config
CLINICAL_SERVICE_ENDPOINT = "v3/download/clinical_data/"
CLINICAL_SERVICE = "katsu"
