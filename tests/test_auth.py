from fastapi.testclient import TestClient


def test_user_registration_and_login(client: TestClient):
    response = client.post(
        "/api/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
            "full_name": "Alice",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "alice"

    token_response = client.post(
        "/api/token",
        data={"username": "alice", "password": "secret"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert "access_token" in token_data

    me_response = client.get(
        "/api/users/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "alice"
