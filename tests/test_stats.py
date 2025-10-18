from fastapi.testclient import TestClient


def authenticate(client: TestClient) -> str:
    client.post(
        "/api/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "secret",
            "full_name": "Bob",
        },
    )
    response = client.post(
        "/api/token",
        data={"username": "bob", "password": "secret"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    return response.json()["access_token"]


def test_update_and_retrieve_stats(client: TestClient):
    token = authenticate(client)
    update_response = client.put(
        "/api/stats/me",
        json={
            "cells_eaten": 10,
            "food_eaten": 5,
            "worlds_explored": 2,
            "sessions_played": 3,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["cells_eaten"] == 10

    me_stats = client.get(
        "/api/stats/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_stats.status_code == 200
    assert me_stats.json()["food_eaten"] == 5

    aggregate = client.get(
        "/api/stats/aggregate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert aggregate.status_code == 200
    totals = aggregate.json()
    assert totals["cells_eaten"] == 10
    assert totals["worlds_explored"] == 2
