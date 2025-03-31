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

Once installed, you can run the tool in 2 ways:

Quick Mode (no installation required):

```bash
uv run candigv2-client
```

Edit Mode (for persistent development):

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
candigv2-client
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.