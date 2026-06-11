"""
Tests for SessionManager (src/automation/session.py).
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.session import SessionManager


@pytest.fixture
def session_path(tmp_path) -> Path:
    return tmp_path / "session.json"


@pytest.mark.unit
class TestLoadSave:
    def test_load_missing_file_starts_empty(self, session_path):
        mgr = SessionManager(session_path)
        assert mgr.session_data == {}

    def test_save_and_reload(self, session_path):
        mgr = SessionManager(session_path)
        mgr.session_data = {"cookies": [{"name": "li_at"}]}
        mgr.save_session()

        assert session_path.exists()
        reloaded = SessionManager(session_path)
        assert reloaded.session_data["cookies"] == [{"name": "li_at"}]

    def test_load_corrupted_json_resets(self, session_path):
        session_path.write_text("{not valid json")
        mgr = SessionManager(session_path)
        assert mgr.session_data == {}


@pytest.mark.unit
class TestSessionValidity:
    def test_invalid_without_cookies(self, session_path):
        mgr = SessionManager(session_path)
        assert mgr.is_session_valid() is False

    def test_invalid_without_last_login(self, session_path):
        mgr = SessionManager(session_path)
        mgr.session_data = {"cookies": [{"name": "li_at"}]}
        assert mgr.is_session_valid() is False

    def test_valid_recent_login(self, session_path):
        mgr = SessionManager(session_path)
        mgr.session_data = {
            "cookies": [{"name": "li_at"}],
            "last_login": datetime.now().isoformat(),
        }
        assert mgr.is_session_valid() is True

    def test_invalid_expired_login(self, session_path):
        mgr = SessionManager(session_path)
        mgr.session_data = {
            "cookies": [{"name": "li_at"}],
            "last_login": (datetime.now() - timedelta(hours=21)).isoformat(),
        }
        assert mgr.is_session_valid() is False

    def test_invalid_bad_timestamp(self, session_path):
        mgr = SessionManager(session_path)
        mgr.session_data = {"cookies": [{"name": "li_at"}], "last_login": "not-a-date"}
        assert mgr.is_session_valid() is False


@pytest.mark.unit
class TestUpdateClear:
    def test_update_session_persists(self, session_path):
        mgr = SessionManager(session_path)
        mgr.update_session([{"name": "li_at"}], {"name": "Jane"})

        assert mgr.session_data["cookies"] == [{"name": "li_at"}]
        assert mgr.get_user_info() == {"name": "Jane"}
        assert "last_login" in mgr.session_data
        # Persisted to disk
        on_disk = json.loads(session_path.read_text())
        assert on_disk["user_info"] == {"name": "Jane"}

    def test_clear_session_removes_file(self, session_path):
        mgr = SessionManager(session_path)
        mgr.update_session([{"name": "li_at"}])
        assert session_path.exists()

        mgr.clear_session()
        assert mgr.session_data == {}
        assert not session_path.exists()

    def test_clear_session_no_file(self, session_path):
        mgr = SessionManager(session_path)
        mgr.clear_session()  # should not raise
        assert mgr.session_data == {}


@pytest.mark.unit
class TestCampaignState:
    def test_set_and_get_campaign_state(self, session_path):
        mgr = SessionManager(session_path)
        mgr.set_campaign_state(7, {"last_index": 42})

        state = mgr.get_campaign_state(7)
        assert state["last_index"] == 42
        assert "updated_at" in state

    def test_get_missing_campaign_state(self, session_path):
        mgr = SessionManager(session_path)
        assert mgr.get_campaign_state(999) == {}
