"""
Semantic Memory - Neo4j Knowledge Graph
Entity extraction, relationships, and dynamic prompt enrichment.
"""
import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

try:
    from neo4j import GraphDatabase, Driver
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False
    GraphDatabase = None
    Driver = None


@dataclass
class Entity:
    """An entity in the knowledge graph"""
    id: str
    name: str
    type: str
    properties: Dict[str, Any]


@dataclass
class Relationship:
    """A relationship between entities"""
    source: str
    target: str
    type: str
    properties: Dict[str, Any]


class SemanticMemory:
    """
    Neo4j-backed semantic memory (knowledge graph).
    Extracts entities and relationships from conversations for persistent facts.
    """
    
    def __init__(self, uri: str = None, user: str = None, password: str = None):
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        # Guvenlik: parola ASLA koda gomulu varsayilanla gelmez.
        # Eksikse ortam degiskeninden okunur, yoksa None kalir ve
        # baglanti denemesi anlasilir sekilde basarisiz olur.
        self.user = user or os.environ.get("NEO4J_USER") or "neo4j"
        self.password = password or os.environ.get("NEO4J_PASSWORD")
        self._driver: Optional[Driver] = None
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._init_driver()
    
    def _init_driver(self):
        """Initialize Neo4j driver"""
        if not NEO4J_AVAILABLE:
            print("Warning: neo4j driver not installed. Semantic memory disabled.")
            return
        try:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self._init_schema()
        except Exception as e:
            print(f"Warning: Could not connect to Neo4j: {e}")
            self._driver = None
    
    def _init_schema(self):
        """Create constraints and indexes"""
        if not self._driver:
            return
        with self._driver.session() as session:
            # Constraints
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE")
            
            # Indexes
            session.run("CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.name)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.name)")
    
    def close(self):
        if self._driver:
            self._driver.close()
    
    def is_available(self) -> bool:
        return self._driver is not None
    
    async def extract_and_store(self, conversation_id: str, messages: List[Dict[str, Any]],
                               extractor_fn=None) -> Dict[str, Any]:
        """
        Extract entities and relationships from messages and store in Neo4j.
        """
        if not self._driver:
            return {"entities": 0, "relationships": 0}
        
        # Prepare conversation text
        text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        
        # Extract entities and relationships
        if extractor_fn:
            result = await asyncio.get_event_loop().run_in_executor(
                self._executor, extractor_fn, text, conversation_id
            )
        else:
            # Fallback: simple pattern-based extraction
            result = await asyncio.get_event_loop().run_in_executor(
                self._executor, self._simple_extract, text, conversation_id
            )
        
        # Store in Neo4j
        await asyncio.get_event_loop().run_in_executor(
            self._executor, self._store_entities, result.get("entities", []), conversation_id
        )
        await asyncio.get_event_loop().run_in_executor(
            self._executor, self._store_relationships, result.get("relationships", []), conversation_id
        )
        
        return result
    
    def _simple_extract(self, text: str, conversation_id: str) -> Dict[str, Any]:
        """Simple fallback extraction using patterns"""
        entities = []
        relationships = []
        
        # Simple patterns for tech stack detection
        tech_patterns = {
            "Django": ["django", "django rest", "drf"],
            "FastAPI": ["fastapi"],
            "PostgreSQL": ["postgresql", "postgres", "psycopg"],
            "Redis": ["redis"],
            "Celery": ["celery"],
            "Neo4j": ["neo4j"],
            "Docker": ["docker"],
            "Python": ["python"],
            "JavaScript": ["javascript", "js ", "node"],
            "TypeScript": ["typescript", "ts "],
            "React": ["react"],
            "Vue": ["vue"],
        }
        
        text_lower = text.lower()
        for entity_name, patterns in tech_patterns.items():
            if any(p in text_lower for p in patterns):
                entities.append({
                    "id": f"tech:{entity_name.lower()}",
                    "name": entity_name,
                    "type": "Technology",
                    "properties": {"domain": "software"}
                })
        
        # Add conversation as entity
        entities.append({
            "id": f"conversation:{conversation_id}",
            "name": f"Conversation {conversation_id[:8]}",
            "type": "Conversation",
            "properties": {"conversation_id": conversation_id}
        })
        
        # Link tech to conversation
        for entity in entities:
            if entity["type"] == "Technology":
                relationships.append({
                    "source": f"conversation:{conversation_id}",
                    "target": entity["id"],
                    "type": "USES",
                    "properties": {}
                })
        
        return {"entities": entities, "relationships": relationships}
    
    def _store_entities(self, entities: List[Dict], conversation_id: str):
        if not self._driver:
            return
        with self._driver.session() as session:
            for entity in entities:
                session.run("""
                    MERGE (e:Entity {id: $id})
                    SET e.name = $name, e.type = $type, e.updated_at = datetime()
                    SET e += $properties
                    MERGE (c:Conversation {id: $conversation_id})
                    MERGE (c)-[:MENTIONS]->(e)
                """, id=entity["id"], name=entity["name"], 
                     type=entity["type"], properties=entity.get("properties", {}),
                     conversation_id=conversation_id)
    
    def _store_relationships(self, relationships: List[Dict], conversation_id: str):
        if not self._driver:
            return
        with self._driver.session() as session:
            for rel in relationships:
                session.run("""
                    MATCH (a:Entity {id: $source})
                    MATCH (b:Entity {id: $target})
                    MERGE (a)-[r:%s]->(b)
                    SET r += $properties, r.updated_at = datetime()
                """ % rel["type"], source=rel["source"], target=rel["target"],
                     properties=rel.get("properties", {}))
    
    async def get_context_for_prompt(self, conversation_id: str, query: str = "") -> str:
        """
        Retrieve relevant facts from knowledge graph for prompt enrichment.
        Returns formatted string to prepend to system prompt.
        """
        if not self._driver:
            return ""
        
        def _query():
            with self._driver.session() as session:
                # Get technologies used
                result = session.run("""
                    MATCH (c:Conversation {id: $cid})-[:MENTIONS]->(e:Entity)
                    WHERE e.type = 'Technology'
                    RETURN e.name, e.type, e.properties
                """, cid=conversation_id)
                
                tech = [{"name": r["e.name"], "type": r["e.type"], "props": r["e.properties"]} 
                        for r in result]
                
                # Get user preferences/patterns
                result = session.run("""
                    MATCH (c:Conversation {id: $cid})-[:MENTIONS]->(e:Entity)
                    WHERE e.type = 'Preference'
                    RETURN e.name, e.properties
                """, cid=conversation_id)
                
                prefs = [{"name": r["e.name"], "props": r["e.properties"]} for r in result]
                
                return {"technologies": tech, "preferences": prefs}
        
        facts = await asyncio.get_event_loop().run_in_executor(self._executor, _query)
        
        if not facts["technologies"] and not facts["preferences"]:
            return ""
        
        parts = ["\n=== SEMANTIC MEMORY (Known Facts) ==="]
        
        if facts["technologies"]:
            tech_names = [t["name"] for t in facts["technologies"]]
            parts.append(f"Technologies in use: {', '.join(tech_names)}")
        
        if facts["preferences"]:
            for p in facts["preferences"]:
                parts.append(f"User preference: {p['name']} = {p['props']}")
        
        parts.append("=== END SEMANTIC MEMORY ===\n")
        return "\n".join(parts)
    
    async def link_project(self, conversation_id: str, project_path: str, 
                          project_name: str, tech_stack: List[str]):
        """Link a conversation to a project with tech stack"""
        if not self._driver:
            return
        
        def _link():
            with self._driver.session() as session:
                session.run("""
                    MERGE (p:Project {id: $project_id})
                    SET p.name = $name, p.path = $path, p.updated_at = datetime()
                    MERGE (c:Conversation {id: $conversation_id})
                    MERGE (c)-[:BELONGS_TO]->(p)
                """, project_id=project_path, name=project_name, 
                     path=project_path, conversation_id=conversation_id)
                
                for tech in tech_stack:
                    session.run("""
                        MERGE (t:Entity {id: $tech_id})
                        SET t.name = $tech_name, t.type = 'Technology'
                        MERGE (p:Project {id: $project_id})
                        MERGE (p)-[:USES]->(t)
                    """, tech_id=f"tech:{tech.lower()}", tech_name=tech, 
                         project_id=project_path)
        
        await asyncio.get_event_loop().run_in_executor(self._executor, _link)


# Global instance
_semantic_memory: Optional[SemanticMemory] = None


def get_semantic_memory() -> Optional[SemanticMemory]:
    """Get or create global semantic memory instance"""
    global _semantic_memory
    if _semantic_memory is None:
        _semantic_memory = SemanticMemory()
    return _semantic_memory if _semantic_memory.is_available() else None