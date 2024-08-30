import logging


def setup_logging():
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(
            level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
