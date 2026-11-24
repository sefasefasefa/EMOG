"""
Short-term Memory - Redis Session Buffer
Stores last N messages per conversation branch for ultra-low latency access.
"""
import json
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import redis


@dataclass
class ShortTermMessage:
    """A message in short-term memory"""
    node_id: str
    role: str
    content: str
    model: str
    tokens_used: int
    timestamp: str
    parent_id: Optional[str] = None


class ShortTermMemory:
    """
    Redis-backed short-term memory buffer.
    Maintains last N messages per conversation branch.
    """
    
    def __init__(self, redis_url: str = None, max_messages: int = 10):
        self.max_messages = max_messages
        redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.client = redis.from_url(redis_url, decode_responses=True)
    
    def _branch_key(self, conversation_id: str, branch_node_id: str) -> str:
        """Generate Redis key for a specific branch"""
        return f"emog:stm:{conversation_id}:{branch_node_id}"
    
    def _active_branch_key(self, conversation_id: str) -> str:
        """Key for tracking active branch"""
        return f"emog:stm:{conversation_id}:active"
    
    def push_message(self, conversation_id: str, branch_node_id: str, 
                     message: ShortTermMessage) -> None:
        """
        Add a message to the branch buffer.
        Maintains max_messages limit (FIFO).
        """
        key = self._branch_key(conversation_id, branch_node_id)
        msg_json = json.dumps(asdict(message))
        
        pipe = self.client.pipeline()
        pipe.lpush(key, msg_json)
        pipe.ltrim(key, 0, self.max_messages - 1)
        pipe.expire(key, 86400 * 7)  # 7 day TTL
        pipe.execute()
        
        # Update active branch tracker
        self.client.set(self._active_branch_key(conversation_id), branch_node_id)
    
    def get_messages(self, conversation_id: str, branch_node_id: str = None) -> List[ShortTermMessage]:
        """
        Get messages from branch buffer (chronological order: oldest first).
        If branch_node_id not provided, uses active branch.
        """
        if branch_node_id is None:
            branch_node_id = self.client.get(self._active_branch_key(conversation_id))
            if not branch_node_id:
                return []
        
        key = self._branch_key(conversation_id, branch_node_id)
        messages_json = self.client.lrange(key, 0, -1)
        
        # Reverse to get chronological order (oldest first)
        messages = []
        for msg_json in reversed(messages_json):
            try:
                data = json.loads(msg_json)
                messages.append(ShortTermMessage(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        
        return messages
    
    def set_active_branch(self, conversation_id: str, branch_node_id: str) -> None:
        """Set the active branch for a conversation"""
        self.client.set(self._active_branch_key(conversation_id), branch_node_id)
        
        # Pre-populate new branch buffer with last N messages from DB
        # (This would be called from the view when switching branches)
    
    def clear_branch(self, conversation_id: str, branch_node_id: str) -> None:
        """Clear a specific branch buffer"""
        key = self._branch_key(conversation_id, branch_node_id)
        self.client.delete(key)
    
    def clear_conversation(self, conversation_id: str) -> None:
        """Clear all branches for a conversation"""
        pattern = f"emog:stm:{conversation_id}:*"
        keys = self.client.keys(pattern)
        if keys:
            self.client.delete(*keys)
    
    def get_branch_info(self, conversation_id: str) -> Dict[str, Any]:
        """Get info about all branches for a conversation"""
        pattern = f"emog:stm:{conversation_id}:*"
        keys = self.client.keys(pattern)
        
        branches = {}
        for key in keys:
            if key.endswith(":active"):
                continue
            # Extract branch node ID from key
            parts = key.split(":")
            if len(parts) >= 4:
                branch_id = parts[-1]
                length = self.client.llen(key)
                branches[branch_id] = {
                    "message_count": length,
                    "key": key
                }
        
        active = self.client.get(self._active_branch_key(conversation_id))
        return {
            "active_branch": active,
            "branches": branches
        }


# Global instance
_short_term_memory: Optional[ShortTermMemory] = None


def get_short_term_memory() -> ShortTermMemory:
    """Get or create global short-term memory instance"""
    global _short_term_memory
    if _short_term_memory is None:
        _short_term_memory = ShortTermMemory()
    return _short_term_memory