"""logging setup"""
import logging
import logging.config
from json import load
from pathlib import Path
from typing import Dict, Union

LOGGER = logging.getLogger(__name__)


def _load(config_file_path: Path) -> Dict:
    """Load logging configuration"""
    with config_file_path.open("r") as config_file:
        logging_config = load(config_file)
    return logging_config


def _override(*cls):
    """Override other attempts to set the Logger class."""
    LOGGER.info("Attempt to override Logger class, class=%s", cls)


def _reload_logging(file: Path):
    try:
        logging_config = load_logging(file)
        setup_logging(logging_config)
    except ValueError as err:
        LOGGER.error(
            "Failed to parse logging configuration '%s': %s", str(file), str(err)
        )


def setup_logging(logging_config: Union[Dict, str, Path]):
    """Configure the logging framework"""
    if isinstance(logging_config, (Path, str)):
        if isinstance(logging_config, str):
            logging_config_file = Path(logging_config)
        else:
            logging_config_file = logging_config

        logging_config_file = logging_config_file.expanduser().absolute()
        logging_config = _load(logging_config_file)

    logging.config.dictConfig(logging_config)


def load_logging(config_file_path: Path):
    """Setup logging system and return logging configuration"""
    return _load(config_file_path)
