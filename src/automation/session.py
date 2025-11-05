import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta


class SessionManager:
    """Manages LinkedIn session persistence and authentication state"""

    def __init__(self, session_path: Path):
        self.session_path = session_path
        self.session_data: Dict[str, Any] = {}
        self.load_session()

    def load_session(self) -> None:
        """Load session data from file"""
        if self.session_path.exists():
            try:
                with open(self.session_path, 'r') as f:
                    self.session_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Failed to load session: {e}")
                self.session_data = {}
        else:
            self.session_data = {}

    def save_session(self) -> None:
        """Save session data to file"""
        try:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_path, 'w') as f:
                json.dump(self.session_data, f, indent=2)
        except IOError as e:
            print(f"Failed to save session: {e}")

    def is_session_valid(self) -> bool:
        """Check if current session is valid"""
        if not self.session_data.get('cookies'):
            return False

        # Check session age (LinkedIn sessions typically last 24 hours)
        last_login = self.session_data.get('last_login')
        if not last_login:
            return False

        try:
            last_login_time = datetime.fromisoformat(last_login)
            if datetime.now() - last_login_time > timedelta(hours=20):  # Refresh before expiry
                return False
        except ValueError:
            return False

        return True

    def update_session(self, cookies: list, user_info: Dict[str, Any] = None) -> None:
        """Update session with new authentication data"""
        self.session_data.update({
            'cookies': cookies,
            'last_login': datetime.now().isoformat(),
            'user_info': user_info or {}
        })
        self.save_session()

    def clear_session(self) -> None:
        """Clear session data"""
        self.session_data = {}
        if self.session_path.exists():
            self.session_path.unlink()

    def get_user_info(self) -> Dict[str, Any]:
        """Get stored user information"""
        return self.session_data.get('user_info', {})

    def set_campaign_state(self, campaign_id: int, state: Dict[str, Any]) -> None:
        """Store campaign execution state"""
        if 'campaigns' not in self.session_data:
            self.session_data['campaigns'] = {}

        self.session_data['campaigns'][str(campaign_id)] = {
            **state,
            'updated_at': datetime.now().isoformat()
        }
        self.save_session()

    def get_campaign_state(self, campaign_id: int) -> Dict[str, Any]:
        """Get campaign execution state"""
        campaigns = self.session_data.get('campaigns', {})
        return campaigns.get(str(campaign_id), {})