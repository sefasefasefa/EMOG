"""
9. Comprehensive DB & Object Management
Full relational DB management - schema, data manipulation, triggers, stored procedures.
"""
import asyncio
import time
from typing import Dict, List, Any, Optional, Any, Type, TypeVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from contextlib import asynccontextmanager
import asyncpg
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index, event, DDL
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.sql import text
import uuid

Base = declarative_base()

T = TypeVar('T', bound=Base)


class MigrationDirection(Enum):
    UP = "up"
    DOWN = "down"


@dataclass
class Migration:
    version: str
    name: str
    up_sql: str
    down_sql: str
    dependencies: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


@dataclass
class QueryResult:
    rows: List[Dict]
    row_count: int
    execution_time: float
    columns: List[str]


class DatabaseManager:
    """
    Comprehensive database manager.
    Handles schema, migrations, queries, triggers, stored procedures.
    """
    
    def __init__(self, database_url: str = "postgresql+asyncpg://user:pass@localhost/db"):
        self.database_url = database_url
        self.engine: Optional[AsyncEngine] = None
        self.session_factory: Optional[sessionmaker] = None
        self.migrations: List[Migration] = []
        self._migration_table = "schema_migrations"
    
    async def initialize(self) -> None:
        """Initialize database connection and migration table."""
        self.engine = create_async_engine(self.database_url, echo=False, pool_size=20)
        self.session_factory = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        
        # Create migration tracking table
        await self._create_migration_table()
    
    async def _create_migration_table(self) -> None:
        """Create migration tracking table."""
        async with self.engine.begin() as conn:
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self._migration_table} (
                    version VARCHAR(50) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    applied_at TIMESTAMP DEFAULT NOW(),
                    execution_time_ms INTEGER
                )
            """))
    
    async def close(self) -> None:
        """Close database connections."""
        if self.engine:
            await self.engine.dispose()
    
    @asynccontextmanager
    async def session(self) -> AsyncSession:
        """Get database session."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    
    # Schema Management
    async def create_all_tables(self) -> None:
        """Create all tables from SQLAlchemy models."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async def drop_all_tables(self) -> None:
        """Drop all tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    
    async def create_table(self, table_name: str, columns: List[Dict], indexes: List[Dict] = None) -> None:
        """Create a table with specified columns."""
        cols_sql = []
        for col in columns:
            col_def = f"{col['name']} {col['type']}"
            if col.get('primary_key'):
                col_def += " PRIMARY KEY"
            if col.get('nullable') is False:
                col_def += " NOT NULL"
            if 'default' in col:
                col_def += f" DEFAULT {col['default']}"
            if col.get('unique'):
                col_def += " UNIQUE"
            cols_sql.append(col_def)
        
        sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(cols_sql)})"
        
        async with self.engine.begin() as conn:
            await conn.execute(text(sql))
            
            # Create indexes
            if indexes:
                for idx in indexes:
                    idx_sql = f"CREATE INDEX IF NOT EXISTS {idx['name']} ON {table_name} ({idx['columns']})"
                    if idx.get('unique'):
                        idx_sql = idx_sql.replace("INDEX", "UNIQUE INDEX")
                    await conn.execute(text(idx_sql))
    
    async def drop_table(self, table_name: str, cascade: bool = False) -> None:
        """Drop a table."""
        sql = f"DROP TABLE IF EXISTS {table_name}"
        if cascade:
            sql += " CASCADE"
        async with self.engine.begin() as conn:
            await conn.execute(text(sql))
    
    # Migration Management
    def add_migration(self, migration: Migration) -> None:
        """Add a migration."""
        self.migrations.append(migration)
        # Sort by version
        self.migrations.sort(key=lambda m: m.version)
    
    async def run_migrations(self, direction: MigrationDirection = MigrationDirection.UP, target_version: str = None) -> List[str]:
        """Run pending migrations."""
        applied = []
        
        # Get applied migrations
        async with self.session() as session:
            result = await session.execute(text(f"SELECT version FROM {self._migration_table}"))
            applied_versions = {row[0] for row in result}
        
        pending = [m for m in self.migrations if m.version not in applied_versions]
        
        if direction == MigrationDirection.DOWN:
            pending.reverse()
        
        for migration in pending:
            if target_version and migration.version == target_version:
                break
            
            start = time.time()
            sql = migration.up_sql if direction == MigrationDirection.UP else migration.down_sql
            
            async with self.engine.begin() as conn:
                await conn.execute(text(sql))
                await conn.execute(text(f"""
                    INSERT INTO {self._migration_table} (version, name, execution_time_ms)
                    VALUES (:version, :name, :exec_time)
                    ON CONFLICT (version) DO UPDATE SET applied_at = NOW(), execution_time_ms = :exec_time
                """), {
                    "version": migration.version,
                    "name": migration.name,
                    "exec_time": int((time.time() - start) * 1000)
                })
            
            applied.append(migration.version)
        
        return applied
    
    async def get_migration_status(self) -> List[Dict]:
        """Get migration status."""
        async with self.session() as session:
            result = await session.execute(text(f"""
                SELECT version, name, applied_at, execution_time_ms
                FROM {self._migration_table}
                ORDER BY applied_at
            """))
            return [
                {"version": row[0], "name": row[1], "applied_at": row[2], "execution_time_ms": row[3]}
                for row in result
            ]
    
    # Query Execution
    async def execute(self, sql: str, params: Dict = None) -> QueryResult:
        """Execute raw SQL query."""
        start = time.time()
        async with self.session() as session:
            result = await session.execute(text(sql), params or {})
            rows = [dict(row._mapping) for row in result]
            return QueryResult(
                rows=rows,
                row_count=len(rows),
                execution_time=time.time() - start,
                columns=list(result.keys()) if result.keys() else []
            )
    
    async def fetch_one(self, sql: str, params: Dict = None) -> Optional[Dict]:
        """Fetch single row."""
        async with self.session() as session:
            result = await session.execute(text(sql), params or {})
            row = result.first()
            return dict(row._mapping) if row else None
    
    async def fetch_all(self, sql: str, params: Dict = None) -> List[Dict]:
        """Fetch all rows."""
        async with self.session() as session:
            result = await session.execute(text(sql), params or {})
            return [dict(row._mapping) for row in result]
    
    async def execute_many(self, sql: str, params_list: List[Dict]) -> int:
        """Execute multiple parameter sets."""
        async with self.session() as session:
            result = await session.execute(text(sql), params_list)
            return result.rowcount
    
    # ORM Operations
    async def create(self, model: Type[T], data: Dict) -> T:
        """Create new record."""
        async with self.session() as session:
            obj = model(**data)
            session.add(obj)
            await session.flush()
            await session.refresh(obj)
            return obj
    
    async def get(self, model: Type[T], id: Any) -> Optional[T]:
        """Get record by ID."""
        async with self.session() as session:
            return await session.get(model, id)
    
    async def update(self, model: Type[T], id: Any, data: Dict) -> Optional[T]:
        """Update record."""
        async with self.session() as session:
            obj = await session.get(model, id)
            if obj:
                for key, value in data.items():
                    setattr(obj, key, value)
                await session.flush()
                await session.refresh(obj)
            return obj
    
    async def delete(self, model: Type[T], id: Any) -> bool:
        """Delete record."""
        async with self.session() as session:
            obj = await session.get(model, id)
            if obj:
                await session.delete(obj)
                return True
            return False
    
    async def query(
        self,
        model: Type[T],
        filters: Dict = None,
        order_by: str = None,
        limit: int = None,
        offset: int = None
    ) -> List[T]:
        """Query records."""
        async with self.session() as session:
            stmt = sa.select(model)
            
            if filters:
                for key, value in filters.items():
                    if hasattr(model, key):
                        stmt = stmt.where(getattr(model, key) == value)
            
            if order_by:
                if order_by.startswith('-'):
                    stmt = stmt.order_by(sa.desc(getattr(model, order_by[1:])))
                else:
                    stmt = stmt.order_by(getattr(model, order_by))
            
            if limit:
                stmt = stmt.limit(limit)
            if offset:
                stmt = stmt.offset(offset)
            
            result = await session.execute(stmt)
            return list(result.scalars().all())
    
    # Triggers and Stored Procedures
    async def create_trigger(
        self,
        name: str,
        table: str,
        event: str,  # INSERT, UPDATE, DELETE
        timing: str,  # BEFORE, AFTER
        function_sql: str,
        condition: str = None
    ) -> None:
        """Create a database trigger."""
        condition_sql = f"WHEN ({condition})" if condition else ""
        sql = f"""
            CREATE OR REPLACE FUNCTION {name}_func()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            BEGIN
                {function_sql}
                RETURN NEW;
            END;
            $$;
            
            DROP TRIGGER IF EXISTS {name} ON {table};
            CREATE TRIGGER {name}
            {timing} {event} ON {table}
            FOR EACH ROW {condition_sql}
            EXECUTE FUNCTION {name}_func();
        """
        async with self.engine.begin() as conn:
            await conn.execute(text(sql))
    
    async def create_stored_procedure(
        self,
        name: str,
        params: List[Dict],  # [{name: str, type: str, mode: str}]
        return_type: str,
        body: str,
        language: str = "plpgsql"
    ) -> None:
        """Create stored procedure."""
        param_defs = ", ".join(f"{p['name']} {p['type']}" for p in params)
        sql = f"""
            CREATE OR REPLACE FUNCTION {name}({param_defs})
            RETURNS {return_type}
            LANGUAGE {language}
            AS $$
            {body}
            $$;
        """
        async with self.engine.begin() as conn:
            await conn.execute(text(sql))
    
    # Transaction Management
    @asynccontextmanager
    async def transaction(self, isolation_level: str = None):
        """Explicit transaction management."""
        async with self.session() as session:
            if isolation_level:
                await session.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation_level}"))
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    
    # Health and Monitoring
    async def health_check(self) -> Dict:
        """Check database health."""
        try:
            async with self.session() as session:
                await session.execute(text("SELECT 1"))
                return {"status": "healthy", "latency_ms": 0}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}
    
    async def get_table_stats(self, table_name: str) -> Dict:
        """Get table statistics."""
        async with self.session() as session:
            result = await session.execute(text(f"""
                SELECT 
                    relname as table_name,
                    n_live_tup as row_count,
                    n_dead_tup as dead_rows,
                    last_vacuum,
                    last_autovacuum,
                    last_analyze,
                    last_autoanalyze
                FROM pg_stat_user_tables
                WHERE relname = :table
            """), {"table": table_name})
            row = result.first()
            return dict(row._mapping) if row else {}
    
    async def get_slow_queries(self, limit: int = 10) -> List[Dict]:
        """Get slow queries from pg_stat_statements."""
        async with self.session() as session:
            try:
                result = await session.execute(text(f"""
                    SELECT query, calls, total_time, mean_time, rows
                    FROM pg_stat_statements
                    ORDER BY mean_time DESC
                    LIMIT {limit}
                """))
                return [dict(row._mapping) for row in result]
            except Exception:
                return []


