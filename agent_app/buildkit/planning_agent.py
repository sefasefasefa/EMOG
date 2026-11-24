"""Planning Agent: Generates detailed BuildPlan from high-level goals."""
import json
import logging
from typing import Dict, List, Optional, Any

from .blueprint import format_for_prompt as format_blueprint
from .plan import BuildPlan, PlanItem, ensure_plan_depth
from agent_app.views import llm_create_resilient
from .llm import pick_model
from django.conf import settings

logger = logging.getLogger(__name__)


PLANNING_SYSTEM_PROMPT = """Sen EMOG'un planlama ajansısın. Kullanıcının yüksek seviyeli hedefini alıp,
detaylı, çalıştırılabilir bir UYGULAMA PLANI (BuildPlan) üreteceksin.

Çıktın SADECE geçerli JSON olmalı (BuildPlan şeması). Hiçbir açıklama, markdown, 
kod bloğu yazma.

BuildPlan şeması:
{
  "phases": [
    {
      "id": "phase_id",
      "label": "Faz Etiketi",
      "parallel": false,
      "items": [
        {
          "key": "benzersiz_anahtar",
          "title": "Görev başlığı",
          "phase": "phase_id",
          "phase_label": "Faz Etiketi",
          "objective": "Ne yapılacak - somut ve test edilebilir",
          "detail": "Ek ayrıntı, kısıtlar, referanslar",
          "files": ["beklenen/dosya/yolu.tsx"],
          "acceptance": ["Kabul kriteri 1", "Kabul kriteri 2"],
          "verify_command": "npm run build",
          "depends_on": ["onceki_gorev_key"],
          "complexity": 3,
          "user_requested": false
        }
      ]
    }
  ]
}

Kurallar:
1. Fazları mantıksal sırayla düzenle: research → blueprint → planning → building → reviewing → polish
2. Aynı faz içindeki bağımsız görevler parallel=true, bağımlıysa false
3. depends_on: sadece aynı veya önceki fazdaki görevler
4. Her görev tek başına test edilebilir olmalı (verify_command veya files)
5. complexity: 1-5 arası (1=basit, 5=karmaşık)
6. user_requested: kullanıcı açıkça istediği özellikler için true
7. Türkçe etiketler, İngilizce key'ler
8. Asla "araştır" de, somut "dosya yaz / komut çalıştır" ver
9. Spesifikasyondaki HER özellik için en az bir kalem üret; özellik atlama.
10. Sadece iskelet yetmez: tasarım sistemi/token, tipografi, responsive düzen,
    mobil menü, bileşen kütüphanesi, yükleniyor/boş/hata durumları, mikro
    etkileşimler, erişilebilirlik, SEO, performans, 404, örnek veri, test ve
    dokümantasyon için ayrı kalemler ekle.
11. Her UI kaleminde somut sayfa/bileşen ve görsel kabul kriteri yaz.
"""


