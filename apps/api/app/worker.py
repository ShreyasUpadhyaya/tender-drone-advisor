"""Placeholder worker process for the C01 service topology."""

import logging
import time

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def main() -> None:
    LOGGER.info("Worker foundation is running; no jobs are registered in C01.")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
