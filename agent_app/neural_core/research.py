"""Bağımsız araştırma: mevcut arama altyapısını kullanır, ağ yoksa
deterministik sorgu planı ve özet döndürür.
"""
import logging

from . import config as neural_config

logger = logging.getLogger(__name__)


def build_queries(goal, limit=3):
    goal = (goal or "").strip()[:160]
    if not goal:
        return []
    templates = [
        f"{goal} nedir",
        f"{goal} en iyi uygulamalar",
        f"{goal} karşılaştırma ve örnekler",
    ]
    return templates[: max(1, int(limit))]


def _safe_search(query, count):
    from agent_app.buildkit.search import search

    return search(query, count=count)


def _summarize(goal, findings, offline):
    if offline or not findings:
        return (
            f"'{goal[:120]}' için çevrimdışı araştırma planı hazırlandı. "
            "Ağ veya arama sağlayıcısı kullanılamadığı için bulgular "
            "yalnızca yerel deneyim ve plan adımlarına dayanıyor."
        )
    titles = ", ".join(item.get("title", "")[:80] for item in findings[:3] if item.get("title"))
    return f"'{goal[:120]}' için {len(findings)} bulgu toplandı. Öne çıkanlar: {titles}."


def run(goal, queries=None, enabled=True, gateway=None):
    """Araştırma yürüt; her durumda JSON-uyumlu bir sözlük döndür."""
    cfg = neural_config.get_config()
    offline = neural_config.offline()
    queries = queries or build_queries(goal, cfg.get("MAX_RESEARCH_QUERIES", 3))

    if not enabled:
        return {
            "queries": queries,
            "findings": [],
            "summary": "Araştırma bu modda kapalı; yalnızca plan ve deneyim kullanıldı.",
            "offline": True,
            "disabled": True,
        }

    findings = []
    if not offline:
        per_query = int(cfg.get("RESEARCH_RESULTS", 6))
        for query in queries:
            try:
                response = _safe_search(query, per_query)
                results = list(getattr(response, "results", []) or [])
            except Exception as exc:  # ağ hatası çalışmayı durdurmaz
                logger.warning("NEURAL araştırma hatası (%s): %s", query, exc)
                results = []
            for item in results:
                if not isinstance(item, dict):
                    continue
                findings.append({
                    "title": str(item.get("title") or "")[:300],
                    "url": str(item.get("url") or "")[:1000],
                    "snippet": str(item.get("snippet") or "")[:600],
                    "provider": str(item.get("provider") or ""),
                    "query": query,
                })
            if findings:
                break

    effective_offline = offline or not findings
    summary = _summarize(goal, findings, effective_offline)
    if gateway is not None and findings and not offline:
        prompt = (
            "Aşağıdaki arama bulgularını 3-5 maddede sentezle. Kaynak uydurma, "
            "yalnızca verilen başlık ve özetleri kullan."
        )
        try:
            text = gateway.complete(
                prompt,
                "\n".join(f"- {f['title']}: {f['snippet']}" for f in findings[:8]),
                purpose="research",
            )
        except Exception:
            text = ""
        if text:
            summary = text[:4000]

    return {
        "queries": queries,
        "findings": findings[:30],
        "summary": summary,
        "offline": effective_offline,
    }
