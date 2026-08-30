import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app.config import settings
from app.services.media_service import ensure_bucket, seed_media_from_disk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Seed media files into MinIO")
    parser.add_argument(
        "--bot-data-dir",
        default=settings.BOT_DATA_DIR,
        help="Path to bot_data directory",
    )
    args = parser.parse_args()

    bot_data_dir = os.path.abspath(args.bot_data_dir)
    logger.info(f"Bot data dir: {bot_data_dir}")

    if not os.path.exists(bot_data_dir):
        logger.error(f"Directory not found: {bot_data_dir}")
        sys.exit(1)

    logger.info("Ensuring MinIO bucket exists...")
    ensure_bucket()

    logger.info("Seeding media files...")
    seed_media_from_disk(bot_data_dir)
    logger.info("Done!")


if __name__ == "__main__":
    main()
