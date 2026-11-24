"""
Episodic Memory - pgvector RAG
Async summarization, embedding, and cosine similarity retrieval.
"""
import json
import os
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import psycopg2
from psycopg2.extras import RealDictCursor
import numpy as np


@dataclass
class EpisodicMemoryEntry:
    """An episodic memory entry"""
    id: str
    conversation_id: str
    branch_node_id: str
    summary: str
    embedding: List[float]
    source_node_ids: List[str]
    created_at: str
    message_count: int


class EpisodicMemory:
    """
    pgvector-backed episodic memory.
    Stores summarized conversation chunks with embeddings for semantic retrieval.
    """
    
    def __init__(self, db_url: str = None, embedding_dim: int = 1536):
        self.embedding_dim = embedding_dim
        db_url = db_url or os.environ.get("DATABASE_URL", "postgresql://localhost/emog")
        self.db_url = db_url
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._init_db()
    
    def _init_db(self):
        """Initialize pgvector extension and tables"""
        conn = psycopg2.connect(self.db_url)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Enable pgvector extension
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        
        # Create episodic memory table
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS episodic_memory (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL,
                branch_node_id UUID NOT NULL,
                summary TEXT NOT NULL,
                embedding vector({self.embedding_dim}),
                source_node_ids UUID[] DEFAULT '{{}}',
                message_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            
            CREATE INDEX IF NOT EXISTS idx_episodic_conversation 
            ON episodic_memory(conversation_id);
            
            CREATE INDEX IF NOT EXISTS idx_episodic_embedding 
            ON episodic_memory USING hnsw (embedding vector_cosine_ops);
        """)
        cur.close()
        conn.close()
    
    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text. Override with actual embedding model."""
        # TODO: Replace with actual embedding model (OpenAI, local, etc.)
        # For now, return deterministic pseudo-embedding based on text hash
        import hashlib
        hash_obj = hashlib.md5(text.encode())
        seed = int(hash_obj.hexdigest(), 16)
        np.random.seed(seed)
        embedding = np.random.normal(0, 1, self.embedding_dim).astype(np.float32)
        embedding = embedding / np.linalg.norm(embedding)
        return embedding.tolist()
    
    async def store_episode(self, conversation_id: str, branch_node_id: str,
                           messages: List[Dict[str, Any]],
                           summarizer_fn=None) -> EpisodicMemoryEntry:
        """
        Store a conversation segment as an episodic memory.
        Runs summarization and embedding in background thread.
        """
        # Prepare text for summarization
        text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        
        # Generate summary
        if summarizer_fn:
            summary = await asyncio.get_event_loop().run_in_executor(
                self._executor, summarizer_fn, text
            )
        else:
            # Fallback: first 500 chars
            summary = text[:500] + ("..." if len(text) > 500 else "")
        
        # Get embedding
        embedding = await asyncio.get_event_loop().run_in_executor(
            self._executor, self._get_embedding, summary
        )
        
        # Extract node IDs
        node_ids = [m.get('id') for m in messages if m.get('id')]
        
        # Store in database
        def _store():
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO episodic_memory 
                (conversation_id, branch_node_id, summary, embedding, source_node_ids, message_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (
                conversation_id, branch_node_id, summary, embedding, 
                node_ids, len(messages)
            ))
            result = cur.fetchone()
            conn.commit()
            cur.close()
            conn.close()
            return result
        
        result = await asyncio.get_event_loop().run_in_executor(self._executor, _store)
        
        return EpisodicMemoryEntry(
            id=str(result[0]),
            conversation_id=conversation_id,
            branch_node_id=branch_node_id,
            summary=summary,
            embedding=embedding,
            source_node_ids=node_ids,
            created_at=result[1].isoformat(),
            message_count=len(messages)
        )
    
    async def retrieve_similar(self, conversation_id: str, query: str, 
                              top_k: int = 3, similarity_threshold: float = 0.7) -> List[EpisodicMemoryEntry]:
        """
        Retrieve similar episodic memories using cosine similarity.
        """
        query_embedding = await asyncio.get_event_loop().run_in_executor(
            self._executor, self._get_embedding, query
        )
        
        def _search():
            conn = psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
            cur = conn.cursor()
            # Cosine similarity: 1 - (embedding <=> query_embedding)
            cur.execute("""
                SELECT id, conversation_id, branch_node_id, summary, embedding,
                       source_node_ids, message_count, created_at,
                       1 - (embedding <=> %s) as similarity
                FROM episodic_memory
                WHERE conversation_id = %s
                AND 1 - (embedding <=> %s) > %s
                ORDER BY embedding <=> %s
                LIMIT %s
            """, (query_embedding, conversation_id, query_embedding, 
                  similarity_threshold, query_embedding, top_k))
            return cur.fetchall()
        
        results = await asyncio.get_event_loop().run_in_executor(self._executor, _search)
        
        entries = []
        for row in results:
            entries.append(EpisodicMemoryEntry(
                id=str(row['id']),
                conversation_id=str(row['conversation_id']),
                branch_node_id=str(row['branch_node_id']),
                summary=row['summary'],
                embedding=row['embedding'],
                source_node_ids=[str(x) for x in row['source_node_ids']],
                created_at=row['created_at'].isoformat() if row['created_at'] else '',
                message_count=row['message_count']
            ))
        
        return entries
    
    def get_conversation_episodes(self, conversation_id: str) -> List[EpisodicMemoryEntry]:
        """Get all episodes for a conversation"""
        conn = psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, conversation_id, branch_node_id, summary, embedding,
                   source_node_ids, message_count, created_at
            FROM episodic_memory
            WHERE conversation_id = %s
            ORDER BY created_at DESC
        """, (conversation_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        entries = []
        for row in rows:
            entries.append(EpisodicMemoryEntry(
                id=str(row['id']),
                conversation_id=str(row['conversation_id']),
                branch_node_id=str(row['branch_node_id']),
                summary=row['summary'],
                embedding=row['embedding'],
                source_node_ids=[str(x) for x in row['source_node_ids']],
                created_at=row['created_at'].isoformat() if row['created_at'] else '',
                message_count=row['message_count']
            ))
        return entries


# Global instance
_episodic_memory: Optional[EpisodicMemory] = None


def get_episodic_memory() -> EpisodicMemory:
    """Get or create global episodic memory instance"""
    global _episodic_memory
    if _episodic_memory is None:
        _episodic_memory = EpisodicMemory()
    return _episodic_memory