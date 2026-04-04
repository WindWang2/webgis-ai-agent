import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from app.main import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
async def async_client():
    async with AsyncClient(app=app, base_url="http://testserver") as ac:
        yield ac
