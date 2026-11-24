"""Eleştirmen: plan gercekten yeterli mi, yoksa bosluk mu var?

Tur sonunda devreye girer:

  1. Mevcut projenin gercek halini topla (dosya agaci + ana dosyalarin ozeti)
  2. Spesifikasyondaki her ozelligi karsilik kaniti olup olmadigina bak
  3. LLM'e kaniti ver, "eksik olani yaz" de
  4. Eksik varsa yeni plan kalemleri uretir (pipeline bunlari yine yurutur)

Amac: "hic durmadan calisir" istegini kontrollu sekilde saglamak. Dongu,
yeni madde bulunmayan turda kendiliginden durur.
"""
import logging
from typing import Dict, List

from django.conf import settings

from . import llm
from .blueprint import format_for_prompt as format_blueprint
from .events import RunLogger
from .plan import BuildPlan, PlanItem

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


GAP_SYSTEM = """Sen sert bir teknik eleştirmensin ve denetim yapan mimar-sınıfısın.
Sana şunlar veriliyor:
- ürün spesifikasyonu,
- uygulama planı ve kalemlerin durumu,
- gerçekten yazılmış projenin dosya ağacı ve bazı dosyaların içeriği.

GÖREV: Spesifikasyonda vaat edilen her şey gerçekten var mı? Kod çalışır mı?
Kullanıcı bunu alıp gerçekten kullandığında ne eksik kalır?

KESİNLİKLE YAPMA:
- Planı tekrar özetleme.
- "İyi görünüyor", "iyi düşünülmüş" gibi gülle laf atma.
- Kod yazma, sadece EKSİK KALAN İŞLERİ tarif et.
- Emin olmadığın yeri eksik sayma; kanıt yoksa belirt.

JSON ŞEMA:
{
  "verdict": "complete | gaps",
  "coverage": 0-100,
  "blocking_issues": [{"issue": "...", "evidence": "...", "fix": "..."}],
  "missing_items": [
    {
      "title": "kısa fiil cümlesi",
      "phase": "frontend|backend|data|quality|hardening|delivery|polish|integrations|foundation|scaffold",
      "objective": "tek cümle",
      "detail": "ne yapılacak, 1-3 cümle",
      "files": ["beklenen dosya yolları"],
      "acceptance": ["doğrulanabilir kriter"],
      "complexity": 1-5
    }
  ],
  "notes": ["gözlem"]
}

`missing_items` en fazla 8 madde olmalı ve gerçekten eksik olanları içermeli.
Hiçbir şey eksik değilse `missing_items` boş ve `verdict` "complete" olsun."""

COVERAGE_SYSTEM = """Sen kalite denetçisisin. Sana ürün spesifikasyonu ve uygulama planının
durumu veriliyor. Her plan kalemi için, o kalemin gerçekten yerine gelip
gelmediğini "kanıt" alanına yaz.

Kanıt türleri:
- "dosya:<yol>" — kalemin beklenen dosyası diskte
- "eksik" — beklenen dosya yok
- "kontrol:<ad>" — kontrol edildi ama tutmadı
- "onay" — dosya eşleşmesi yok ama kalem tamamlanmış olarak işaretlenmiş (kabul et)

JSON ŞEMA:
{
  "items": [
    {"key": "<plan anahtarı>", "verdict": "done|partial|missing", "evidence": "kısa kanıt", "note": "..."}
  ]
}"""


