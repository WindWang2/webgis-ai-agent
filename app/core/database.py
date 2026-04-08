"""Database Core Module"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

class Base(DeclarativeBase):
    pass

def get_engine():
    return create_engine(
        settings.DATABASE_URL,
        connect_args={}
    )

Engine = get_engine()
SessionLocal = sessionmaker(bind=Engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=Engine)
