"""
Git-like Conversation History with Time Travel & Rollback
Implements version-controlled conversation history with branching and rollback capabilities.
"""
import os
import json
import sqlite3
import threading
import uuid
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from contextlib import contextmanager
from collections import defaultdict


class NodeType(Enum):
    """Types of conversation nodes"""
    ROOT = "root"
    USER_MESSAGE = "user_message"
    ASSISTANT_RESPONSE = "assistant_response"
    SYSTEM_MESSAGE = "system_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass
class ConversationNode:
    """A node in the conversation tree (like a Git commit)"""
    id: str
    conversation_id: str
    parent_id: Optional[str]
    node_type: NodeType
    content: str
    role: str  # user, assistant, system, tool
    metadata: Dict[str, Any] = field(default_factory=dict)
    tokens_used: int = 0
    model: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    children: List[str] = field(default_factory=list)  # Child node IDs
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationNode':
        data = data.copy()
        data['node_type'] = NodeType(data['node_type'])
        return cls(**data)


@dataclass
class ConversationBranch:
    """A branch in the conversation tree (like a Git branch)"""
    id: str
    conversation_id: str
    name: str
    head_node_id: str  # Points to the latest node in this branch
    base_node_id: str  # Node where this branch diverged
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Conversation:
    """Root conversation container"""
    id: str
    title: str
    user_id: Optional[str] = None
    root_node_id: Optional[str] = None
    current_branch_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConversationTimeTravel:
    """
    Git-like version control for conversations.
    Supports branching, merging, and time-travel rollback.
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.environ.get("CONVERSATION_DB", "./conversations.db")
        self._lock = threading.Lock()
        self._init_db()
        self._init_default_conversation()
    
    def _init_db(self):
        """Initialize SQLite database with conversation schema"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        cursor = conn.cursor()
        
        # Conversations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                user_id TEXT,
                root_node_id TEXT,
                current_branch_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        
        # Nodes table (conversation messages as tree nodes)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                parent_id TEXT,
                node_type TEXT NOT NULL,
                content TEXT NOT NULL,
                role TEXT NOT NULL,
                metadata TEXT,
                tokens_used INTEGER DEFAULT 0,
                model TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                FOREIGN KEY (parent_id) REFERENCES nodes(id)
            )
        """)
        
        # Branches table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branches (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                name TEXT NOT NULL,
                head_node_id TEXT NOT NULL,
                base_node_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                FOREIGN KEY (head_node_id) REFERENCES nodes(id),
                FOREIGN KEY (base_node_id) REFERENCES nodes(id)
            )
        """)
        
        # Branch history for time-travel
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS branch_history (
                id TEXT PRIMARY KEY,
                branch_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                action TEXT NOT NULL,  -- 'commit', 'checkout', 'merge', 'rollback'
                timestamp TEXT NOT NULL,
                metadata TEXT,
                FOREIGN KEY (branch_id) REFERENCES branches(id),
                FOREIGN KEY (node_id) REFERENCES nodes(id)
            )
        """)
        
        # Indexes for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_conversation ON nodes(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_branches_conversation ON branches(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_branch_history_branch ON branch_history(branch_id)")
        
        conn.commit()
        conn.close()
    
    def _init_default_conversation(self):
        """Create default conversation if none exists"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM conversations")
        count = cursor.fetchone()[0]
        
        if count == 0:
            # Create default conversation
            conv_id = str(uuid.uuid4())
            root_id = str(uuid.uuid4())
            branch_id = str(uuid.uuid4())
            
            now = datetime.utcnow().isoformat()
            
            # Create conversation
            conn.execute("""
                INSERT INTO conversations (id, title, root_node_id, current_branch_id, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (conv_id, "Default Conversation", root_id, branch_id, now, now, "{}"))
            
            # Create root node
            conn.execute("""
                INSERT INTO nodes (id, conversation_id, parent_id, node_type, content, role, metadata, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (root_id, conv_id, None, "root", "Welcome to the conversation", "system", "{}", now))
            
            # Create default branch
            conn.execute("""
                INSERT INTO branches (id, conversation_id, name, head_node_id, base_node_id, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (branch_id, conv_id, "main", root_id, root_id, now, now, "{}"))
            
            conn.commit()
        
        conn.close()
    
    def create_conversation(self, title: str, user_id: str = None, metadata: Dict = None) -> Conversation:
        """Create a new conversation with root node and main branch"""
        conv_id = str(uuid.uuid4())
        root_id = str(uuid.uuid4())
        branch_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO conversations (id, title, user_id, root_node_id, current_branch_id, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (conv_id, title, user_id, root_id, branch_id, now, now, json.dumps(metadata or {})))
        
        # Create root node
        root_node = ConversationNode(
            id=root_id,
            conversation_id=conv_id,
            parent_id=None,
            node_type=NodeType.ROOT,
            content="Welcome to the conversation",
            role="system",
            timestamp=now,
        )
        cursor.execute("""
            INSERT INTO nodes (id, conversation_id, parent_id, node_type, content, role, metadata, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (root_id, conv_id, None, "root", "Welcome to the conversation", "system", "{}", now))
        
        # Create main branch
        branch = ConversationBranch(
            id=branch_id,
            conversation_id=conv_id,
            name="main",
            head_node_id=root_id,
            base_node_id=root_id,
            created_at=now,
            updated_at=now,
        )
        cursor.execute("""
            INSERT INTO branches (id, conversation_id, name, head_node_id, base_node_id, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (branch_id, conv_id, "main", root_id, root_id, now, now, "{}"))
        
        conn.commit()
        conn.close()
        
        return Conversation(
            id=conv_id,
            title=title,
            user_id=user_id,
            root_node_id=root_id,
            current_branch_id=branch_id,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
    
    def add_message(self, 
                    conversation_id: str,
                    content: str,
                    role: str,
                    parent_node_id: str = None,
                    node_type: NodeType = NodeType.USER_MESSAGE,
                    model: str = None,
                    tokens_used: int = 0,
                    metadata: Dict = None) -> ConversationNode:
        """Add a message as a node in the conversation tree"""
        if parent_node_id is None:
            # Get current branch head
            parent_node_id = self.get_branch_head(conversation_id)
        
        node_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        node = ConversationNode(
            id=node_id,
            conversation_id=conversation_id,
            parent_id=parent_node_id,
            node_type=node_type,
            content=content,
            role=role,
            metadata=metadata or {},
            tokens_used=tokens_used,
            model=model,
            timestamp=now,
        )
        
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO nodes (id, conversation_id, parent_id, node_type, content, role, metadata, tokens_used, model, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (node_id, conversation_id, parent_node_id, node_type.value, content, role,
              json.dumps(metadata or {}), tokens_used, model, now))
        
        # Update parent's children list
        cursor.execute("""
            UPDATE nodes SET children = COALESCE(children, '[]') || ? WHERE id = ?
        """, (json.dumps([node_id]), parent_node_id))
        
        # Update branch head
        current_branch = self.get_current_branch(conversation_id)
        if current_branch:
            cursor.execute("""
                UPDATE branches SET head_node_id = ?, updated_at = ? WHERE id = ?
            """, (node_id, now, current_branch.id))
        
        conn.commit()
        conn.close()
        
        return node
    
    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get conversation by ID"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return Conversation(
            id=row[0], title=row[1], user_id=row[2], root_node_id=row[3],
            current_branch_id=row[4], created_at=row[5], updated_at=row[6],
            metadata=json.loads(row[7]) if row[7] else {}
        )
    
    def get_node(self, node_id: str) -> Optional[ConversationNode]:
        """Get a node by ID"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return ConversationNode(
            id=row[0], conversation_id=row[1], parent_id=row[2],
            node_type=NodeType(row[3]), content=row[4], role=row[5],
            metadata=json.loads(row[6]) if row[6] else {},
            tokens_used=row[7], model=row[8], timestamp=row[9],
        )
    
    def get_branch(self, branch_id: str) -> Optional[ConversationBranch]:
        """Get branch by ID"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM branches WHERE id = ?", (branch_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return ConversationBranch(
            id=row[0], conversation_id=row[1], name=row[2],
            head_node_id=row[3], base_node_id=row[4],
            created_at=row[5], updated_at=row[6],
            metadata=json.loads(row[7]) if row[7] else {}
        )
    
    def get_current_branch(self, conversation_id: str) -> Optional[ConversationBranch]:
        """Get the current active branch for a conversation"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT current_branch_id FROM conversations WHERE id = ?", (conversation_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row or not row[0]:
            return None
        return self.get_branch(row[0])
    
    def get_branch_head(self, conversation_id: str) -> Optional[str]:
        """Get the head node ID of the current branch"""
        branch = self.get_current_branch(conversation_id)
        return branch.head_node_id if branch else None
    
    def get_path_to_root(self, node_id: str) -> List[ConversationNode]:
        """Get path from node to root (for context reconstruction)"""
        path = []
        current_id = node_id
        
        while current_id:
            node = self.get_node(current_id)
            if not node:
                break
            path.append(node)
            current_id = node.parent_id
        
        return list(reversed(path))
    
    def get_conversation_history(self, conversation_id: str, branch_name: str = None) -> List[ConversationNode]:
        """Get linear history for a branch (for context)"""
        if branch_name:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM branches WHERE conversation_id = ? AND name = ?", (conversation_id, branch_name))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return []
            branch_id = row[0]
        else:
            branch = self.get_current_branch(conversation_id)
            if not branch:
                return []
            branch_id = branch.id
        
        branch = self.get_branch(branch_id)
        if not branch:
            return []
        
        # Get path from root to head
        path = self.get_path_to_root(branch.head_node_id)
        return path
    
    def create_branch(self, 
                      conversation_id: str, 
                      branch_name: str, 
                      base_node_id: str = None,
                      metadata: Dict = None) -> ConversationBranch:
        """Create a new branch from a specific node (like git checkout -b)"""
        if base_node_id is None:
            base_node_id = self.get_branch_head(conversation_id)
        
        branch_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        
        branch = ConversationBranch(
            id=branch_id,
            conversation_id=conversation_id,
            name=branch_name,
            head_node_id=base_node_id,
            base_node_id=base_node_id,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO branches (id, conversation_id, name, head_node_id, base_node_id, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (branch_id, conversation_id, branch_name, base_node_id, base_node_id, now, now, json.dumps(metadata or {})))
        
        # Log branch creation
        cursor.execute("""
            INSERT INTO branch_history (id, branch_id, node_id, action, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), branch_id, base_node_id, "branch_created", now, json.dumps({"base_node": base_node_id})))
        
        conn.commit()
        conn.close()
        
        return branch
    
    def checkout_branch(self, conversation_id: str, branch_id: str) -> bool:
        """Switch to a different branch (like git checkout)"""
        branch = self.get_branch(branch_id)
        if not branch or branch.conversation_id != conversation_id:
            return False
        
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat()
        cursor.execute("""
            UPDATE conversations SET current_branch_id = ?, updated_at = ? WHERE id = ?
        """, (branch_id, now, conversation_id))
        
        # Log checkout
        cursor.execute("""
            INSERT INTO branch_history (id, branch_id, node_id, action, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), branch_id, branch.head_node_id, "checkout", now, "{}"))
        
        conn.commit()
        conn.close()
        return True
    
    def merge_branch(self,
                     conversation_id: str,
                     source_branch_id: str,
                     target_branch_id: str,
                     merge_strategy: str = "fast_forward") -> Tuple[bool, str]:
        """
        Merge source branch into target branch.
        Returns (success, message)
        """
        source = self.get_branch(source_branch_id)
        target = self.get_branch(target_branch_id)
        
        if not source or not target:
            return False, "Branch not found"
        
        if source.conversation_id != conversation_id or target.conversation_id != conversation_id:
            return False, "Branches must belong to same conversation"
        
        # Find common ancestor
        source_path = set(self.get_path_to_root(source.head_node_id))
        target_path = []
        current = target.head_node_id
        while current:
            target_path.append(current)
            current = self.get_node(current).parent_id if self.get_node(current) else None
        
        common_ancestor = None
        for node_id in target_path:
            if node_id in source_path:
                common_ancestor = node_id
                break
        
        if not common_ancestor:
            return False, "No common ancestor found"
        
        # Simple fast-forward if target is ancestor of source
        if common_ancestor == target.head_node_id:
            # Fast-forward merge
            self.checkout_branch(conversation_id, source_branch_id)
            # Update target branch to point to source head
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            cursor.execute("""
                UPDATE branches SET head_node_id = ?, updated_at = ? WHERE id = ?
            """, (source.head_node_id, now, target_branch_id))
            
            # Log merge
            cursor.execute("""
                INSERT INTO branch_history (id, branch_id, node_id, action, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (str(uuid.uuid4()), target_branch_id, source.head_node_id, "merge_fast_forward", now, 
                  json.dumps({"source_branch": source_branch_id, "strategy": "fast_forward"})))
            conn.commit()
            conn.close()
            return True, "Fast-forward merge completed"
        
        # For true merge, would need conflict resolution - simplified for now
        # In real implementation, would create merge commit
        return False, "True merge not implemented - use fast-forward or manual merge"
    
    def rollback_to_node(self, conversation_id: str, branch_id: str, target_node_id: str) -> bool:
        """
        Rollback branch to a specific node (like git reset --hard).
        This moves the branch head to the target node.
        """
        branch = self.get_branch(branch_id)
        if not branch or branch.conversation_id != conversation_id:
            return False
        
        # Verify target node is in branch history
        path = self.get_path_to_root(branch.head_node_id)
        path_ids = [n.id for n in path]
        
        if target_node_id not in path_ids:
            return False
        
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat()
        cursor.execute("""
            UPDATE branches SET head_node_id = ?, updated_at = ? WHERE id = ?
        """, (target_node_id, now, branch_id))
        
        # Log rollback
        cursor.execute("""
            INSERT INTO branch_history (id, branch_id, node_id, action, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), branch_id, target_node_id, "rollback", now, 
              json.dumps({"from_node": branch.head_node_id, "to_node": target_node_id})))
        
        conn.commit()
        conn.close()
        return True
    
    def get_branch_history(self, branch_id: str, limit: int = 50) -> List[Dict]:
        """Get history of actions on a branch"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, branch_id, node_id, action, timestamp, metadata
            FROM branch_history
            WHERE branch_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (branch_id, limit))
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "id": row[0], "branch_id": row[1], "node_id": row[2],
                "action": row[3], "timestamp": row[4],
                "metadata": json.loads(row[5]) if row[5] else {}
            }
            for row in rows
        ]


# Global instance
_conversation_time_travel: Optional[ConversationTimeTravel] = None
_time_travel_lock = threading.Lock()


def get_conversation_time_travel(db_path: str = None) -> ConversationTimeTravel:
    """Get or create global conversation time travel instance"""
    global _conversation_time_travel
    with _time_travel_lock:
        if _conversation_time_travel is None:
            _conversation_time_travel = ConversationTimeTravel(db_path)
        return _conversation_time_travel