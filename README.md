# CANDIGv2 Download Client

A command-line tool for exporting clinical data from CANDIG servers.

## Overview

The CANDIG Download Client provides a way to download clinical data from CANDIG federated networks. This tool allows users to:

- Connect to CANDIG servers with authentication
- Select specific programs to download

## Install

```bash
# Install UV if you don't have it yet
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/CanDIG/candigv2-download-client.git
cd candigv2-download-client
```

## Usage

You can run the tool in two ways:

#### Quick Mode

```bash
uv run python -m src.client.main
```

#### Development Mode

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
candigv2-client
```

## Usage

The script operates in one of two main modes, selected by a required argument: `--clinical-download` or `--htsget-download`.

```bash
python src/client/main.py [MODE] [OPTIONS...]
```

### Authentication

*   You can provide the token using the `--token YOUR_TOKEN` argument.
*   If `--token` is not provided, the script will prompt you to enter the token securely in the terminal

### Common Arguments

*   `--base-url URL`: The base URL of the CANDIG federation server (Default: `http://candig.docker.internal:5080`).
*   `--token TOKEN`: Authentication bearer token. Prompts if not provided.
*   `--timeout SECONDS`: Request timeout in seconds (Default: 30 seconds).

### Mode 1: Clinical Data Download (`--clinical-download`)

This mode downloads clinical data, optionally filtered.

**Arguments:**

*   `--output-dir DIR`: Directory to save the output CSV files (Default: `clinical_downloads`).
*   **Gene Search Filters:**
    *   `--gene-id`: Perform a gene search for samples associated with this Gene ID (e.g., `SLX9`) before fetching their clinical data.
    *   `--assembly --chrom --start --end`: Perform a gene search for samples within these genomic coordinates (e.g., `--assembly hg38 --chrom 1 --start 10000 --end 20000`) before fetching their clinical data.
*   **Clinical Data Filters:**
    *   `--treatment-type`: Filter by one or more treatment types.
    *   `--primary-site`: Filter by one or more primary sites.
    *   `--drug-name`: Filter by one or more systemic therapy drug names.
    *   `--program-id`: Filter by one or more program IDs.

**Note:** If no filters are provided with `--clinical-download`, the script will attempt to download *all* clinical data available to user

**Examples:**

1.  **Fetch all clinical data:**
    ```bash
    python src/client/main.py --clinical-download --token YOUR_TOKEN
    ```
2.  **Fetch data for samples matching a gene ID:**
    ```bash
    python src/client/main.py --clinical-download --gene-id SLX9 --token YOUR_TOKEN
    ```
3.  **Fetch data for samples matching coordinates:**
    ```bash
    python src/client/main.py --clinical-download --assembly hg38 --chrom 21 --start 10522300 --end 10530000 --token YOUR_TOKEN
    ```
4.  **Fetch data filtered directly by primary site and specific sample IDs:**
    ```bash
    python src/client/main.py --clinical-download --primary-site "Colon" "Brain" --token YOUR_TOKEN
    ```
5.  **Fetch data filtered directly by drug name:**
    ```bash
    python src/client/main.py --clinical-download --drug-name "Durvalumab" --token YOUR_TOKEN
    ```

### Mode 2: HTSget Genomic Reads Download (`--htsget-download`)

This mode downloads genomic reads data for a single sample using the HTSget protocol.

**Arguments:**

*   `--htsget-sample-id`: **Required.** The Sample ID for which to download reads (e.g., `SAMPLE_001`).
*   `--htsget-output-dir DIR`: Directory to save the downloaded HTSget file (Default: `htsget_downloads`).
*   `--htsget-url URL`: Specific base URL for the HTSget service. If not provided, it defaults to the `--base-url`.
*   **Coordinate Filters (Optional - for downloading a specific region):**
    *   `--chrom CHR --start START --end END`: Download only the region specified by chromosome, start, and end position.

**Examples:**

1.  **Download the whole reads file for a sample:**
    ```bash
    python src/client/main.py --htsget-download --sample-id LOCAL-test --token YOUR_TOKEN
    ```
2.  **Download a specific region for a sample:**
    ```bash
    python src/client/main.py --htsget-download --sample-id LOCAL-test --chrom chr1 --start 0 --end 1000000 --token YOUR_TOKEN
    ```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.