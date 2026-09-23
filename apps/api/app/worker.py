"""Redis worker for C02 tender ingestion jobs."""

import logging

from redis import Redis
from rq import Queue, Worker

from app.settings import get_settings

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.ingestion_queue, connection=connection)
    LOGGER.info("Starting tender ingestion worker queue=%s", settings.ingestion_queue)
    Worker([queue], connection=connection).work()


if __name__ == "__main__":
    main()
