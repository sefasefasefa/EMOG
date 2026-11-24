"""
Memory Maintenance Tasks - Pruning, Re-indexing, Deduplication
"""
import os
import json
from celery import shared_task
from ..celery_app import app
from typing import List, Dict, Any
from datetime import datetime, timedelta

# Import memory modules
from agent_app.memory.episodic import get_episodic_memory, EpisodicMemory
from agent_app.memory.semantic import get_semantic_memory, SemanticMemory
from agent_app.memory.short_term import get_short_term_memory


@shared_task(bind=True, name='agent_app.tasks.memory.prune')
def prune_memories(self):
    """
    Daily memory pruning:
    - Remove old short-term buffers (inactive > 7 days)
    - Clean up orphaned episodic entries
    - Remove semantic duplicates
    """
    results = {
        'short_term_pruned': 0,
        'episodic_cleaned': 0,
        'semantic_deduped': 0,
    }
    
    # 1. Prune short-term memory (handled by Redis TTL, but clean orphaned keys)
    try:
        stm = get_short_term_memory()
        # Scan for old conversation keys
        pattern = "emog:stm:*"
        cursor = 0
        while True:
            cursor, keys = stm.client.scan(cursor, match=pattern, count=100)
            for key in keys:
                ttl = stm.client.ttl(key)
                if ttl == -1:  # No expiry set
                    stm.client.expire(key, 86400 * 7)  # Set 7-day TTL
                    results['short_term_pruned'] += 1
            if cursor == 0:
                break
    except Exception as e:
        print(f"Short-term pruning error: {e}")
    
    # 2. Clean episodic memory (remove entries older than 90 days with no recent access)
    try:
        etm = get_episodic_memory()
        conn = etm.db_url
        import psycopg2
        pg_conn = psycopg2.connect(conn)
        cur = pg_conn.cursor()
        
        # Delete episodes older than 90 days with low message count
        cur.execute("""
            DELETE FROM episodic_memory
            WHERE created_at < NOW() - INTERVAL '90 days'
            AND message_count < 3
        """)
        results['episodic_cleaned'] = cur.rowcount
        pg_conn.commit()
        cur.close()
        pg_conn.close()
    except Exception as e:
        print(f"Episodic cleaning error: {e}")
    
    # 3. Semantic deduplication
    try:
        sem = get_semantic_memory()
        if sem and sem.is_available():
            deduped = _deduplicate_semantic_entities(sem)
            results['semantic_deduped'] = deduped
    except Exception as e:
        print(f"Semantic deduplication error: {e}")
    
    return results


def _deduplicate_semantic_entities(sem: SemanticMemory) -> int:
    """Deduplicate similar semantic entities"""
    if not sem._driver:
        return 0
    
    deduplicated = 0
    with sem._driver.session() as session:
        # Find entities with same name and type
        result = session.run("""
            MATCH (e:Entity)
            WITH e.name, e.type, collect(e) as entities
            WHERE size(entities) > 1
            RETURN entities
        """)
        
        for record in result:
            entities = record['entities']
            if len(entities) > 1:
                # Keep the first, merge others into it
                primary = entities[0]
                for dup in entities[1:]:
                    # Merge relationships
                    session.run("""
                        MATCH (dup:Entity {id: $dup_id})-[r]->(other)
                        MATCH (primary:Entity {id: $primary_id})
                        MERGE (primary)-[r2:USES]->(other)
                        SET r2 += r.properties
                    """, dup_id=dup['id'], primary_id=primary['id'])
                    
                    session.run("""
                        MATCH (other)-[r]->(dup:Entity {id: $dup_id})
                        MATCH (primary:Entity {id: $primary_id})
                        MERGE (other)-[r2:USES]->(primary)
                        SET r2 += r.properties
                    """, dup_id=dup['id'], primary_id=primary['id'])
                    
                    # Delete duplicate
                    session.run("DETACH DELETE (d:Entity {id: $dup_id})", dup_id=dup['id'])
                    deduplicated += 1
    
    return deduplicated


@shared_task(bind=True, name='agent_app.tasks.memory.reindex_episodic')
def reindex_episodic(self):
    """
    Weekly re-index of episodic memory embeddings.
    Recomputes embeddings for entries with missing or stale embeddings.
    """
    from agent_app.memory.episodic import EpisodicMemory
    import psycopg2
    from psycopg2.extras import RealDictCursor
    
    etm = EpisodicMemory()
    conn = psycopg2.connect(etm.db_url, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    
    # Find entries with missing or zero embeddings
    cur.execute("""
        SELECT id, summary FROM episodic_memory
        WHERE embedding IS NULL OR embedding = '[]'::vector
    """)
    
    rows = cur.fetchall()
    updated = 0
    
    for row in rows:
        embedding = etm._get_embedding(row['summary'])
        cur.execute("""
            UPDATE episodic_memory SET embedding = %s WHERE id = %s
        """, (embedding, row['id']))
        updated += 1
    
    conn.commit()
    cur.close()
    conn.close()
    
    return {'reindexed': updated}


@shared_task(bind=True, name='agent_app.tasks.memory.deduplicate_semantic')
def deduplicate_semantic(self):
    """
    Weekly semantic deduplication.
    """
    sem = get_semantic_memory()
    if sem and sem.is_available():
        return {'deduplicated': _deduplicate_semantic_entities(sem)}
    return {'deduplicated': 0, 'error': 'Neo4j not available'}


@shared_task(bind=True, name='agent_app.tasks.memory.index_episodic')
def index_episodic(self, conversation_id: str, branch_node_id: str, 
                   messages: List[Dict], summarizer_fn_name: str = None):
    """
    Async episodic indexing task.
    Called after conversation segment ends or threshold reached.
    """
    import asyncio
    from agent_app.memory.episodic import get_episodic_memory
    
    etm = get_episodic_memory()
    
    # Resolve summarizer function
    summarizer_fn = None
    if summarizer_fn_name == 'default':
        summarizer_fn = lambda text: text[:500] + ("..." if len(text) > 500 else "")
    
    async def _run():
        return await etm.store_episode(conversation_id, branch_node_id, messages, summarizer_fn)
    
    return asyncio.run(_run())


@shared_task(bind=True, name='agent_app.tasks.memory.index_semantic')
def index_semantic(self, conversation_id: str, messages: List[Dict], 
                   extractor_fn_name: str = None):
    """
    Async semantic indexing task.
    """
    import asyncio
    from agent_app.memory.semantic import get_semantic_memory
    
    sem = get_semantic_memory()
    if not sem:
        return {'error': 'Semantic memory not available'}
    
    extractor_fn = None
    # Could resolve custom extractor functions here
    
    async def _run():
        return await sem.extract_and_store(conversation_id, messages, extractor_fn)
    
    return asyncio.run(_run())


@shared_task(bind=True, name='agent_app.tasks.projects.index_project')
def index_project(self, conversation_id: str, project_path: str, 
                  project_name: str, tech_stack: List[str]):
    """
    Index a project in semantic memory.
    """
    import asyncio
    from agent_app.memory.semantic import get_semantic_memory
    
    sem = get_semantic_memory()
    if not sem:
        return {'error': 'Semantic memory not available'}
    
    async def _run():
        await sem.link_project(conversation_id, project_path, project_name, tech_stack)
        return {'status': 'indexed'}
    
    return asyncio.run(_run())