"""
Conversation History Service - Git-like tree operations with Recursive CTE
"""
from django.db import connection, transaction
from django.db.models import Q
from typing import List, Optional, Dict, Any
import uuid

from .models import Conversation, Node, Checkpoint


class ConversationHistoryService:
    """Service for managing Git-like conversation history"""

    @staticmethod
    def get_path_to_root(node_id: str) -> List[Node]:
        """
        Get the full path from root to the given node using Recursive CTE.
        Returns nodes in chronological order (root first).
"""
        # SQLite stores UUIDs without hyphens
        node_id_str = str(node_id).replace('-', '')
        with connection.cursor() as cursor:
            cursor.execute("""
                WITH RECURSIVE node_path AS (
                    -- Base case: start from the target node
                    SELECT id, conversation_id, parent_id, role, content, 
                           tokens_used, model, meta_data, timestamp, 0 as depth
                    FROM nodes
                    WHERE id = %s
                    
                    UNION ALL
                    
                    -- Recursive case: walk up to parent
                    SELECT n.id, n.conversation_id, n.parent_id, n.role, n.content,
                           n.tokens_used, n.model, n.meta_data, n.timestamp, np.depth + 1
                    FROM nodes n
                    INNER JOIN node_path np ON n.id = np.parent_id
                )
                SELECT id, conversation_id, parent_id, role, content,
                       tokens_used, model, meta_data, timestamp
                FROM node_path
                ORDER BY depth DESC
            """, [node_id_str])
            
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            
            nodes = []
            for row in rows:
                node = Node(
                    id=row[0],
                    conversation_id=row[1],
                    parent_id=row[2],
                    role=row[3],
                    content=row[4],
                    tokens_used=row[5],
                    model=row[6],
                    meta_data=row[7] or {},
                    timestamp=row[8]
                )
                nodes.append(node)
            return nodes

    @staticmethod
    def get_branch_nodes(node_id: str) -> List[Node]:
        """
        Get all nodes in the branch starting from the given node (including descendants).
        """
        # SQLite stores UUIDs without hyphens
        node_id_str = str(node_id).replace('-', '')
        with connection.cursor() as cursor:
            cursor.execute("""
                WITH RECURSIVE branch_nodes AS (
                    -- Base case: start from the given node
                    SELECT id, conversation_id, parent_id, role, content,
                           tokens_used, model, meta_data, timestamp, 0 as depth
                    FROM nodes
                    WHERE id = %s
                    
                    UNION ALL
                    
                    -- Recursive case: walk down to children
                    SELECT n.id, n.conversation_id, n.parent_id, n.role, n.content,
                           n.tokens_used, n.model, n.meta_data, n.timestamp, bn.depth + 1
                    FROM nodes n
                    INNER JOIN branch_nodes bn ON n.parent_id = bn.id
                )
                SELECT id, conversation_id, parent_id, role, content,
                       tokens_used, model, meta_data, timestamp
                FROM branch_nodes
                ORDER BY depth, timestamp
            """, [node_id_str])
            
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            
            nodes = []
            for row in rows:
                node = Node(
                    id=row[0],
                    conversation_id=row[1],
                    parent_id=row[2],
                    role=row[3],
                    content=row[4],
                    tokens_used=row[5],
                    model=row[6],
                    meta_data=row[7] or {},
                    timestamp=row[8]
                )
                nodes.append(node)
            return nodes

    @staticmethod
    def get_conversation_tree(conversation_id: str) -> List[Dict[str, Any]]:
        """
        Get the full conversation tree structure using Recursive CTE.
        Returns nested structure with children arrays.
        """
        # SQLite stores UUIDs without hyphens
        conv_id_str = str(conversation_id).replace('-', '')
        with connection.cursor() as cursor:
            cursor.execute("""
                WITH RECURSIVE tree AS (
                    -- Root nodes (no parent)
                    SELECT id, conversation_id, parent_id, role, content,
                           tokens_used, model, meta_data, timestamp,
                           0 as depth,
                           id as path
                    FROM nodes
                    WHERE conversation_id = %s AND parent_id IS NULL
                    
                    UNION ALL
                    
                    -- Children
                    SELECT n.id, n.conversation_id, n.parent_id, n.role, n.content,
                           n.tokens_used, n.model, n.meta_data, n.timestamp,
                           t.depth + 1,
                           t.path || '/' || n.id
                    FROM nodes n
                    INNER JOIN tree t ON n.parent_id = t.id
                    WHERE n.conversation_id = %s
                )
                SELECT id, conversation_id, parent_id, role, content,
                       tokens_used, model, meta_data, timestamp, depth, path
                FROM tree
                ORDER BY path
            """, [conv_id_str, conv_id_str])
            
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            
            # Build tree structure
            nodes_by_id = {}
            root_nodes = []
            
            for row in rows:
                node_data = {
                    'id': str(row[0]),
                    'conversation_id': str(row[1]),
                    'parent_id': str(row[2]) if row[2] else None,
                    'role': row[3],
                    'content': row[4],
                    'tokens_used': row[5],
                    'model': row[6],
                    'meta_data': row[7] or {},
                    'timestamp': row[8].isoformat() if row[8] else None,
                    'depth': row[9],
                    'children': []
                }
                nodes_by_id[str(row[0])] = node_data
                
                if row[2] is None:
                    root_nodes.append(node_data)
                else:
                    parent_id = str(row[2])
                    if parent_id in nodes_by_id:
                        nodes_by_id[parent_id]['children'].append(node_data)
            
            return root_nodes

    @staticmethod
    def get_leaf_nodes(conversation_id: str) -> List[Node]:
        """Get all leaf nodes (nodes with no children) in a conversation"""
        # SQLite stores UUIDs without hyphens
        conv_id_str = str(conversation_id).replace('-', '')
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT n.id, n.conversation_id, n.parent_id, n.role, n.content,
                       n.tokens_used, n.model, n.meta_data, n.timestamp
                FROM nodes n
                WHERE n.conversation_id = %s
                AND NOT EXISTS (
                    SELECT 1 FROM nodes c WHERE c.parent_id = n.id
                )
                ORDER BY n.timestamp
            """, [conv_id_str])
            
            rows = cursor.fetchall()
            nodes = []
            for row in rows:
                node = Node(
                    id=row[0],
                    conversation_id=row[1],
                    parent_id=row[2],
                    role=row[3],
                    content=row[4],
                    tokens_used=row[5],
                    model=row[6],
                    meta_data=row[7] or {},
                    timestamp=row[8]
                )
                nodes.append(node)
            return nodes

    @staticmethod
    def create_branch(conversation_id: str, target_node_id: str, user_message: str, 
                      role: str = 'user', meta_data: dict = None) -> Node:
        """
        Create a new branch from the target node.
        The new node will have the target node as its parent.
        """
        target_node = Node.objects.get(id=target_node_id)
        
        if str(target_node.conversation_id) != conversation_id:
            raise ValueError("Target node does not belong to this conversation")
        
        new_node = Node.objects.create(
            conversation_id=conversation_id,
            parent=target_node,
            role=role,
            content=user_message,
            meta_data=meta_data or {}
        )
        
        return new_node

    @staticmethod
    def create_checkpoint(node_id: str, snapshot_type: str = 'full', 
                          snapshot_data: dict = None) -> Checkpoint:
        """Create a checkpoint at the given node"""
        node = Node.objects.get(id=node_id)
        
        checkpoint, created = Checkpoint.objects.update_or_create(
            node=node,
            defaults={
                'snapshot_type': snapshot_type,
                'snapshot_data': snapshot_data or {}
            }
        )
        return checkpoint

    @staticmethod
    def get_checkpoint(node_id: str) -> Optional[Checkpoint]:
        """Get checkpoint for a node"""
        try:
            return Checkpoint.objects.get(node_id=node_id)
        except Checkpoint.DoesNotExist:
            return None

    @staticmethod
    def get_conversation_messages_for_llm(node_id: str) -> List[Dict[str, str]]:
        """
        Get the message path from root to node, formatted for LLM consumption.
        """
        path = ConversationHistoryService.get_path_to_root(node_id)
        return [
            {"role": node.role, "content": node.content}
            for node in path
        ]


class ConversationManager:
    """High-level conversation management"""

    @staticmethod
    def create_conversation(user=None, title: str = "", meta_data: dict = None) -> Conversation:
        return Conversation.objects.create(
            user=user,
            title=title,
            meta_data=meta_data or {}
        )

    @staticmethod
    def add_message(conversation_id: str, parent_node_id: Optional[str], 
                    role: str, content: str, model: str = "", 
                    tokens_used: int = 0, meta_data: dict = None) -> Node:
        """Add a new message node to the conversation"""
        parent = None
        if parent_node_id:
            parent = Node.objects.get(id=parent_node_id)
            if str(parent.conversation_id) != conversation_id:
                raise ValueError("Parent node not in this conversation")
        
        node = Node.objects.create(
            conversation_id=conversation_id,
            parent=parent,
            role=role,
            content=content,
            model=model,
            tokens_used=tokens_used,
            meta_data=meta_data or {}
        )
        
        # Update conversation timestamp
        Conversation.objects.filter(id=conversation_id).update(
            updated_at=node.timestamp
        )
        
        return node

    @staticmethod
    def get_or_create_active_branch(conversation_id: str) -> Node:
        """
        Get the most recent leaf node (active branch tip) or create root if empty.
        """
        leaves = ConversationHistoryService.get_leaf_nodes(conversation_id)
        if leaves:
            return leaves[-1]  # Most recent leaf
        
        # No nodes yet, create a system root node
        conv = Conversation.objects.get(id=conversation_id)
        return Node.objects.create(
            conversation=conv,
            parent=None,
            role='system',
            content=conv.meta_data.get('system_prompt', 'You are a helpful AI assistant.'),
            meta_data={'is_root': True}
        )