import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402

TOKEN = "test-token"
SAMPLE_BYTES = (ROOT / "sample_data" / "sunrise_bakery_transactions.csv").read_bytes()


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "SECRET_KEY": "test",
                       "DATABASE": str(tmp_path / "test.db")})


def new_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf"] = TOKEN
    return client


def token(client):
    with client.session_transaction() as sess:
        return sess.setdefault("csrf", TOKEN)


def register(client, username="alice"):
    return client.post("/register", data={"username": username, "password": "password123",
                                          "csrf_token": token(client)})


@pytest.fixture
def client(app):
    return new_client(app)


@pytest.fixture
def logged_in(client):
    register(client)
    return client


os.environ.setdefault("MPLBACKEND", "Agg")
