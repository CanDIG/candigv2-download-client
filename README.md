# CanDIGv2 Download Client

A command-line tool for exporting clinical data from CanDIG servers.

## Overview

The CanDIG Download Client provides a way to download clinical data from CanDIG federated networks. This tool allows users to:

- Connect to CanDIG servers with authentication
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

### Configuration options

*   `--base-url URL`: The base URL of the CanDIG deployment (Default: `http://candig.docker.internal:5080`).
*   `--token TOKEN`: Authentication bearer token. Prompts if not provided.
*   `--timeout SECONDS`: Request timeout in seconds (Default: 60 seconds).

### Data Download options

**Arguments:**

*   `--output-dir DIR`: Directory to save the output files (Default: `candig_downloads`).
*   **Donor Filters:**
    *   `--gene-id`: Filter to donors that have mutations in a particular gene (e.g., `SLX9`)
    *   `--assembly --chrom --start --end`: Filter to donors that have mutations in a particular genomic region (e.g., `--assembly hg38 --chrom 1 --start 10000 --end 20000`)
    *   `--treatment-type`: Filter to donors treated by one or more treatment types.
    *   `--drug-name`: Filter to donors treated with one or more systemic therapy drugs.
    *   `--primary-site`: Filter to donors with a tumour diagnosed in one or more primary sites.
    *   `--program-id`: Filter to donors from one or more program IDs.
*   **Output type:**
    *   `--all|-a`: If specified, downloads all clinical and variant data specified (will eventually include transcriptome matrices too)
    *   `--clinical|-c`: If specified, downloads clinical data
    *   `--variant|-v`: If specified, downloads variant data
    *   `--variant-format`: Must be one of `beacon` or `vcf`, returns the variants in the given format (Default=`vcf`)
    *   *Coming soon* `--matrix|-m`: If specified, downloads transcriptomic matrices for filtered donors 

> [!Tip]
> Filters must be individually quoted strings.

> [!Note]
> If no filters/args are provided, the program will attempt to download *all* data available to user.

> [!CAUTION]
> Filters must match those indicated in the data portal exactly and are case-sensitive.

**Examples:**

1.  **Fetch all available data types for all programs you have authorization for:**
    ```bash
    python src/client/main.py --token YOUR_TOKEN
    ```
2.  **Fetch clinical data for donors with mutation in a gene ID:**
    ```bash
    python src/client/main.py --gene-id SLX9 --clinical --token YOUR_TOKEN
    ```
3.  **Fetch clinical and variant data for donors with mutation in a gene ID:**
    ```bash
    python src/client/main.py --gene-id SLX9 -c -v --token YOUR_TOKEN
    ```
4.  **Fetch all available data for donors with mutation in a gene ID:**
    ```bash
    python src/client/main.py --gene-id SLX9 --token YOUR_TOKEN
    ```
5.  **Fetch clinical and variant data where donors have mutations within the matching coordinates:**
    ```bash
    python src/client/main.py -c -v --assembly hg38 --chrom 21 --start 10522300 --end 10530000 --token YOUR_TOKEN
    ```
6.  **Fetch all available data for donors with primary site identified as either `Colon` or `Bronchus and Lung`:**
    ```bash
    python src/client/main.py --primary-site "Colon" "Bronchus and lung" --token YOUR_TOKEN
    ```
7.  **Fetch all available data for donors that were treated with the drug `Durvalumab` (allowing for multiple case-sensitive options):**
    ```bash
    python src/client/main.py --drug-name "Durvalumab" "durvalumab" --token YOUR_TOKEN
    ```
8.  **Download all variants for all donors from all authorized programs:**
     ```bash
     python src/client/main.py -v --token YOUR_TOKEN
     ```
9.  **Download all variants for all donors from all authorized programs within the `SLX9` gene:**
     ```bash
     python src/client/main.py -v --filter-gene SLX9 --token YOUR_TOKEN
     ```

## License

This project is licensed under GNU LESSER GENERAL PUBLIC LICENSE - see the [LICENSE](LICENSE) file for details.