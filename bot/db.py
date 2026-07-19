from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from bot.config import settings

_engine = None
_session_factory = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings().database_url, pool_pre_ping=True)
    return _engine


@contextmanager
def session() -> Session:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=engine(), expire_on_commit=False)
    s = _session_factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