class Critic:
    def __init__(self, run_logger: RunLogger, blueprint: Dict, dossier: Dict = None,
                 benchmark: Dict = None, model: str = None):
        self.log = run_logger
        self.blueprint = blueprint or {}
        self.dossier = dossier or {}
        self.benchmark = benchmark or {}
        self.model = model
        self.max_gap_items = int(_cfg("critic_gap_items", 8))

    # ------------------------------------------------------------------
    def review(self, plan: BuildPlan, round_index: int = 0) -> Dict:
        self.log.stage("critic", f"Eleştiri turu {round_index + 1} başlıyor")

        tree = self._project_tree()
        verification = self._verify_items(plan, tree)

        report = llm.as_dict(llm.ask_json(
            GAP_SYSTEM,
            self._gap_prompt(plan, tree, verification),
            model=self.model,
            purpose="research",
            temperature=0.3,
        ))

        missing = [m for m in (report.get("missing_items") or []) if isinstance(m, dict)]
        missing = self._dedupe_against_plan(missing, plan)

        verdict = str(report.get("verdict") or ("complete" if not missing else "gaps")).lower()
        coverage = _clamp_int(report.get("coverage"), 0, 100)

        result = {
            "round": round_index + 1,
            "verdict": verdict,
            "coverage": coverage,
            "blocking_issues": report.get("blocking_issues") or [],
            "missing_items": missing,
            "notes": report.get("notes") or [],
            "verification": verification,
            "project_tree": tree,
        }
        self.log.save("critic", result)
        self.log.status(
            f"Eleştiri sonucu: {verdict} | kapsam %{coverage} | "
            f"{len(report.get('blocking_issues') or [])} engelleyici sorun | "
            f"{len(missing)} yeni kalem önerildi"
        )
        self.log.stage("critic", f"Eleştiri turu {round_index + 1} bitti")
        return result

    # ------------------------------------------------------------------
    def _project_tree(self, max_chars: int = 7000) -> str:
        try:
            from agent_app.project_scanner import get_project_context, get_project_root

            root = get_project_root()
            if not root:
                return "(proje dizini yok)"
            return get_project_context(root, max_chars=max_chars)
        except Exception as exc:
            logger.debug("proje agaci alinamadi: %s", exc)
            return "(proje agaci okunamadi)"

    def _verify_items(self, plan: BuildPlan, tree: str) -> List[Dict]:
        """Ucuz, deterministik kanit toplama (LLM'e sormadan)."""
        rows = []
        for item in plan.items:
            expected = [f for f in item.files if "*" not in f]
            if expected:
                present = [f for f in expected if f in tree]
                verdict = "done" if present else "missing"
                evidence = f"dosya:{', '.join(present[:4])}" if present else "eksik"
            else:
                verdict = {"done": "done", "failed": "missing"}.get(item.status, "partial")
                evidence = "onay"
            rows.append({"key": item.key, "verdict": verdict, "evidence": evidence,
                         "title": item.title, "status": item.status})
        return rows

    # ------------------------------------------------------------------
    def _gap_prompt(self, plan: BuildPlan, tree: str, verification: List[Dict]) -> str:
        done = len(plan.done())
        failed = len(plan.failed())
        skipped = len(plan.skipped())
        evidence_rows = "\n".join(
            f"- {r['key']} [{r['status']}] -> {r['verdict']} ({r['evidence']})"
            for r in verification
            if r["status"] != "done" or r["verdict"] != "done"
        ) or "- (hepsi tutarlı)"

        return (
            f"=== SPESİFİKASYON ===\n{format_blueprint(self.blueprint, limit=5000)}\n\n"
            f"=== PLAN ÖZETİ ===\n"
            f"toplam={len(plan)} tamamlanan={done} başarısız={failed} atlanan={skipped}\n\n"
            f"{plan.format_for_prompt(include_done=True, limit=3000)}\n\n"
            f"=== KALEM KANITLARI (uyumsuz olanlar) ===\n{evidence_rows}\n\n"
            f"=== PROJENİN ŞU ANKİ GERÇEK HALİ (dosya ağacı + içerik) ===\n{tree}\n\n"
            "Eksikleri bul ve yalnızca yeni iş kalemleri olarak bildir."
        )

    # ------------------------------------------------------------------
    def _dedupe_against_plan(self, missing: List[Dict], plan: BuildPlan) -> List[Dict]:
        """Planda zaten var olan kalemleri tekrarlama."""
        from .plan import _match_key

        seen = []
        out = []
        for entry in missing[: self.max_gap_items * 2]:
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            if _match_key(title, plan.items):
                continue
            if any(_similar(title, existing) for existing in seen):
                continue
            seen.append(title)
            out.append(entry)
            if len(out) >= self.max_gap_items:
                break
        return out

    # ------------------------------------------------------------------
    def to_items(self, missing: List[Dict]) -> List[Dict]:
        """LLM ciktisini plan kalemine normalize et."""
        from .plan import PHASE_ORDER

        rows = []
        for entry in missing:
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            phase = str(entry.get("phase") or "polish").lower()
            if phase not in PHASE_ORDER:
                phase = "polish"
            rows.append({
                "title": title,
                "phase": phase,
                "objective": str(entry.get("objective") or "").strip() or title,
                "detail": str(entry.get("detail") or "").strip(),
                "files": [str(f).strip().lstrip("/") for f in (entry.get("files") or []) if str(f).strip()],
                "acceptance": [str(a).strip() for a in (entry.get("acceptance") or []) if str(a).strip()],
                "complexity": _clamp_int(entry.get("complexity"), 1, 5),
            })
        return rows


# ----------------------------------------------------------------------
def _similar(a: str, b: str) -> bool:
    from .plan import _slug

    na, nb = _slug(a, 0), _slug(b, 0)
    if not na or not nb:
        return False
    return na == nb or (len(na) > 8 and len(nb) > 8 and (na in nb or nb in na))


def _clamp_int(value, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low