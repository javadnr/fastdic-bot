import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

logger.info("Connecting to database...")
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=0,
    pool_timeout=30,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
logger.info("Database connected")


class Base(DeclarativeBase):
    pass
