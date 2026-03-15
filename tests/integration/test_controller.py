"""Integration tests for the full controller loop against the mock mGBA server.

These tests run the real MGBAClient and KnowledgeBase against a mock TCP server
so the full observe → decide → execute cycle can be validated without mGBA.
"""

from src.agent.controller import PokemonAgentController
from src.agent.models import AgentDecision
from tests.fixtures import mock_mgba  # noqa: F401 — imported for pytest fixture discovery


async def test_controller_runs_3_iterations(mock_mgba, tmp_path):
    """Controller completes 3 iterations against mock mGBA without errors."""
    kb_path = tmp_path / "test.db"
    rom_path = tmp_path / "fake.gba"
    rom_path.touch()
    lua_path = tmp_path / "fake.lua"
    lua_path.touch()

    controller = PokemonAgentController(
        rom_path=rom_path,
        lua_script_path=lua_path,
        knowledge_db_path=kb_path,
        host="127.0.0.1",
        port=5001,
        launch_mgba=False,
    )

    iteration_count = 0

    async def mock_get_decision(obs):
        nonlocal iteration_count
        iteration_count += 1
        if iteration_count >= 3:
            controller.running = False
        return AgentDecision(
            action_type="wait",
            action_params={"frames": 5},
            reasoning="Test decision",
        )

    controller._get_decision = mock_get_decision

    await controller.knowledge_base.initialize()
    await controller.mgba_client.connect(retries=3, delay=0.1)

    controller.running = True
    controller._game_loop_done.clear()
    await controller.game_loop()

    assert iteration_count == 3
    await controller.knowledge_base.close()
    await controller.mgba_client.disconnect()


async def test_controller_records_knowledge_during_loop(mock_mgba, tmp_path):
    """Knowledge entries from decisions are stored in the knowledge base."""
    kb_path = tmp_path / "test.db"
    rom_path = tmp_path / "fake.gba"
    rom_path.touch()
    lua_path = tmp_path / "fake.lua"
    lua_path.touch()

    controller = PokemonAgentController(
        rom_path=rom_path,
        lua_script_path=lua_path,
        knowledge_db_path=kb_path,
        host="127.0.0.1",
        port=5001,
        launch_mgba=False,
    )

    async def mock_get_decision(obs):
        controller.running = False
        return AgentDecision(
            action_type="wait",
            action_params={"frames": 1},
            reasoning="Test",
            knowledge_to_store=[
                {
                    "category": "location",
                    "title": "Littleroot Town",
                    "description": "Starting town, Prof Birch is here",
                }
            ],
        )

    controller._get_decision = mock_get_decision

    await controller.knowledge_base.initialize()
    await controller.mgba_client.connect(retries=3, delay=0.1)

    controller.running = True
    controller._game_loop_done.clear()
    await controller.game_loop()

    results = await controller.knowledge_base.get_relevant_knowledge("Littleroot")
    assert len(results) == 1
    assert results[0]["title"] == "Littleroot Town"

    await controller.knowledge_base.close()
    await controller.mgba_client.disconnect()


async def test_guidance_stored_and_retrievable(mock_mgba, tmp_path):
    """User guidance stored directly in KB is retrievable during the loop."""
    kb_path = tmp_path / "test.db"
    rom_path = tmp_path / "fake.gba"
    rom_path.touch()
    lua_path = tmp_path / "fake.lua"
    lua_path.touch()

    controller = PokemonAgentController(
        rom_path=rom_path,
        lua_script_path=lua_path,
        knowledge_db_path=kb_path,
        host="127.0.0.1",
        port=5001,
        launch_mgba=False,
    )

    await controller.knowledge_base.initialize()
    gid = await controller.knowledge_base.add_user_guidance("Go north to Route 103")
    active = await controller.knowledge_base.get_active_guidance()
    assert any(g["id"] == gid for g in active)
    await controller.knowledge_base.close()


async def test_controller_game_state_populated(mock_mgba, tmp_path):
    """Game state returned by mock server is correctly parsed into GameState."""
    from src.agent.models import GameState

    kb_path = tmp_path / "test.db"
    rom_path = tmp_path / "fake.gba"
    rom_path.touch()

    controller = PokemonAgentController(
        rom_path=rom_path,
        lua_script_path=rom_path,
        knowledge_db_path=kb_path,
        host="127.0.0.1",
        port=5001,
        launch_mgba=False,
    )

    await controller.mgba_client.connect(retries=3, delay=0.1)
    state_data = await controller.mgba_client.request_state()
    game_state = GameState.from_dict(state_data)

    assert game_state.map_id == 3
    assert game_state.player_x == 15
    assert len(game_state.party) == 2
    assert game_state.party[0].species_name == "Torchic"

    await controller.mgba_client.disconnect()
