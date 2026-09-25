from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


# Creating the engine does not connect to PostgreSQL.
engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=5,
    connect_args={
        "connect_timeout": 3,
        "options": "-c statement_timeout=5000 -c lock_timeout=3000",
    },
    hide_parameters=True,
)
SessionLocal = sessionmaker(bind=engine)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
