"""Redis worker for C02 tender ingestion jobs."""

import logging

from redis import Redis
from rq import Queue, Worker

from app.db import SessionLocal
from app.recovery import mark_stale_jobs
from app.settings import get_settings

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    # Recovery is conservative: stale jobs are marked terminal and must be
    # explicitly replayed through their idempotent API request.
    with SessionLocal() as db:
        recovered = mark_stale_jobs(db, settings.job_stale_seconds)
    LOGGER.info("stale_job_recovery=%s", recovered)
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.ingestion_queue, connection=connection)
    LOGGER.info("Starting tender ingestion worker queue=%s", settings.ingestion_queue)
    Worker([queue], connection=connection).work()


if __name__ == "__main__":
    main()
