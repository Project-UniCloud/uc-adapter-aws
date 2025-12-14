import logging
import sys


def setup_logger(name: str) -> logging.Logger:
    """
    Configures a logger with a standard format for the entire application.
    Output: Timestamp [Level] [Module] Message
    """
    logger = logging.getLogger(name)

    # Check if handlers already exist to avoid duplicate logs
    if not logger.handlers:
        logger.setLevel(logging.INFO)

        # Create console handler
        handler = logging.StreamHandler(sys.stdout)

        # Define format
        formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
        handler.setFormatter(formatter)

        logger.addHandler(handler)

    return logger