"""
Memory Manager - Coordinates all three memory layers
"""
import asyncio
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from .short_term import ShortTermMemory, get_short_term_memory, ShortTermMessage
from .episodic import EpisodicMemory, get_episodic_memory
from .semantic import SemanticMemory, get_semantic_memory


@dataclass
class MemoryContext:
    """Combined context from all memory layers"""
    short_term: List[ShortTermMessage]
    episodic: List[Dict[str, Any]]
    semantic: str
    conversation_id: str
    branch_node_id: str


class MemoryManager:
    """
    Unified interface for all three memory layers.
    Handles reading/writing across short-term, episodic, and semantic memory.
    """
    
    def __init__(self):
        self.stm = get_short_term_memory()
        self.etm = get_episodic_memory()
        self.sem = get_semantic_memory()
        self._episodic_trigger_threshold = 20  # Trigger episodic storage after N messages
        self._message_counts = {}  # Track messages per branch
    
    async def get_context(self, conversation_id: str, branch_node_id: str = None,
                         query: str = "") -> MemoryContext:
        """
        Get combined context from all memory layers.
        """
        # Short-term: last N messages from active branch
        stm_messages = self.stm.get_messages(conversation_id, branch_node_id)
        
        # Episodic: similar past episodes
        episodic_entries = []
        if query:
            episodic_entries = await self.etm.retrieve_similar(conversation_id, query, top_k=3)
        
        # Semantic: known facts about user/project
        semantic_context = ""
        if self.sem:
            semantic_context = await self.sem.get_context_for_prompt(conversation_id, query)
        
        return MemoryContext(
            short_term=stm_messages,
            episodic=[{
                "id": e.id,
                "summary": e.summary,
                "branch_node_id": e.branch_node_id,
                "created_at": e.created_at,
                "message_count": e.message_count
            } for e in episodic_entries],
            semantic=semantic_context,
            conversation_id=conversation_id,
            branch_node_id=branch_node_id or ""
        )
    
    def add_message(self, conversation_id: str, branch_node_id: str,
                   role: str, content: str, model: str = "",
                   tokens_used: int = 0, node_id: str = None,
                   parent_id: str = None) -> None:
        """
        Add a message to short-term memory.
        Triggers episodic storage if threshold reached.
        """
        message = ShortTermMessage(
            node_id=node_id or "",
            role=role,
            content=content,
            model=model,
            tokens_used=tokens_used,
            timestamp=datetime.utcnow().isoformat(),
            parent_id=parent_id
        )
        
        self.stm.push_message(conversation_id, branch_node_id, message)
        
        # Track message count for episodic trigger
        key = f"{conversation_id}:{branch_node_id}"
        self._message_counts[key] = self._message_counts.get(key, 0) + 1
        
        # Trigger episodic storage if threshold reached
        if self._message_counts[key] >= self._episodic_trigger_threshold:
            self._message_counts[key] = 0
            # Note: Actual episodic storage should be triggered with full message list
            # This is handled by the view when it has access to all messages
    
    async def store_episode(self, conversation_id: str, branch_node_id: str,
                           messages: List[Dict], summarizer_fn=None) -> None:
        """Store a conversation segment as episodic memory"""
        await self.etm.store_episode(conversation_id, branch_node_id, messages, summarizer_fn)
    
    async def extract_semantic(self, conversation_id: str, messages: List[Dict],
                              extractor_fn=None) -> None:
        """Extract and store semantic facts"""
        if self.sem:
            await self.sem.extract_and_store(conversation_id, messages, extractor_fn)
    
    async def enrich_prompt(self, system_prompt: str, conversation_id: str,
                           branch_node_id: str = None, query: str = "") -> str:
        """
        Enrich system prompt with all relevant memory context.
        """
        context = await self.get_context(conversation_id, branch_node_id, query)
        
        parts = [system_prompt]
        
        # Add semantic memory (persistent facts)
        if context.semantic:
            parts.append(context.semantic)
        
        # Add episodic memory (relevant past episodes)
        if context.episodic:
            ep_text = "\n=== EPISODIC MEMORY (Relevant Past) ==="
            for ep in context.episodic:
                ep_text += f"\n- [{ep['created_at']}] {ep['summary'][:200]}..."
            ep_text += "\n=== END EPISODIC MEMORY ==="
            parts.append(ep_text)
        
        return "\n\n".join(parts)
    
    def switch_branch(self, conversation_id: str, new_branch_node_id: str):
        """Switch active branch in short-term memory"""
        self.stm.set_active_branch(conversation_id, new_branch_node_id)
    
    def get_branch_info(self, conversation_id: str) -> Dict[str, Any]:
        """Get info about all branches"""
        return self.stm.get_branch_info(conversation_id)


# Global instance
_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """Get or create global memory manager instance"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager