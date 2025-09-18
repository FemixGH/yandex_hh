import os

REQUIRED_VARS = {
    "S3_ENDPOINT": os.getenv("S3_ENDPOINT"),
    "S3_ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
    "S3_SECRET_KEY": os.getenv("S3_SECRET_KEY"),
    "S3_BUCKET": os.getenv("S3_BUCKET"),
    "S3_PREFIX": os.getenv("S3_PREFIX"),
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN"),
}
