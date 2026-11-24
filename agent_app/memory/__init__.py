"""
Layered Memory Architecture for EMOG
- Short-term: Redis session buffer (last N messages per branch)
- Episodic: pgvector RAG (async summarization, embedding, retrieval)
- Semantic: Neo4j knowledge graph (entity extraction, prompt enrichment)
"""