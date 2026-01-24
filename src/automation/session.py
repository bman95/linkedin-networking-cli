import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import sys

sys.path.append(str(Path(__file__).parent.parent))

from utils.logging import get_logger

logger = get_logger(__name__)


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
                logger.info(f"Session loaded successfully from {self.session_path}")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load session from {self.session_path}: {e}")
                self.session_data = {}
        else:
            self.session_data = {}

    def save_session(self) -> None:
        """Save session data to file"""
        try:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_path, 'w') as f:
                json.dump(self.session_data, f, indent=2)
            logger.debug(f"Session saved successfully to {self.session_path}")
        except IOError as e:
            logger.error(f"Failed to save session to {self.session_path}: {e}")

    def is_session_valid(self) -> bool:
        """Check if current session is valid"""
        if not self.session_data.get('cookies'):
            logger.debug("Session invalid: No cookies found")
            return False

        # Check session age (LinkedIn sessions typically last 24 hours)
        last_login = self.session_data.get('last_login')
        if not last_login:
            logger.debug("Session invalid: No last_login timestamp")
            return False

        try:
            last_login_time = datetime.fromisoformat(last_login)
            time_since_login = datetime.now() - last_login_time
            if time_since_login > timedelta(hours=20):  # Refresh before expiry
                logger.info(f"Session expired: Last login was {time_since_login.total_seconds() / 3600:.1f} hours ago")
                return False
        except ValueError as e:
            logger.warning(f"Session invalid: Could not parse last_login timestamp: {e}")
            return False

        logger.debug("Session is valid")
        return True

    def update_session(self, cookies: list, user_info: Dict[str, Any] = None) -> None:
        """Update session with new authentication data"""
        self.session_data.update({
            'cookies': cookies,
            'last_login': datetime.now().isoformat(),
            'user_info': user_info or {}
        })
        logger.info(f"Session updated with {len(cookies)} cookies")
        self.save_session()

    def clear_session(self) -> None:
        """Clear session data"""
        self.session_data = {}
        if self.session_path.exists():
            self.session_path.unlink()
            logger.info(f"Session cleared and file deleted: {self.session_path}")
        else:
            logger.info("Session cleared (no session file existed)")

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
        logger.debug(f"Campaign state updated for campaign_id={campaign_id}")
        self.save_session()

    def get_campaign_state(self, campaign_id: int) -> Dict[str, Any]:
        """Get campaign execution state"""
        campaigns = self.session_data.get('campaigns', {})
        state = campaigns.get(str(campaign_id), {})
        if state:
            logger.debug(f"Retrieved campaign state for campaign_id={campaign_id}")
        else:
            logger.debug(f"No campaign state found for campaign_id={campaign_id}")
        return state