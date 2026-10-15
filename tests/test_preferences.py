"""
test_preferences.py — Unit Tests for User Preferences Feature
=============================================================
Tests the User Preferences endpoints (GET /preferences, PUT /preferences)
validating default fallback, database updates, Pydantic validation, and auth.
"""
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.models.db_preference import UserPreference


# ---------------------------------------------------------------------------
# 1. GET /preferences (Default Fallback & Existing Retrieval)
# ---------------------------------------------------------------------------

@patch("app.routers.preferences.get_user_preference", new_callable=AsyncMock)
def test_get_preferences_default(mock_get_pref, client):
    """
    When no preference record exists in DB for the user,
    returns HTTP 200 with default theme 'dark'.
    """
    mock_get_pref.return_value = None

    response = client.get("/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "dark"
    assert "user_id" in data
    assert "updated_at" in data
    mock_get_pref.assert_awaited_once()


@patch("app.routers.preferences.get_user_preference", new_callable=AsyncMock)
def test_get_preferences_existing(mock_get_pref, client):
    """
    When a preference record exists in DB, returns HTTP 200 with saved theme.
    """
    now = datetime.now(timezone.utc)
    mock_get_pref.return_value = UserPreference(
        user_id="00000000-0000-0000-0000-000000000001",
        theme="light",
        updated_at=now,
    )

    response = client.get("/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "light"
    assert data["user_id"] == "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# 2. PUT /preferences (Update Theme & Input Validation)
# ---------------------------------------------------------------------------

@patch("app.routers.preferences.upsert_user_preference", new_callable=AsyncMock)
def test_update_preferences_light(mock_upsert_pref, client):
    """
    Updating theme to 'light' returns HTTP 200 with updated theme.
    """
    now = datetime.now(timezone.utc)
    mock_upsert_pref.return_value = UserPreference(
        user_id="00000000-0000-0000-0000-000000000001",
        theme="light",
        updated_at=now,
    )

    response = client.put("/preferences", json={"theme": "light"})
    assert response.status_code == 200
    assert response.json()["theme"] == "light"

    # =========================================================================
    # WHAT IS `assert_awaited_once_with(...)` AND `call_args[0][0]`?
    # =========================================================================
    #
    # 1. WHAT `assert_awaited_once_with(...)` DOES:
    #    - Verifies that `await upsert_user_preference(...)` was called EXACTLY ONCE.
    #    - Verifies that the parameters passed into it match the expected arguments.
    #
    # 2. WHAT IS `mock.call_args` IN PYTHON?
    #    Whenever a mock function is called, `unittest.mock` records the call as a tuple:
    #        mock.call_args = (positional_args_tuple, keyword_args_dict)
    #
    #    Index [0] = tuple of positional arguments: (arg0, arg1, arg2, ...)
    #    Index [1] = dictionary of keyword arguments: {"key": value, ...}
    #
    # 3. WHY `mock_upsert_pref.call_args[0][0]`?
    #    Our router endpoint calls:
    #        await upsert_user_preference(db, current_user.object_id, body.theme.value)
    #
    #    This passes 3 positional arguments:
    #        Argument 0: `db`               -> The database session (injected by FastAPI Depends(get_db))
    #        Argument 1: `user_id`          -> "00000000-0000-0000-0000-000000000001" (from mock_user)
    #        Argument 2: `theme`            -> "light" (from request JSON body)
    #
    #    Therefore:
    #        `call_args[0]`    = (db_session_instance, "00000000-0000-0000-0000-000000000001", "light")
    #        `call_args[0][0]` = db_session_instance (the 1st argument passed to the function)
    #
    #    By passing `mock_upsert_pref.call_args[0][0]` as the 1st parameter to
    #    `assert_awaited_once_with`, we match whatever DB session FastAPI injected,
    #    while strictly asserting that:
    #        - Argument 1 was the authenticated user's ID
    #        - Argument 2 was the theme string "light"
    # =========================================================================
    mock_upsert_pref.assert_awaited_once_with(
        mock_upsert_pref.call_args[0][0],
        "00000000-0000-0000-0000-000000000001",
        "light",
    )


@patch("app.routers.preferences.upsert_user_preference", new_callable=AsyncMock)
def test_update_preferences_dark(mock_upsert_pref, client):
    """
    Updating theme to 'dark' returns HTTP 200 with updated theme.
    """
    now = datetime.now(timezone.utc)
    mock_upsert_pref.return_value = UserPreference(
        user_id="00000000-0000-0000-0000-000000000001",
        theme="dark",
        updated_at=now,
    )

    response = client.put("/preferences", json={"theme": "dark"})
    assert response.status_code == 200
    assert response.json()["theme"] == "dark"

    # Verify upsert was awaited once with dark theme
    mock_upsert_pref.assert_awaited_once_with(
        mock_upsert_pref.call_args[0][0],
        "00000000-0000-0000-0000-000000000001",
        "dark",
    )


def test_update_preferences_invalid_theme(client):
    """
    Passing an unrecognized theme (e.g. 'blue') fails Pydantic schema validation
    with HTTP 422 Unprocessable Entity.
    """
    response = client.put("/preferences", json={"theme": "blue"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 3. UNAUTHENTICATED REQUEST (Security Boundary)
# ---------------------------------------------------------------------------

def test_preferences_unauthenticated():
    """
    Without an authenticated user (no Bearer token or override),
    endpoint returns HTTP 401 Unauthorized.
    """
    with TestClient(app) as unauth_client:
        response = unauth_client.get("/preferences")
        assert response.status_code == 401
