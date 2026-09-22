import os
import time
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from alembic.config import Config
from alembic import command


def wait_for_db():
    for i in range(30):
        try:
            conn = psycopg2.connect(
                dbname=os.environ.get("POSTGRES_DB", "fastdic_bot"),
                user=os.environ.get("POSTGRES_USER", "fastdic"),
                password=os.environ.get("POSTGRES_PASSWORD", "fastdic"),
                host="db",
                port=5432,
            )
            conn.close()
            print("Database is ready")
            return
        except psycopg2.OperationalError:
            print(f"Waiting for database... ({i+1}/30)")
            time.sleep(2)
    print("Could not connect to database")
    sys.exit(1)


if __name__ == "__main__":
    wait_for_db()
    cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    command.upgrade(cfg, "head")
    print("Migrations done")
