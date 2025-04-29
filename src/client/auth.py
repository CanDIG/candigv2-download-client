# auth.py
"""Handles authentication token retrieval."""

import getpass
from typing import Optional
import sys


def get_auth_token(token_arg: Optional[str]) -> Optional[str]:
    """
    Gets the authentication token, either from the command-line argument
    or by prompting the user.
    """
    if token_arg:
        print("Using token from command-line argument.")
        return token_arg
    try:
        token = getpass.getpass("Enter your authentication token: ")
        return token if token else None
    except EOFError:
        print("\nError: Could not read token (EOF encountered).", file=sys.stderr)
        return None
    except Exception as e:
        print(f"\nError reading token: {e}", file=sys.stderr)
        return None