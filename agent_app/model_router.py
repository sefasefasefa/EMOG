"""
Model Router - Automatic model selection based on task complexity.
Routes tasks to appropriate NVIDIA model tier based on complexity analysis.
"""
import re
import os
from typing import Dict, Optional, Tuple
from django.conf import settings


class ModelRouter:
    """Routes tasks to appropriate model tier based on complexity analysis."""

    def __init__(self):
        self.tiers = getattr(settings, 'NVIDIA_MODEL_TIERS', {})
        self.keywords = getattr(settings, 'TASK_COMPLEXITY_KEYWORDS', {})
        self.default_tier = getattr(settings, 'DEFAULT_TASK_TIER', 'standard')
        self.routing_config = getattr(settings, 'MODEL_ROUTING', {})
        self.task_models = getattr(settings, 'NVIDIA_TASK_MODELS', {}) or {}

        # Tier order for escalation (lowest to highest capability)
        self.tier_order = ['lite', 'fast', 'standard', 'high', 'ultra']
        self.tier_index = {tier: i for i, tier in enumerate(self.tier_order)}

    # --- Amaca gore model secimi -------------------------------------------------
    # Sira onemli: once arastirma/derin analiz, sonra kodlama, sonra hizli yurutme.
    PURPOSE_RULES = (
        (('araştır', 'araştir', 'araştırma', 'research', 'analiz', 'analyze', 'analys',
          'mimari', 'architecture', 'karmaşık', 'karmasik', 'complex', 'derin', 'deep',
          'karşılaştır', 'karsilastir', 'compare', 'strateji', 'strategy', 'rapor',
          'design', 'mimarisi', 'senaryo', 'karar'), 'research'),
        (('kod', 'code', 'python', 'javascript', 'typescript', 'refactor', 'bug', 'hata',
          'fonksiyon', 'class', 'test', 'script', 'api', 'endpoint', 'implement',
          'frontend', 'backend', 'sql', 'veritaban', 'program',
          'derle', 'çalıştır', 'calistir', 'değişken', 'degisken', 'modül', 'modul',
          'git', 'commit', 'pr', 'deploy'), 'coding'),
        (('hızlı', 'hizli', 'toplu', 'batch', 'topluca', 'sık', 'sik', 'hemen',
          'quick', 'basit', 'simple', 'küçük', 'kucuk', 'biçimlendir', 'format',
          'özetle', 'ozetle', 'listele'), 'agent'),
    )

    def select_for_task_text(self, task_text):
        """Metne bakarak amaca uygun modeli seçer. (model_id, anahtar) doner."""
        text = (task_text or '').lower()
        for keywords, key in self.PURPOSE_RULES:
            if any(k in text for k in keywords):
                model_id = self.task_models.get(key)
                if model_id:
                    return model_id, key
        return self.task_models.get('default') or getattr(settings, 'OMNIROUTE_DEFAULT_MODEL', ''), 'default'

    def classify_task(self, task_text: str, context: str = "") -> str:
        """
        Classify task complexity based on keywords and patterns.
        Returns tier name: 'lite', 'fast', 'standard', 'high', or 'ultra'
        """
        text = (task_text + " " + context).lower()
        
        # Score each tier based on keyword matches
        scores = {}
        for tier, keywords in getattr(settings, 'TASK_COMPLEXITY_KEYWORDS', {}).items():
            score = 0
            for kw in keywords:
                # Use word boundary matching for better accuracy
                pattern = r'\b' + re.escape(kw.lower()) + r'\b'
                matches = len(re.findall(pattern, text))
                score += matches
            scores[tier] = score
        
        # Additional heuristics
        scores = self._apply_heuristics(scores, text)
        
        # Find highest scoring tier
        if not scores or max(scores.values()) == 0:
            return self.default_tier
        
        # Get tier with highest score
        best_tier = max(scores, key=scores.get)
        return best_tier
    
    def _apply_heuristics(self, scores: Dict[str, int], text: str) -> Dict[str, int]:
        """Apply additional heuristics for better classification."""
        
        # Code-related patterns increase complexity
        code_patterns = [
            r'\b(class|function|def|async|await)\b',
            r'\b(import|export|require|from\s+\w+\s+import)\b',
            r'\b(async|await|promise|callback)\b',
            r'\b(database|query|sql|migration)\b',
            r'\b(api|endpoint|route|middleware)\b',
            r'\b(test|test|spec|mock|fixture)\b',
            r'\b(docker|kubernetes|deploy|ci/cd)\b',
        ]
        
        code_score = sum(1 for p in code_patterns if re.search(p, text))
        if code_score > 2:
            scores['standard'] = scores.get('standard', 0) + code_score
            scores['high'] = scores.get('high', 0) + code_score // 2
        
        # Length-based heuristics
        word_count = len(text.split())
        if word_count < 10:
            scores['lite'] = scores.get('lite', 0) + 2
            scores['fast'] = scores.get('fast', 0) + 1
        elif word_count > 100:
            scores['high'] = scores.get('high', 0) + 2
            scores['ultra'] = scores.get('ultra', 0) + 1
        
        # Multi-step task indicators
        multi_step = len(re.findall(r'\b(then|and then|after that|next|step \d|first|second|third|finally)\b', text))
        if multi_step > 2:
            scores['high'] = scores.get('high', 0) + multi_step
            scores['ultra'] = scores.get('ultra', 0) + multi_step // 2
        
        return scores
    
    def get_model_for_tier(self, tier: str) -> Dict:
        """Get model configuration for a tier."""
        return self.tiers.get(tier, self.tiers.get(self.default_tier, {}))
    
    def select_model(self, task_text: str, context: str = "", preferred_tier: Optional[str] = None) -> Tuple[str, Dict]:
        """
        Select appropriate model for a task.
        Returns: (tier_name, model_config)
        """
        if preferred_tier and preferred_tier in self.tiers:
            tier = preferred_tier
        elif self.routing_config.get('enabled', True):
            tier = self.classify_task(task_text, context)
        else:
            tier = self.default_tier
        
        # Validate tier exists
        if tier not in self.tiers:
            tier = self.default_tier
        
        model_config = self.get_model_for_tier(tier)
        return tier, model_config
    
    def escalate_tier(self, current_tier: str, reason: str = "") -> str:
        """Escalate to next higher tier if available."""
        current_idx = self.tier_index.get(current_tier, 2)  # default to standard
        max_idx = len(self.tier_order) - 1
        
        if current_idx < max_idx:
            new_tier = self.tier_order[current_idx + 1]
            return new_tier
        return current_tier
    
    def get_tier_info(self, tier: str) -> Dict:
        """Get full tier information."""
        return self.tiers.get(tier, {})


# Global router instance
_router = None

def get_model_router() -> ModelRouter:
    """Get or create global model router instance."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def select_model_for_task(task_text: str, context: str = "", preferred_tier: str = None) -> Tuple[str, Dict]:
    """Convenience function to select model for a task."""
    router = get_model_router()
    return router.select_model(task_text, context, preferred_tier)


def select_purpose_model(task_text: str) -> Tuple[str, str]:
    """Amaca gore (kodlama / arastirma / hizli yurutme) model secimi."""
    return get_model_router().select_for_task_text(task_text)


def escalate_model_tier(current_tier: str, reason: str = "") -> str:
    """Escalate to higher tier."""
    router = get_model_router()
    return router.escalate_tier(current_tier, reason)