# Example Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)
    full_name = Column(String(255))
    avatar_url = Column(String(500))
    provider = Column(String(50), nullable=True, index=True)
    provider_id = Column(String(255), nullable=True, index=True)
    is_active = Column(Integer, default=1)
    is_superuser = Column(Integer, default=0)
    created_at = Column(DateTime, default=sa.func.now())
    updated_at = Column(DateTime, default=sa.func.now(), onupdate=sa.func.now())
    meta = Column(JSONB, default={})


class Project(Base):
    __tablename__ = "projects"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    settings = Column(JSONB, default={})
    created_at = Column(DateTime, default=sa.func.now())
    updated_at = Column(DateTime, default=sa.func.now(), onupdate=sa.func.now())


# Example Migrations
INITIAL_MIGRATIONS = [
    Migration(
        version="001",
        name="create_users_table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) UNIQUE NOT NULL,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255),
                full_name VARCHAR(255),
                avatar_url VARCHAR(500),
                provider VARCHAR(50),
                provider_id VARCHAR(255),
                is_active BOOLEAN DEFAULT true,
                is_superuser BOOLEAN DEFAULT false,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                metadata JSONB DEFAULT '{}'
            );
            CREATE INDEX idx_users_email ON users(email);
            CREATE INDEX idx_users_username ON users(username);
            CREATE INDEX idx_users_provider ON users(provider);
            CREATE INDEX idx_users_provider_id ON users(provider_id);
        """,
        down_sql="DROP TABLE IF EXISTS users;"
    ),
    Migration(
        version="002",
        name="create_projects_table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS projects (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                description TEXT,
                owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
                settings JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            CREATE INDEX idx_projects_owner ON projects(owner_id);
        """,
        down_sql="DROP TABLE IF EXISTS projects;"
    ),
]