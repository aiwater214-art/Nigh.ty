import math

from server.player import Player
from server.world import WorldConfig, WorldState
import asyncio
import contextlib
import math

from server.player import Player

from server.world import (
    WorldConfig,
    WorldManager,
    WorldSnapshotRepository,
    WorldState,
)


def _make_player(name: str) -> Player:
    return Player(name=name, token=f"token-{name}")


def test_absorbed_cells_do_not_recollide():
    config = WorldConfig(name="test-world", width=500.0, height=500.0)
    world = WorldState(config=config)

    big_player = _make_player("big")
    mid_player = _make_player("mid")
    small_player = _make_player("small")

    big_cell = world.add_player(big_player)
    mid_cell = world.add_player(mid_player)
    small_cell = world.add_player(small_player)

    cells_with_radii = (
        (big_cell, 60.0),
        (mid_cell, 40.0),
        (small_cell, 20.0),
    )
    for cell, radius in cells_with_radii:
        cell.position = (100.0, 100.0)
        cell.radius = radius

    big_area = big_cell.area()
    mid_area = mid_cell.area()
    small_area = small_cell.area()

    world._handle_cell_collisions()

    assert set(world.cells.keys()) == {big_player.id}
    assert set(world.players.keys()) == {big_player.id}

    resulting_cell = world.cells[big_player.id]
    expected_area = big_area + 0.8 * mid_area + 0.8 * small_area

    assert math.isclose(resulting_cell.area(), expected_area, rel_tol=1e-6)


def test_overlapping_cells_absorb_with_small_size_advantage():
    config = WorldConfig(name="duel", width=400.0, height=400.0)
    world = WorldState(config=config)

    hunter = world.add_player(_make_player("hunter"))
    prey = world.add_player(_make_player("prey"))

    hunter.radius = 52.0
    prey.radius = 50.0
    hunter.position = (200.0, 200.0)
    prey.position = (200.0, 200.0)

    world._handle_cell_collisions()

    assert prey.id not in world.cells
    assert hunter.id in world.cells
    assert world.cells[hunter.id].radius > 52.0


def test_update_config_prunes_food(tmp_path):
    async def run_scenario() -> None:
        repo = WorldSnapshotRepository(str(tmp_path))
        manager = WorldManager(repo)

        world_info = await manager.create_world("prune-test")
        world_id = world_info["id"]
        ctx = manager._worlds[world_id]
        state = ctx.state

        assert len(state.foods) == state.config.food_count
        assert state.config.food_count > 5

        if ctx.task:
            ctx.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.task
            ctx.task = None

        await manager.update_config({"food_count": 5})

        assert state.config.food_count == 5
        assert len(state.foods) == 5

    asyncio.run(run_scenario())
