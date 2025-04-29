import logging
from config.settings import LOG_LEVEL, LOG_FORMAT, LOG_FILE

def setup_logging():
    """Configure logging for the application."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE)
        ]
    )
    return logging.getLogger(__name__)

# Create a logger instance
logger = setup_logging() 