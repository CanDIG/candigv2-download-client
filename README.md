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

### Configuration options
Located in config.py where you can change DEFAULT_BASE_URL or DATA_OUTPUT_DIR

## Usage

The script operates in one of two main modes, selected by a required argument: `--clinical` or `--variant`.

```bash
python src/client/main.py [MODE] [OPTIONS...]
```

### Authentication

*   You can provide the token using the `--token YOUR_TOKEN` argument.
*   If `--token` is not provided, the script will prompt you to enter the token securely in the terminal

### Data Download options

**Arguments:**

*   **Donor Filters:**
    *   `--gene-id`: Filter to donors that have mutations in a particular gene (e.g., `SLX9`)
    *   `--coord`: Filter to donors that have mutations in a particular genomic region (e.g., `chr1:10000-20000`)
    *   `--treatment-type`: Filter to donors treated by one or more treatment types.
    *   `--drug-name`: Filter to donors treated with one or more systemic therapy drugs.
    *   `--primary-site`: Filter to donors with a tumour diagnosed in one or more primary sites.
    *   `--program-id`: Filter to donors from one or more program IDs.
*   **Output type:**
    *   `--all|-a`: If specified, downloads all clinical and variant data specified (will eventually include transcriptome matrices too)
    *   `--clinical|-c`: If specified, downloads clinical data
    *   `--variant|-v`: If specified, downloads variant data
    *   `--log-level|--ll`: set the logging level (10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL). Default is WARNING (30)
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
    python src/client/main.py -a --token YOUR_TOKEN
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
    python src/client/main.py --gene-id SLX9 -a --token YOUR_TOKEN
    ```
5.  **Fetch clinical and variant data where donors have mutations within the matching coordinates:**
    ```bash
    python src/client/main.py -c -v --coord "chr21:10522300-10530000" --token YOUR_TOKEN
    ```
6.  **Fetch all available data for donors with primary site identified as either `Colon` or `Bronchus and Lung`:**
    ```bash
    python src/client/main.py -c --primary-site "Colon" "Bronchus and lung" --token YOUR_TOKEN
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

```
python src/client/main.py --gene-id TPTE --clinical
```

