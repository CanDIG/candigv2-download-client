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

# create virtual environment
uv venv
source .venv/bin/activate

# install dependencies
uv pip install -e .
```

## Configure CanDIG instance

Change the value of `DEFAULT_BASE_URL` to the CanDIG instance you will be downloading from in `src/client/config.py`.

## Usage

The program can download either clinical only, variant only, or all data a user is authorized for using the following arguments: `--clinical` or `--variant` or `--all`. The data downloaded can be further filtered using clinical and genomic parameters described in detail below.

```bash
candig-download [OUTPUT_TYPE] [FILTER]
```

## Authentication

- You can provide the token using the `--token YOUR_TOKEN` argument.
- If `--token` is not provided, the script will prompt you to enter the token securely in the terminal

## Options

**Arguments:**

- **Donor Filters:**
  - `--gene-id`: Filter to donors that have mutations in a particular gene (e.g., `SLX9`)
  - `--coord`: Filter to donors that have mutations in a particular genomic region (e.g., `chr1:10000-20000`)
  - `--treatment-type`: Filter to donors treated by one or more treatment types.
  - `--drug-name`: Filter to donors treated with one or more systemic therapy drugs.
  - `--primary-site`: Filter to donors with a tumour diagnosed in one or more primary sites.
  - `--program-id`: Filter to donors from one or more program IDs.
- **Output type:**
  - `--all|-a`: If specified, downloads all clinical and variant data specified (will eventually include transcriptome matrices too)
  - `--clinical|-c`: If specified, downloads clinical data
  - `--variant|-v`: If specified, downloads variant data
  - `--log-level|-ll`: set the logging level (10=DEBUG, 20=INFO, 30=WARNING, 40=ERROR, 50=CRITICAL). Default is WARNING (30)
  - `--dry-run|-d`: If specified, shows what would be downloaded (record counts, file sizes). Note that variant dry-run would still download the clinical data for filtering purpose.
  - `--resume|-r` continue the download by locating the existing session folder
  - *Coming soon* `--matrix|-m`: If specified, downloads transcriptomic matrices for filtered donors

> [!Tip]
> Filters must be individually quoted strings.

> [!Note]
> If no filters/args are provided, the program will attempt to download *all* data available to user.

> [!CAUTION]
> Filters must match those indicated in the data portal exactly and are case-sensitive.

**Examples:**

1. **Fetch all available data types for all programs you have authorization for:**

    ```bash
    candig-download -a --token YOUR_TOKEN
    ```

2. **Fetch clinical data for donors with mutation in a gene ID with verbose logging:**

    ```bash
    candig-download -ll 10 -c --gene-id SLX9 --token YOUR_TOKEN
    
    ```

3. **Fetch variant data for donors with mutation in a gene ID in dry mode:**

    ```bash
    candig-download -d -v --gene-id SLX9 --token YOUR_TOKEN
    ```

4. **Fetch all available data for donors with mutation in a gene ID:**

    ```bash
    candig-download --gene-id SLX9 -a --token YOUR_TOKEN
    ```

5. **Fetch clinical and variant data where donors have mutations within the matching coordinates:**

    ```bash
    candig-download -c -v --coord "chr21:10522300-10530000" --token YOUR_TOKEN
    ```

6. **Fetch all available data for donors with primary site identified as either `Colon` or `Bronchus and Lung`:**

    ```bash
    candig-download -c --primary-site "Colon" "Bronchus and lung" --token YOUR_TOKEN
    ```

7. **Fetch all available data for donors that were treated with the drug `Durvalumab` (allowing for multiple case-sensitive options):**

    ```bash
    candig-download -a --drug-name "Durvalumab" "durvalumab" --token YOUR_TOKEN
    ```

8. **Download all variants for all donors from all authorized programs within the `SLX9` gene:**

     ```bash
     candig-download -v --gene-id SLX9 --token YOUR_TOKEN
     ```

9. **Resume download**

    ```bash
    candig-download -r candig_downloads/{session_id} --token YOUR_TOKEN
    ```

## License

This project is licensed under GNU LESSER GENERAL PUBLIC LICENSE - see the [LICENSE](LICENSE) file for details.
