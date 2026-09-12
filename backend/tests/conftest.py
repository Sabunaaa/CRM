import os
import sys
from pathlib import Path

import pytest
from argon2 import PasswordHasher

TEST_DB = Path(__file__).parent / "test.db"
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["TEAM_PASSWORD_HASH"] = PasswordHasher().hash("team-secret")
os.environ["SESSION_SECRET"] = "test-session-secret-with-enough-entropy"
os.environ["SECURE_COOKIES"] = "false"
os.environ["COLLECTOR_JOB_NAME"] = ""

from app.database import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
