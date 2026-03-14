import pytest

from .mock_mgba_server import MockMGBAServer


@pytest.fixture
async def mock_mgba():
    """Start a mock mGBA server on port 5001 and stop it after the test."""
    server = MockMGBAServer(port=5001)
    await server.start()
    yield server
    await server.stop()