class PlanningAgent:
    """Generates BuildPlan from high-level goals."""
    
    def __init__(self, model: str = None):
        self.model = model or pick_model("planning")
        self.max_refinement_rounds = 2
        pipeline_cfg = getattr(settings, "BUILD_PIPELINE", {}) or {}
        self.min_items = int(pipeline_cfg.get("min_plan_items", 32))
    
    async def generate_plan(self, goal: str, context: Dict = None) -> BuildPlan:
        """Generate a complete BuildPlan from goal."""
        context = context or {}
        
        # Build context for prompt
        project_context = self._build_context(goal, context)
        
        # Round 1: Initial plan
        plan = await self._generate_initial_plan(goal, project_context)
        
        # Round 2-N: Refinement
        for round_num in range(self.max_refinement_rounds):
            refined = await self._refine_plan(plan, goal, project_context, round_num + 1)
            if refined is None or not refined.items:
                break
            if self._plan_equal(plan, refined):
                break
            plan = refined

        # LLM kisa plan dondurse bile spesifikasyon kapsamini ve tasarim
        # derinligini deterministik olarak tamamla.
        plan = ensure_plan_depth(plan, (context or {}).get("blueprint") or {})
        if len(plan.items) < self.min_items:
            logger.warning(
                "PlanningAgent plani hala kisa (%s < %s); blueprint derinligi kontrol edilmeli",
                len(plan.items), self.min_items,
            )
        return plan
    
    def _build_context(self, goal: str, context: Dict) -> str:
        parts = [f"HEDEF: {goal}"]
        
        if context.get("blueprint"):
            parts.append(f"\nSPESİFİKASYON:\n{format_blueprint(context['blueprint'], limit=9000)}")
        
        if context.get("dossier"):
            dossier = context["dossier"]
            if dossier.get("summary"):
                parts.append(f"\nARAŞTIRMA ÖZETİ:\n{dossier['summary'][:2000]}")
        
        if context.get("project_snapshot"):
            parts.append(f"\nPROJE DURUMU:\n{context['project_snapshot'][:2000]}")
        
        if context.get("existing_files"):
            parts.append(f"\nMEVCUT DOSYALAR:\n{', '.join(context['existing_files'][:50])}")
        
        return "\n".join(parts)
    
    async def _generate_initial_plan(self, goal: str, context: str) -> BuildPlan:
        prompt = f"""{PLANNING_SYSTEM_PROMPT}

{context}

Bu hedef için tam bir BuildPlan üret. Fazları ve görevleri mantıksal sırayla düzenle.
En az {self.min_items} kalem üret. Spesifikasyondaki her özellik en az bir kalemde
karşılanmalı; tasarım sistemi, responsive, durumlar, erişilebilirlik, SEO,
performans, test ve dokümantasyon kalemlerini atlama."""
        
        try:
            response = await llm_create_resilient(
                model=self.model,
                messages=[
                    {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=8192,
            )
            
            content = response.choices[0].message.content
            return self._parse_plan(content, goal)
            
        except Exception as e:
            logger.exception(f"PlanningAgent initial generation failed: {e}")
            return self._fallback_plan(goal)
    
    async def _refine_plan(self, plan: BuildPlan, goal: str, context: str, round_num: int) -> Optional[BuildPlan]:
        """Critique and improve the plan."""
        prompt = f"""Aşağıdaki planı eleştir ve iyileştir. Hataları düzelt, eksikleri tamamla.

MEVCUT PLAN:
{plan.to_json(indent=2)}

HEDEF: {goal}
BAĞLAM: {context[:2000]}

İyileştirme alanları:
1. Eksik görevler var mı? Plan en az {self.min_items} kalem olmalı.
2. Spesifikasyondaki her özellik bir kalemde karşılanıyor mu?
3. Tasarım sistemi, responsive, yükleniyor/boş/hata durumları, erişilebilirlik,
   SEO, performans, 404, örnek veri, test ve dokümantasyon kalemleri var mı?
4. Bağımlılıklar doğru mu?
5. Parallel/serial ayarları uygun mu?
6. Verify_command'lar çalışır mı?
7. Acceptance kriterleri test edilebilir mi?
8. Complexity değerleri gerçekçi mi?

Düzeltilmiş TAM PLANI JSON olarak ver."""
        
        try:
            response = await llm_create_resilient(
                model=self.model,
                messages=[
                    {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=8192,
            )
            
            content = response.choices[0].message.content
            return self._parse_plan(content, goal)
            
        except Exception as e:
            logger.warning(f"PlanningAgent refinement round {round_num} failed: {e}")
            return None
    
    def _parse_plan(self, content: str, goal: str = "") -> BuildPlan:
        """Parse LLM response into BuildPlan."""
        try:
            # Extract JSON from response
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content)
            plan = BuildPlan.from_dict(data)
            if not plan.items:
                return self._fallback_plan(goal)
            return plan
        except Exception as e:
            logger.exception(f"Failed to parse plan: {e}")
            return self._fallback_plan(goal)
    
    def _plan_equal(self, p1: BuildPlan, p2: BuildPlan) -> bool:
        return p1.to_json() == p2.to_json()
    
    def _fallback_plan(self, goal: str) -> BuildPlan:
        """Minimal fallback plan when generation fails."""
        objective = (goal or "").strip() or "Kullanici istegini uygula"
        return BuildPlan(items=[
            PlanItem(
                key="implement",
                order=1,
                title=f"Uygula: {objective[:50]}",
                phase="building",
                objective=objective,
                detail="Fallback plan - LLM plani alinamadi, istek dogrudan uygulanacak",
                files=[],
                acceptance=["Istek karsilanir", "Dosyalar olusturulur"],
                verify_command="",
                depends_on=[],
                complexity=3,
                user_requested=True,
            )
        ], meta={"fallback": True})
    
    async def refine_plan(self, plan: BuildPlan, feedback: str) -> BuildPlan:
        """Refine existing plan with user feedback."""
        prompt = f"""Mevcut planı kullanıcı geri bildirimiyle güncelle.

PLAN:
{plan.to_json(indent=2)}

GERİ BİLDİRİM: {feedback}

Güncellenmiş TAM PLANI JSON olarak ver."""
        
        try:
            response = await llm_create_resilient(
                model=self.model,
                messages=[
                    {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=8192,
            )
            return self._parse_plan(response.choices[0].message.content, feedback)
        except Exception as e:
            logger.exception(f"Plan refinement failed: {e}")
            return plan