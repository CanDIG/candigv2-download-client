import getpass
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_auth_token(token_arg: Optional[str]) -> Optional[str]:
    """
    Gets the authentication token, either from the command-line argument
    or by prompting the user.
    """
    if token_arg:
        logger.info("Using token from command-line argument.")
        return token_arg
    try:
        token = getpass.getpass("Enter your authentication token: ")
        return token if token else None
    except EOFError:
        logger.error("Could not read token (EOF encountered).")
        return None
    except Exception as e:
        logger.error(f"Error reading token: {e}")
        return None
