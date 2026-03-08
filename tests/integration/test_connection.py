"""Manual end-to-end connection test.

Run this after launching mGBA with the Lua script to verify bidirectional
communication works correctly.

Usage:
    uv run python tests/fixtures/test_connection.py
    (mGBA must be running with src/lua_scripts/pokemon_agent.lua loaded)
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.agent.mgba_client import MGBAClient


@pytest.mark.skip(reason="Manual E2E test — requires live mGBA instance on port 5000")
async def test_connection():
    client = MGBAClient(host="127.0.0.1", port=5000)

    print("Connecting to mGBA...")
    await client.connect(retries=5, delay=2.0)
    print("Connected.")

    # Test 1: get_state
    print("\n[Test 1] Requesting game state...")
    response = await client.send_request("get_state")
    print(f"  Response: {response}")
    assert response["type"] == "response", f"Expected 'response', got {response['type']}"
    assert response["payload"]["status"] == "ok", f"Unexpected status: {response['payload']}"
    assert "frame_number" in response["payload"], "Missing frame_number in response"
    print("  PASS: get_state returns valid response with frame_number")

    # Test 2: frame counter increments
    print("\n[Test 2] Checking frame counter increments...")
    frame1 = response["payload"]["frame_number"]
    await asyncio.sleep(0.1)
    response2 = await client.send_request("get_state")
    frame2 = response2["payload"]["frame_number"]
    print(f"  Frame 1: {frame1}, Frame 2: {frame2}")
    assert frame2 >= frame1, f"Frame counter did not increment: {frame1} -> {frame2}"
    print("  PASS: Frame counter is incrementing")

    # Test 3: unknown action returns error
    print("\n[Test 3] Testing unknown action error handling...")
    err_response = await client.send_request("nonexistent_action")
    print(f"  Response: {err_response}")
    assert err_response["payload"]["status"] == "error", "Expected error status"
    assert err_response["payload"]["error_code"] == "UNKNOWN_ACTION"
    print("  PASS: Unknown action returns structured error")

    # Test 4: press_button stub
    print("\n[Test 4] Testing press_button stub...")
    btn_response = await client.send_request("press_button", button="A", duration_frames=5)
    print(f"  Response: {btn_response}")
    assert btn_response["payload"]["status"] == "ok"
    print("  PASS: press_button stub responds ok")

    await client.disconnect()
    print("\n✓ All tests passed — bidirectional communication verified")


if __name__ == "__main__":
    asyncio.run(test_connection())
