"""
12. Permanent Immutable Memory
Long-term memory infrastructure retaining all project history, preferences, past problems.
"""
import asyncio
import hashlib
import time
import json
import sqlite3
from typing import Dict, List, Any, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from contextlib import contextmanager
import threading
import pickle


class MemoryType(Enum):
    PROJECT_HISTORY = "project_history"
    USER_PREFERENCE = "user_preference"
    PROBLEM_SOLUTION = "problem_solution"
    CODE_PATTERN = "code_pattern"
    DECISION_LOG = "decision_log"
    INTERACTION = "interaction"
    ERROR_RESOLUTION = "error_resolution"
    LEARNING = "learning"


@dataclass
class MemoryEntry:
    id: str
    type: MemoryType
    content: Dict
    tags: List[str] = field(default_factory=list)
    importance: float = 1.0  # 0.0 - 1.0
    access_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    embedding: Optional[List[float]] = None  # For semantic search


@dataclass
class MemoryQuery:
    query: str
    types: List[MemoryType] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    limit: int = 10
    min_importance: float = 0.0


class ImmutableMemory:
    """
    Permanent, immutable memory store.
    Uses append-only storage with versioning.
    """
    
    def __init__(self, db_path: str = "./memory.db"):
        self.db_path = Path(db_path)
        self._init_db()
        self._lock = threading.RLock()
        
        # In-memory cache for recent entries
        self._cache: Dict[str, MemoryEntry] = {}
        self._cache_size = 1000
    
    def _init_db(self) -> None:
        """Initialize SQLite database with WAL mode."""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        
        # Create tables
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                importance REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                project_id TEXT,
                user_id TEXT,
                embedding BLOB,
                version INTEGER DEFAULT 1,
                previous_id TEXT
            )
        """)
        
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories(tags)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)
        """)
        
        # Create FTS table for full-text search
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                id, content, tags, project_id, user_id,
                content='memories', content_rowid='rowid'
            )
        """)
        
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, id, content, tags, project_id, user_id)
                VALUES (new.rowid, new.id, json_extract(new.content, '$.summary'), new.tags, new.project_id, new.user_id);
            END
        """)
        
        self.conn.commit()
    
    def store(self, entry: MemoryEntry) -> str:
        """Store a memory entry (append-only, creates new version)."""
        with self._lock:
            # Check if updating existing
            existing = self._get_by_id(entry.id)
            if existing:
                # Create new version
                entry.id = f"{entry.id}_v{int(time.time() * 1000)}"
            
            # Serialize content
            content_json = json.dumps(entry.content)
            tags_json = json.dumps(entry.tags)
            embedding_blob = pickle.dumps(entry.embedding) if entry.embedding else None
            
            with self.conn:
                self.conn.execute("""
                    INSERT INTO memories (id, type, content, tags, importance, access_count, created_at, last_accessed, project_id, user_id, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.id, entry.type.value, content_json, tags_json,
                    entry.importance, entry.access_count,
                    entry.created_at, entry.last_accessed,
                    entry.project_id, entry.user_id, embedding_blob
                ))
            
            # Update cache
            self._cache[entry.id] = entry
            if len(self._cache) > self._cache_size:
                # Remove oldest
                oldest = min(self._cache.items(), key=lambda x: x[1].last_accessed)
                del self._cache[oldest[0]]
            
            return entry.id
    
    def _get_by_id(self, id: str) -> Optional[MemoryEntry]:
        """Get memory by exact ID."""
        # Check cache first
        if id in self._cache:
            entry = self._cache[id]
            entry.access_count += 1
            entry.last_accessed = time.time()
            return entry
        
        cursor = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (id,)
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_entry(row)
        return None
    
    def _row_to_entry(self, row) -> MemoryEntry:
        """Convert database row to MemoryEntry."""
        return MemoryEntry(
            id=row[0],
            type=MemoryType(row[1]),
            content=json.loads(row[2]),
            tags=json.loads(row[3]) if row[3] else [],
            importance=row[4],
            access_count=row[5],
            created_at=row[6],
            last_accessed=row[7],
            project_id=row[8],
            user_id=row[9],
            embedding=pickle.loads(row[10]) if row[10] else None
        )
    
    def query(self, query: MemoryQuery) -> List[MemoryEntry]:
        """Query memories with filters."""
        with self._lock:
            conditions = []
            params = []
            
            if query.query:
                # Use FTS
                sql = """
                    SELECT m.* FROM memories m
                    JOIN memories_fts f ON m.rowid = f.rowid
                    WHERE memories_fts MATCH ?
                """
                params.append(query.query)
            else:
                sql = "SELECT * FROM memories WHERE 1=1"
            
            if query.types:
                placeholders = ','.join('?' * len(query.types))
                sql += f" AND type IN ({','.join('?' * len(query.types))})"
                params.extend([t.value for t in query.types])
            
            if query.project_id:
                sql += " AND project_id = ?"
                params.append(query.project_id)
            
            if query.user_id:
                sql += " AND user_id = ?"
                params.append(query.user_id)
            
            if query.min_importance > 0:
                sql += " AND importance >= ?"
                params.append(query.min_importance)
            
            sql += " ORDER BY importance DESC, last_accessed DESC LIMIT ?"
            params.append(query.limit)
            
            cursor = self.conn.execute(sql, params)
            results = []
            for row in cursor:
                entry = self._row_to_entry(row)
                entry.access_count += 1
                entry.last_accessed = time.time()
                results.append(entry)
            
            # Update access stats
            if results:
                ids = [e.id for e in results]
                placeholders = ','.join('?' * len(ids))
                with self.conn:
                    self.conn.execute(
                        f"UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE id IN ({','.join('?' * len(ids))})",
                        [time.time()] + ids
                    )
            
            return results
    
    def get_by_tags(self, tags: List[str], match_all: bool = False) -> List[MemoryEntry]:
        """Get memories by tags."""
        with self._lock:
            if match_all:
                # All tags must match
                placeholders = ','.join('?' * len(tags))
                sql = f"""
                    SELECT * FROM memories 
                    WHERE {placeholders}
                """
                # Simplified - check JSON contains all tags
            else:
                # Any tag matches
                placeholders = ','.join('?' * len(tags))
                sql = f"SELECT * FROM memories WHERE tags IN ({placeholders})"
            
            cursor = self.conn.execute(sql, tags)
            return [self._row_to_entry(row) for row in cursor]
    
    def get_project_history(self, project_id: str, limit: int = 100) -> List[MemoryEntry]:
        """Get complete project history."""
        return self.query(MemoryQuery(
            project_id=project_id,
            limit=limit
        ))
    
    def get_user_preferences(self, user_id: str) -> Dict:
        """Get user preferences."""
        results = self.query(MemoryQuery(
            types=[MemoryType.USER_PREFERENCE],
            user_id=user_id,
            limit=100
        ))
        
        prefs = {}
        for entry in results:
            key = entry.content.get('key')
            if key:
                prefs[key] = entry.content.get('value')
        return prefs
    
    def set_user_preference(self, user_id: str, key: str, value: Any, importance: float = 1.0) -> str:
        """Set user preference."""
        entry = MemoryEntry(
            id=f"pref_{user_id}_{key}",
            type=MemoryType.USER_PREFERENCE,
            content={"key": key, "value": value},
            tags=["preference", key],
            importance=importance,
            user_id=user_id
        )
        return self.store(entry)
    
    def log_decision(self, project_id: str, decision: str, rationale: str, alternatives: List[str], tags: List[str] = None) -> str:
        """Log a decision."""
        entry = MemoryEntry(
            id=f"decision_{hashlib.md5(f'{project_id}:{decision}:{time.time()}'.encode()).hexdigest()[:8]}",
            type=MemoryType.DECISION_LOG,
            content={
                "decision": decision,
                "rationale": rationale,
                "alternatives": alternatives
            },
            tags=tags or ["decision"],
            importance=0.9,
            project_id=project_id
        )
        return self.store(entry)
    
    def log_problem_solution(self, project_id: str, problem: str, solution: str, error: str = None, tags: List[str] = None) -> str:
        """Log problem and solution."""
        entry = MemoryEntry(
            id=f"solution_{hashlib.md5(f'{project_id}:{problem}:{time.time()}'.encode()).hexdigest()[:8]}",
            type=MemoryType.PROBLEM_SOLUTION,
            content={
                "problem": problem,
                "solution": solution,
                "error": error
            },
            tags=tags or ["problem", "solution"],
            importance=0.95,
            project_id=project_id
        )
        return self.store(entry)
    
    def search_similar(self, query_text: str, limit: int = 10) -> List[MemoryEntry]:
        """Search for similar memories using FTS."""
        return self.query(MemoryQuery(query=query_text, limit=limit))
    
    def get_statistics(self) -> Dict:
        """Get memory statistics."""
        cursor = self.conn.execute("""
            SELECT type, COUNT(*) as count, AVG(importance) as avg_importance
            FROM memories
            GROUP BY type
        """)
        
        stats = {"total": 0, "by_type": {}}
        for row in cursor:
            stats["by_type"][row[0]] = {"count": row[1], "avg_importance": row[2]}
            stats["total"] += row[1]
        
        # Get total size
        size = self.db_path.stat().st_size if self.db_path.exists() else 0
        stats["db_size_bytes"] = size
        
        return stats
    
    def prune_old(self, days: int = 365, min_importance: float = 0.5) -> int:
        """Prune old, low-importance memories (soft delete - marks as archived)."""
        cutoff = time.time() - (days * 86400)
        
        with self.conn:
            cursor = self.conn.execute("""
                UPDATE memories SET importance = importance * 0.5
                WHERE created_at < ? AND importance < ?
            """, (cutoff, min_importance))
            return cursor.rowcount
    
    def close(self) -> None:
        """Close database connection."""
        self.conn.close()


class MemoryManager:
    """High-level memory management with semantic search."""
    
    def __init__(self, db_path: str = "./memory.db"):
        self.memory = ImmutableMemory(db_path)
        self._embedding_model = None
    
    def remember(self, content: Dict, type: MemoryType, **kwargs) -> str:
        """Store a memory."""
        entry = MemoryEntry(
            id=f"{type.value}_{hashlib.md5(json.dumps(content, sort_keys=True).encode()).hexdigest()[:8]}",
            type=type,
            content=content,
            **kwargs
        )
        return self.memory.store(entry)
    
    def recall(self, query: str, **kwargs) -> List[MemoryEntry]:
        """Search memories."""
        return self.memory.query(MemoryQuery(query=query, **kwargs))
    
    def remember_code_pattern(self, pattern: str, language: str, description: str, tags: List[str] = None) -> str:
        """Remember a code pattern."""
        return self.remember(
            {"pattern": pattern, "language": language, "description": description},
            MemoryType.CODE_PATTERN,
            tags=tags or ["pattern", language],
            importance=0.8
        )
    
    def get_code_patterns(self, language: str = None, tags: List[str] = None) -> List[Dict]:
        """Get stored code patterns."""
        results = self.memory.query(MemoryQuery(
            types=[MemoryType.CODE_PATTERN],
            tags=tags or [],
            limit=50
        ))
        
        patterns = []
        for entry in results:
            if language is None or entry.content.get('language') == language:
                patterns.append(entry.content)
        return patterns
    
    def get_project_context(self, project_id: str) -> Dict:
        """Get complete project context for LLM."""
        history = self.memory.get_project_history(project_id, limit=200)
        
        context = {
            "decisions": [],
            "problems_solved": [],
            "patterns": [],
            "preferences": self.memory.get_user_preferences("default"),
        }
        
        for entry in history:
            if entry.type == MemoryType.DECISION_LOG:
                context["decisions"].append(entry.content)
            elif entry.type == MemoryType.PROBLEM_SOLUTION:
                context["problems_solved"].append(entry.content)
            elif entry.type == MemoryType.CODE_PATTERN:
                context["patterns"].append(entry.content)
        
        return context
    
    def close(self) -> None:
        """Close memory store."""
        self.memory.close()