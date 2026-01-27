from typing import Dict, Set
from fastapi import WebSocket


class WSManager:
    """WebSocket connection manager"""
    
    def __init__(self):
        self.active: Dict[int, Set[WebSocket]] = {}

    async def connect(self, user_id: int, ws: WebSocket):
        """Connect user to WebSocket"""
        await ws.accept()
        self.active.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: int, ws: WebSocket):
        """Disconnect user from WebSocket"""
        conns = self.active.get(user_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self.active.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict):
        """Send message to specific user"""
        for ws in list(self.active.get(user_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(user_id, ws)

    def get_connected_users(self) -> Set[int]:
        """Get list of connected user IDs"""
        return set(self.active.keys())


# Global WebSocket manager instance
ws_manager = WSManager()


