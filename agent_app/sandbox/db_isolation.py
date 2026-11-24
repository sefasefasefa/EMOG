"""
Database Isolation Layer for Sandbox
Provides read-only replicas, transaction management, and safe query execution.
"""
import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Iterator
from dataclasses import dataclass
from enum import Enum
import threading


class TransactionMode(Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


@dataclass
class QueryResult:
    """Result of a database query"""
    columns: List[str]
    rows: List[List[Any]]
    rowcount: int
    execution_time_ms: float


class DatabaseIsolation:
    """
    Database isolation layer providing:
    - Read-only connections for SELECT queries
    - Transaction management with automatic rollback on error
    - Query validation and sanitization
    - Connection pooling
    """
    
    # Blocked SQL patterns for safety
    BLOCKED_PATTERNS = [
        r"DROP\s+(TABLE|DATABASE|INDEX|VIEW)",
        r"DELETE\s+FROM\s+\w+\s*;?\s*$",  # DELETE without WHERE
        r"TRUNCATE\s+TABLE",
        r"ALTER\s+TABLE",
        r"CREATE\s+(TABLE|DATABASE|INDEX|VIEW)",
        r"GRANT\s+",
        r"REVOKE\s+",
        r"COMMIT",
        r"ROLLBACK",
        r"SAVEPOINT",
        r"PRAGMA\s+",
        r"VACUUM",
        r"ATTACH\s+DATABASE",
        r"DETACH\s+DATABASE",
    ]
    
    # Allowed read-only patterns
    READ_ONLY_PATTERNS = [
        r"^SELECT\s+",
        r"^WITH\s+.*SELECT\s+",
        r"^EXPLAIN\s+(QUERY\s+PLAN\s+)?SELECT\s+",
    ]
    
    def __init__(self, db_path: str = None, read_only_path: str = None):
        self.db_path = db_path or os.environ.get("DATABASE_PATH", "db.sqlite3")
        self.read_only_path = read_only_path or self.db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        
        # Initialize read-only connection
        self._init_readonly_db()
    
    def _init_readonly_db(self):
        """Initialize read-only database (can be a copy or the same with URI)"""
        # For SQLite, we can use URI with mode=ro
        self._readonly_uri = f"file:{self.read_only_path}?mode=ro&cache=shared"
    
    def _get_readonly_connection(self) -> sqlite3.Connection:
        """Get a read-only connection"""
        conn = sqlite3.connect(self._readonly_uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enforce read-only at connection level
        conn.execute("PRAGMA query_only = ON")
        return conn
    
    def _get_readwrite_connection(self) -> sqlite3.Connection:
        """Get a read-write connection"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def validate_query(self, query: str, allow_write: bool = False) -> bool:
        """Validate SQL query for safety"""
        import re
        query_upper = query.strip().upper()
        
        # Check blocked patterns
        for pattern in self.BLOCKED_PATTERNS:
            if re.search(pattern, query_upper, re.IGNORECASE):
                if not allow_write or pattern not in ["COMMIT", "ROLLBACK"]:
                    return False
        
        if not allow_write:
            # Must match read-only patterns
            allowed = False
            for pattern in self.READ_ONLY_PATTERNS:
                if re.search(pattern, query_upper, re.IGNORECASE):
                    allowed = True
                    break
            if not allowed:
                return False
        
        return True
    
    def execute_readonly(self, query: str, params: tuple = ()) -> QueryResult:
        """Execute a read-only query"""
        if not self.validate_query(query, allow_write=False):
            raise ValueError(f"Query not allowed in read-only mode: {query[:100]}")
        
        import time
        start = time.time()
        
        conn = self._get_readonly_connection()
        try:
            cursor = conn.execute(query, params)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [list(row) for row in cursor.fetchall()]
            rowcount = len(rows)
            return QueryResult(
                columns=columns,
                rows=rows,
                rowcount=rowcount,
                execution_time_ms=(time.time() - start) * 1000
            )
        finally:
            conn.close()
    
    @contextmanager
    def transaction(self, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        """Context manager for database transactions"""
        conn = self._get_readonly_connection() if read_only else self._get_readwrite_connection()
        try:
            if not read_only:
                conn.execute("BEGIN")
            yield conn
            if not read_only:
                conn.commit()
        except Exception as e:
            if not read_only:
                conn.rollback()
            raise
        finally:
            conn.close()
    
    def execute_in_transaction(self, queries: List[tuple], read_only: bool = False) -> List[QueryResult]:
        """Execute multiple queries in a single transaction"""
        results = []
        with self.transaction(read_only=read_only) as conn:
            for query, params in queries:
                if not self.validate_query(query, allow_write=not read_only):
                    raise ValueError(f"Query not allowed: {query[:100]}")
                cursor = conn.execute(query, params)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = [list(row) for row in cursor.fetchall()]
                results.append(QueryResult(
                    columns=columns,
                    rows=rows,
                    rowcount=len(rows),
                    execution_time_ms=0
                ))
        return results
    
    def get_schema(self) -> Dict[str, Any]:
        """Get database schema information (read-only)"""
        conn = self._get_readonly_connection()
        try:
            # Get tables
            tables = conn.execute("""
                SELECT name, sql FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """).fetchall()
            
            schema = {}
            for table in tables:
                name = table[0]
                # Get columns
                cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
                schema[name] = {
                    "create_sql": table[1],
                    "columns": [
                        {"name": c[1], "type": c[2], "notnull": bool(c[3]), "pk": bool(c[5])}
                        for c in cols
                    ]
                }
            return schema
        finally:
            conn.close()
    
    def execute_safe(self, query: str, params: tuple = (), allow_write: bool = False) -> QueryResult:
        """Execute query with appropriate connection based on type"""
        if self.validate_query(query, allow_write=allow_write):
            if allow_write:
                with self.transaction(read_only=False) as conn:
                    cursor = conn.execute(query, params)
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    rows = [list(row) for row in cursor.fetchall()]
                    return QueryResult(
                        columns=columns,
                        rows=rows,
                        rowcount=len(rows),
                        execution_time_ms=0
                    )
            else:
                return self.execute_readonly(query, params)
        else:
            raise ValueError(f"Query validation failed: {query[:100]}")


# Global instance
_db_isolation: Optional[DatabaseIsolation] = None
_isolation_lock = threading.Lock()


def get_db_isolation(db_path: str = None) -> DatabaseIsolation:
    """Get or create global database isolation instance"""
    global _db_isolation
    with _isolation_lock:
        if _db_isolation is None:
            _db_isolation = DatabaseIsolation(db_path)
        return _db_isolation