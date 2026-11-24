"""Öğrenen (şablonsuz) bilgi modelleri: araştırma + sentez + kalıcı kayıt.

- 3D varlık/sınıf bilgisi: her zaman internet araştırması yapılır, bottom-up
  hiyerarşi ve özellikler sentezlenir, LearnedEntityKnowledge ile birleştirilir.
- Video bilgisi: başarılı üretimlerden öğrenilen profil ayrı LearnedVideoKnowledge'da tutulur.

Not: "Model" burada uygulama-içi öğrenen bilgi deposudur (ML ağırlık fine-tune değil).
"""

from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

logger = logging.getLogger(__name__)


# =========================
# Genel yardımcılar
# =========================

def _now():
    return timezone.now()


def _normalize_concept(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^0-9a-zA-ZçğıöşüÇĞİÖŞÜ _\-]", "", s)
    return s[:220]


def _safe_json_loads(text: str):
    if not text:
        return None
    t = text.strip()
    # try direct
    try:
        return json.loads(t)
    except Exception:
        pass
    # try to extract first JSON object/array
    for opener, closer in [("{", "}"), ("[", "]")]:
        i = t.find(opener)
        j = t.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except Exception:
                continue
    return None


def _merge_deep(base: Any, incoming: Any) -> Any:
    """Derin birleştirme (dict'ler merge, list'ler benzersiz/eklemeli, diğerleri override)."""
    if isinstance(base, dict) and isinstance(incoming, dict):
        out = dict(base)
        for k, v in incoming.items():
            if k in out:
                out[k] = _merge_deep(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    if isinstance(base, list) and isinstance(incoming, list):
        out = list(base)
        for item in incoming:
            if item not in out:
                out.append(copy.deepcopy(item))
        return out
    return copy.deepcopy(incoming)


# =========================
# 3D Varlık: araştırma & sentez
# =========================

DEFAULT_ENTITY_SPEC = {
    "category": "other",
    "description": "",
    "hierarchy": {
        "type": "bottom_up",
        "layers": [
            {"level": 0, "name": "fundamental", "description": "En temel malzeme/alt bileşenler", "elements": []},
            {"level": 1, "name": "components", "description": "Alt parçalar/bileşenler", "elements": []},
            {"level": 2, "name": "parts", "description": "Parçalar/modüller", "elements": []},
            {"level": 3, "name": "assembly", "description": "Montaj/alt sistemler", "elements": []},
            {"level": 4, "name": "whole", "description": "Bütün varlık", "elements": []},
        ],
    },
    "features": {
        "identity": [],
        "physical": [],
        "functional": [],
        "visual": [],
        "behavioral": [],
    },
    "components_template": {
        "identity": {"label": "", "locked_fields": []},
        "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
        "mesh": {"asset_id": None, "primitive": None, "parts": []},
        "skeleton": {"root": None, "bones": []},
        "animation": {"clips": [], "active": None, "pose": {}, "capabilities": []},
        "physics": {"enabled": True, "body": "dynamic", "mass": 1.0, "collider": "box", "materials": []},
        "render": {"visible": True, "material": {"color": [0.8, 0.8, 0.85, 1.0]}},
    },
    "skeleton_template": {"root": None, "bones": [], "hierarchy": []},
    "render_template": {"type": "auto", "parts": []},
    "parameters": {},
    "sources": [],
    "confidence": 0.0,
}


def _build_research_queries(concept: str, category: str = "") -> List[str]:
    c = (concept or "").strip()
    cat = (category or "").strip()
    base = c if not cat else f"{c} {cat}"
    # Always-on, bottom-up oriented queries (genel)
    return [
        f"{base} temel bileşenleri malzemesi en küçük yapı taşları",
        f"{base} iç yapısı parçaları katmanları anatomisi hiyerarşi",
        f"{base} fiziksel özellikleri boyut kütle malzeme mekanik",
        f"{base} hareket animasyon fonksiyon davranış yetenekleri",
        f"{base} 3D modelleme parçalar iskelet rigging bileşenler",
    ]


BOTTOM_UP_LAYER_PLAN = [
    {"level": 0, "name": "fundamental", "description": "En temel seviye: kavrama uygun en küçük yapı taşı (canlılarda kan/hücre/molekül; cansızlarda malzeme/element/atomik-alt bileşen)", "queries": [
        "{base} en temel yapı taşı nedir en küçük birim",
        "{base} biyolojik ise kan hücre molekül; cansız ise malzeme element atomik bileşen",
    ]},
    {"level": 1, "name": "micro_components", "description": "Mikro/alt bileşenler ve temel yapı elemanları (hücre grupları, kristal/tanecik, alt malzeme birimleri)", "queries": [
        "{base} alt bileşenleri temel elemanları mikroskobik yapı",
        "{base} temel yapı taşlarından oluşan alt birimler neler",
    ]},
    {"level": 2, "name": "components", "description": "Bileşenler/doku veya alt parçalar", "queries": [
        "{base} bileşenleri parçaları dokuları alt yapılar",
        "{base} iç yapısı bölümleri bileşen hiyerarşisi",
    ]},
    {"level": 3, "name": "parts", "description": "Parçalar/organ veya modüller", "queries": [
        "{base} ana parçaları organları modülleri ve işlevleri",
        "{base} parça listesi yapısal birimler",
    ]},
    {"level": 4, "name": "assembly", "description": "Montaj/alt sistemler", "queries": [
        "{base} sistemleri montajı alt sistemler nasıl birleşir",
        "{base} parçaların birleşimi etkileşimleri",
    ]},
    {"level": 5, "name": "whole", "description": "Bütün varlık ve davranış", "queries": [
        "{base} bütün yapı fiziksel özellikler davranış hareket",
        "{base} 3D özellikleri işlevleri etkileşimleri",
    ]},
]


def _build_layer_queries(concept: str, category: str, layer: Dict[str, Any], prior_layers: List[Dict[str, Any]]) -> List[str]:
    c = (concept or "").strip()
    cat = (category or "").strip()
    base = c if not cat else f"{c} {cat}"
    out = []
    for q in (layer.get("queries") or []):
        try:
            out.append(q.format(base=base, concept=c, category=cat))
        except Exception:
            out.append(f"{base} {q}")
    # domain-aware reinforcement
    hints = _infer_domain_hints(c, cat)
    if layer.get("name") == "fundamental":
        if hints.get("domain") == "biological":
            out.append(f"{base} kan hücre molekül en temel yapı taşları")
        elif hints.get("domain") == "mechanical":
            out.append(f"{base} malzeme element alaşım en temel bileşenler")
    # add context from previous layer elements
    if prior_layers:
        prev = prior_layers[-1]
        elems = []
        for e in (prev.get("elements") or [])[:3]:
            if isinstance(e, dict) and e.get("name"):
                elems.append(str(e.get("name")))
            elif isinstance(e, str):
                elems.append(e)
        if elems:
            out.append(f"{base} {', '.join(elems)} üzerine kurulu sonraki seviye {layer.get('name','')} yapısı")
    # dedupe preserve order
    seen = set()
    uniq = []
    for q in out:
        qn = (q or "").strip()
        if not qn or qn in seen:
            continue
        seen.add(qn)
        uniq.append(qn)
    return uniq[:4]


def _synthesis_prompt(concept: str, category: str, findings: List[Dict[str, Any]]) -> Tuple[str, str]:
    system = (
        "Sen bir 3D varlık/sınıf ontolojisi çıkarım uzmanısın. "
        "Verilen kavram için hazır şablon kullanma; araştırma bulgularına ve genel bilgiye dayanarak "
        "EN TEMEL seviyeden başlayıp yukarı doğru (bottom-up) hiyerarşik bir yapı çıkar. "
        "Biyolojik varlıklarda hücre/kan/doku/organ/sistem/organizma gibi katmanları; "
        "nesnelerde malzeme/alt bileşen/parça/montaj/bütün katmanlarını kullan. "
        "SADECE geçerli JSON döndür."
    )
    evidence = []
    for f in (findings or [])[:12]:
        evidence.append({
            "title": f.get("title", ""),
            "snippet": f.get("snippet", ""),
            "url": f.get("url", ""),
            "query": f.get("query", ""),
        })
    user = json.dumps({
        "concept": concept,
        "category_hint": category,
        "requirements": {
            "bottom_up": True,
            "always_research_used": True,
            "output": "json",
            "must_include": [
                "category", "description", "hierarchy.layers (level 0 en temelden yukarı)",
                "features (identity/physical/functional/visual/behavioral)",
                "components_template (identity,transform,mesh,skeleton,animation,physics,render)",
                "skeleton_template (bones/hierarchy, uygunsa)",
                "render_template (parts/primitives ipuçları)",
                "parameters (değişken nitelikler)",
                "confidence (0-1)"
            ],
        },
        "research_findings": evidence,
        "schema_example": DEFAULT_ENTITY_SPEC,
        "instruction": (
            f"'{concept}' kavramını bottom-up modellemek için JSON üret. "
            "hierarchy.layers içinde level 0 en temel (kan/malzeme/atomik-alt bileşen benzeri) olsun ve yukarı çıksın. "
            "Her layer için name, description, elements (isim/rol/özellik) ver. "
            "Uygun değilse bones boş olabilir ama yapı yine de bottom-up olmalı."
        ),
    }, ensure_ascii=False)
    return system, user


def _layer_synthesis_prompt(concept: str, category: str, layer: Dict[str, Any], prior_layers: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> Tuple[str, str]:
    system = (
        "Sen bottom-up varlık modelleme uzmanısın. Sadece istenen katman için JSON üret. "
        "EN TEMELDEN yukarı doğru sırayla ilerleniyor; bu katmanı önceki alt katmanların üzerine kur. "
        "Hazır şablon kullanma, araştırma bulgularına dayan. SADECE JSON döndür. "
        "Fundamental/mikro seviyelerde kavrama uygun en küçük somut birimleri (örn. canlı: kan/hücre/molekül; cansız: malzeme/element/alt bileşen) belirt."
    )
    evidence = [{"title": f.get("title", ""), "snippet": f.get("snippet", ""), "url": f.get("url", "")} for f in (findings or [])[:8]]
    user = json.dumps({
        "concept": concept,
        "category_hint": category,
        "target_layer": {
            "level": layer.get("level"),
            "name": layer.get("name"),
            "description": layer.get("description"),
        },
        "previous_layers_bottom_up": prior_layers,
        "research_findings": evidence,
        "output_schema": {
            "layer": {"level": layer.get("level"), "name": layer.get("name"), "description": layer.get("description"), "elements": [{"name": "", "role": "", "properties": [], "notes": ""}]},
            "layer_features": {"identity": [], "physical": [], "functional": [], "visual": [], "behavioral": []},
            "confidence": 0.0
        },
        "instruction": "Bu katmanın elements listesini somut ve araştırmaya dayalı doldur (en az 1 öğe). Temel katmanlarda soyutlama yerine gerçek alt yapı taşlarını adlandır.",
    }, ensure_ascii=False)
    return system, user


def _ensure_layer_structure(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    hier = spec.setdefault("hierarchy", {})
    hier.setdefault("type", "bottom_up")
    layers = hier.get("layers")
    if not isinstance(layers, list) or not layers:
        layers = copy.deepcopy(DEFAULT_ENTITY_SPEC["hierarchy"]["layers"])
        hier["layers"] = layers
    # ensure plan layers exist/order
    by_level = {int(l.get("level", i)): l for i, l in enumerate(layers) if isinstance(l, dict)}
    ordered = []
    for plan in BOTTOM_UP_LAYER_PLAN:
        lvl = plan["level"]
        cur = by_level.get(lvl) or {}
        merged = {**plan, **{k: v for k, v in cur.items() if k not in ("queries",)}, "level": lvl}
        merged.setdefault("elements", [])
        ordered.append(merged)
    hier["layers"] = ordered
    return ordered


def _merge_layer_result(spec: Dict[str, Any], layer_result: Any, layer_plan: Dict[str, Any], findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    spec = copy.deepcopy(spec or {})
    layers = _ensure_layer_structure(spec)
    lvl = layer_plan["level"]
    target = layers[lvl] if lvl < len(layers) else None
    if target is None:
        return spec
    # extract produced layer
    produced = None
    if isinstance(layer_result, dict):
        produced = layer_result.get("layer") if isinstance(layer_result.get("layer"), dict) else layer_result
    if isinstance(produced, dict):
        elems = produced.get("elements")
        if isinstance(elems, list) and elems:
            target["elements"] = elems
        if produced.get("description"):
            target["description"] = produced.get("description")
        if produced.get("name"):
            target["name"] = produced.get("name")
    # heuristic fallback if empty
    if not target.get("elements"):
        terms = []
        for f in findings[:5]:
            text = f"{f.get('title','')} {f.get('snippet','')}".lower()
            for w in re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ0-9]{3,}", text):
                if w not in terms and len(terms) < 6:
                    terms.append(w)
        target["elements"] = [{"name": t, "role": layer_plan.get("name", "element"), "properties": []} for t in (terms or [layer_plan.get("name", "element")])]
    # merge layer features
    if isinstance(layer_result, dict) and isinstance(layer_result.get("layer_features"), dict):
        spec["features"] = _merge_deep(spec.get("features") or {}, layer_result["layer_features"])
    # update mesh parts/render hints from upper layers
    ct = spec.setdefault("components_template", {})
    mesh = ct.setdefault("mesh", {})
    parts = []
    for l in layers:
        for e in (l.get("elements") or []):
            n = e.get("name") if isinstance(e, dict) else e
            if n and n not in parts:
                parts.append(n)
    mesh["parts"] = parts[:40]
    spec.setdefault("render_template", {})["parts"] = [{"name": p} for p in parts[:24]]
    spec.setdefault("skeleton_template", {})["hierarchy"] = spec.get("hierarchy")
    ct["skeleton"] = copy.deepcopy(spec.get("skeleton_template"))
    # confidence bump per researched layer
    try:
        base_c = float(spec.get("confidence") or 0.0)
    except Exception:
        base_c = 0.0
    inc = 0.0
    if isinstance(layer_result, dict):
        try:
            inc = float(layer_result.get("confidence") or 0.0) * 0.15
        except Exception:
            inc = 0.0
    spec["confidence"] = max(base_c, min(1.0, base_c + 0.08 + inc + (0.03 if findings else 0.0)))
    return spec


def _infer_domain_hints(concept: str, category: str = "") -> Dict[str, Any]:
    text = f"{concept} {category}".lower()
    bio_terms = ["insan", "human", "hayvan", "animal", "canlı", "creature", "bitki", "plant", "organizma", "vücut", "beden", "kuş", "balık", "memeli"]
    mech_terms = ["araba", "araç", "vehicle", "makine", "machine", "robot", "silah", "weapon", "kılıç", "sword", "motor", "engine", "cihaz", "device"]
    if any(t in text for t in bio_terms) or category in ("human", "character", "creature"):
        return {
            "domain": "biological",
            "fundamental": ["hücre", "kan", "molekül", "protein", "su"],
            "micro": ["doku", "hücre grupları", "sinir hücreleri"],
            "components": ["doku tabakaları", "damarlar", "bağ dokusu"],
            "parts": ["organlar", "uzuvlar"],
            "assembly": ["sistemler (iskelet, kas, dolaşım, sinir)"],
        }
    if any(t in text for t in mech_terms) or category in ("weapon", "vehicle", "prop"):
        return {
            "domain": "mechanical",
            "fundamental": ["malzeme", "metal/polimer", "element", "alaşım bileşenleri"],
            "micro": ["tanecik/yapı birimleri", "kristal/tahıl yapısı"],
            "components": ["alt parçalar", "bağlantı elemanları"],
            "parts": ["modüller", "ana parçalar"],
            "assembly": ["alt sistemler", "montaj grupları"],
        }
    return {
        "domain": "generic",
        "fundamental": ["temel malzeme", "en küçük bileşen", "yapı taşı"],
        "micro": ["alt bileşenler", "mikro yapı elemanları"],
        "components": ["bileşenler", "alt parçalar"],
        "parts": ["parçalar", "modüller"],
        "assembly": ["montaj", "alt sistemler"],
    }


def _heuristic_spec(concept: str, category: str = "", findings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """LLM yoksa araştırma bulgularından asgari bottom-up spec üret (şablonsuz, dinamik)."""
    c = (concept or "").strip() or "Varlık"
    cat = (category or "other").strip() or "other"
    hints = _infer_domain_hints(c, cat)
    spec = copy.deepcopy(DEFAULT_ENTITY_SPEC)
    spec["category"] = cat
    spec["description"] = f"{c} için araştırma tabanlı (dinamik) öğrenilmiş tanım ({hints.get('domain')})."
    spec["hierarchy"]["layers"][0]["elements"] = [{"name": n, "role": "fundamental", "properties": []} for n in hints.get("fundamental", [])[:6]]
    spec["hierarchy"]["layers"][1]["elements"] = [{"name": n, "role": "micro_component", "properties": []} for n in hints.get("micro", [])[:6]]
    spec["hierarchy"]["layers"][2]["elements"] = [{"name": n, "role": "component", "properties": []} for n in hints.get("components", [])[:6]]
    spec["hierarchy"]["layers"][3]["elements"] = [{"name": n, "role": "part", "properties": []} for n in hints.get("parts", [])[:6]]
    spec["hierarchy"]["layers"][4]["elements"] = [{"name": n, "role": "assembly", "properties": []} for n in hints.get("assembly", [])[:4]] + [{"name": c, "role": "whole_entity", "properties": []}]
    spec["components_template"]["identity"]["label"] = c
    spec["components_template"]["mesh"]["parts"] = [e["name"] for e in spec["hierarchy"]["layers"][3]["elements"]]
    spec["parameters"] = {"scale": 1.0, "variant": "default"}
    if findings:
        spec["sources"] = [{"title": f.get("title", ""), "url": f.get("url", "")} for f in findings[:10] if f.get("url") or f.get("title")]
        spec["confidence"] = 0.35
        terms = []
        for f in findings[:8]:
            text = f"{f.get('title','')} {f.get('snippet','')}".lower()
            for w in re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]{4,}", text):
                if w not in terms and len(terms) < 20:
                    terms.append(w)
        if terms:
            spec["hierarchy"]["layers"][2]["elements"] = [{"name": t, "role": "inferred_component", "properties": []} for t in terms[:6]]
            spec["hierarchy"]["layers"][3]["elements"] = [{"name": t, "role": "inferred_part", "properties": []} for t in terms[6:12]] or spec["hierarchy"]["layers"][3]["elements"]
    else:
        spec["confidence"] = 0.15
    return spec


def _normalize_spec(spec: Any, concept: str, category: str = "") -> Dict[str, Any]:
    if not isinstance(spec, dict):
        spec = {}
    out = copy.deepcopy(DEFAULT_ENTITY_SPEC)
    # merge known keys
    for k in ("category", "description", "hierarchy", "features", "components_template", "skeleton_template", "render_template", "parameters", "confidence"):
        if k in spec:
            if isinstance(out.get(k), dict) and isinstance(spec.get(k), dict):
                out[k] = _merge_deep(out[k], spec[k])
            else:
                out[k] = copy.deepcopy(spec[k])
    if not out.get("category") or out.get("category") == "other":
        out["category"] = (category or spec.get("category") or "other")
    if not out.get("description"):
        out["description"] = str(spec.get("description") or f"{concept} için araştırma ile öğrenilmiş dinamik tanım.")
    # ensure identity label
    ct = out.setdefault("components_template", {})
    ident = ct.setdefault("identity", {})
    if not ident.get("label"):
        ident["label"] = concept
    ident.setdefault("locked_fields", [])
    # ensure hierarchy bottom-up marker
    hier = out.setdefault("hierarchy", {})
    hier.setdefault("type", "bottom_up")
    if not isinstance(hier.get("layers"), list) or not hier.get("layers"):
        hier["layers"] = copy.deepcopy(DEFAULT_ENTITY_SPEC["hierarchy"]["layers"])
    # confidence clamp
    try:
        out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0.0) or 0.0)))
    except Exception:
        out["confidence"] = 0.0
    return out


def _get_or_init_learned_knowledge(concept: str, category: str, norm: str, user):
    from .models import LearnedEntityKnowledge
    qs = LearnedEntityKnowledge.objects.filter(normalized_concept=norm)
    if user is not None:
        obj = qs.filter(user=user).order_by("-updated_at").first() or qs.filter(user__isnull=True).order_by("-updated_at").first()
    else:
        obj = qs.filter(user__isnull=True).order_by("-updated_at").first() or qs.order_by("-updated_at").first()
    if obj is None:
        base_spec = _normalize_spec(copy.deepcopy(DEFAULT_ENTITY_SPEC), concept, category)
        _ensure_layer_structure(base_spec)
        obj = LearnedEntityKnowledge(
            user=user,
            concept=concept,
            normalized_concept=norm,
            category=base_spec.get("category") or category,
            hierarchy=base_spec.get("hierarchy") or {},
            features=base_spec.get("features") or {},
            sources=[],
            research_summary="",
            research_history=[],
            confidence=float(base_spec.get("confidence") or 0.0),
            research_count=0,
            last_researched_at=None,
            learned_spec=base_spec,
        )
        obj.save()
    return obj


def _save_learned_progress(obj, concept: str, category: str, spec: Dict[str, Any], history_entry: Dict[str, Any], new_sources: List[Dict[str, Any]], research_summary: str, bump_research_count: bool = False):
    spec = _normalize_spec(spec or {}, concept, category)
    obj.concept = concept or obj.concept
    obj.normalized_concept = _normalize_concept(concept) or obj.normalized_concept
    obj.category = spec.get("category") or obj.category or category
    obj.hierarchy = spec.get("hierarchy") or {}
    obj.features = spec.get("features") or {}
    # merge sources
    prev_src = obj.sources if isinstance(obj.sources, list) else []
    merged_src = list(prev_src)
    seen = {s.get("url") for s in merged_src if isinstance(s, dict)}
    for s in (new_sources or []):
        if not isinstance(s, dict):
            continue
        url = s.get("url")
        if url and url in seen:
            continue
        merged_src.append(s)
        if url:
            seen.add(url)
    obj.sources = merged_src[-80:]
    if research_summary:
        obj.research_summary = research_summary
    hist = obj.research_history if isinstance(obj.research_history, list) else []
    if history_entry:
        hist.append(history_entry)
    obj.research_history = hist[-120:]
    try:
        obj.confidence = max(float(obj.confidence or 0.0), float(spec.get("confidence") or 0.0))
    except Exception:
        obj.confidence = float(spec.get("confidence") or 0.0)
    if bump_research_count:
        obj.research_count = int(obj.research_count or 0) + 1
    obj.last_researched_at = _now()
    obj.learned_spec = spec
    obj.save()
    return spec


def research_and_learn_entity(concept: str, category: str = "", user=None, context: Optional[dict] = None) -> Dict[str, Any]:
    """Her zaman internet araştırması yapar; EN DİPTEN yukarıya katman katman araştırır ve her adımı öğrenen modele ekler."""
    from .neural_core import research as neural_research
    from .neural_core.gateway import ModelGateway

    concept = (concept or "").strip()
    if not concept:
        raise ValueError("concept gerekli")
    category = (category or "").strip() or "other"
    norm = _normalize_concept(concept)
    context = context or {}

    gateway = ModelGateway()
    obj = _get_or_init_learned_knowledge(concept, category, norm, user)

    # mevcut öğrenilmiş spec'i temel al (şablonsuz büyüme)
    spec = _normalize_spec(obj.learned_spec if isinstance(obj.learned_spec, dict) else {}, concept, category)
    _ensure_layer_structure(spec)

    all_queries = []
    all_findings = []
    summaries = []
    any_offline = False

    # 1) ITERATİF BOTTOM-UP: her katman için ayrı internet araştırması + sentez + anlık kayıt
    for plan in BOTTOM_UP_LAYER_PLAN:
        prior_layers = [l for l in spec.get("hierarchy", {}).get("layers", []) if isinstance(l, dict) and int(l.get("level", 0)) < plan["level"]]
        layer_queries = _build_layer_queries(concept, category, plan, prior_layers)
        try:
            rr = neural_research.run(
                goal=f"{concept} ({category}) - bottom-up katman {plan['level']}:{plan['name']} araştırması",
                queries=layer_queries,
                enabled=True,  # her zaman internet araştırması
                gateway=gateway,
            )
        except Exception as exc:
            logger.warning("Katman araştırma hatası (%s L%s): %s", concept, plan["level"], exc)
            rr = {"queries": layer_queries, "findings": [], "summary": "", "offline": True}

        findings = list(rr.get("findings") or [])
        summary = str(rr.get("summary") or "")
        offline = bool(rr.get("offline"))
        any_offline = any_offline or offline
        all_queries.extend(layer_queries)
        all_findings.extend(findings)
        if summary:
            summaries.append(f"L{plan['level']} {plan['name']}: {summary}")

        # katman sentezi (LLM varsa bulgularla, yoksa sezgisel ama araştırma bulgularını kullanarak)
        layer_result = None
        if findings or gateway.active_provider():
            try:
                sys_p, usr_p = _layer_synthesis_prompt(concept, category, plan, prior_layers, findings)
                text = gateway.complete(sys_p, usr_p, purpose="research", temperature=0.2)
                layer_result = _safe_json_loads(text)
            except Exception as exc:
                logger.warning("Katman sentez hatası (%s L%s): %s", concept, plan["level"], exc)
                layer_result = None

        spec = _merge_layer_result(spec, layer_result, plan, findings)
        spec = _normalize_spec(spec, concept, category)

        srcs = [{"title": f.get("title", ""), "url": f.get("url", ""), "query": f.get("query", ""), "layer": plan["level"]} for f in findings[:10]]
        history_entry = {
            "at": _now().isoformat(),
            "phase": "layer_research",
            "layer": plan["level"],
            "layer_name": plan["name"],
            "queries": layer_queries,
            "findings_count": len(findings),
            "offline": offline,
            "summary": summary[:800],
            "confidence": spec.get("confidence", 0.0),
        }
        # HER araştırılan katmanı hemen öğrenen modele ekle
        spec = _save_learned_progress(obj, concept, category, spec, history_entry, srcs, (summary or obj.research_summary or ""), bump_research_count=False)

    # 2) Bütüncül sentez (tüm bulgular + birikmiş katmanlarla) ve son kayıt
    holistic = None
    if all_findings or gateway.active_provider():
        try:
            # mevcut birikmiş hiyerarşiyi bağlam olarak ver
            holistic_context_findings = list(all_findings[:20])
            holistic_context_findings.append({
                "title": "Birikmiş bottom-up hiyerarşi",
                "snippet": json.dumps(spec.get("hierarchy") or {}, ensure_ascii=False)[:1200],
                "url": "",
                "query": "iterative-bottom-up",
            })
            sys_p, usr_p = _synthesis_prompt(concept, category, holistic_context_findings)
            text = gateway.complete(sys_p, usr_p, purpose="research", temperature=0.2)
            holistic = _safe_json_loads(text)
        except Exception as exc:
            logger.warning("Bütüncül sentez hatası (%s): %s", concept, exc)
            holistic = None

    if isinstance(holistic, dict):
        spec = _merge_deep(spec, holistic)
    spec = _normalize_spec(spec, concept, category)
    _ensure_layer_structure(spec)
    final_summary = "\n".join(summaries[-6:])[:2000] if summaries else str(spec.get("description") or "")
    final_history = {
        "at": _now().isoformat(),
        "phase": "holistic_merge",
        "queries": all_queries,
        "findings_count": len(all_findings),
        "offline": any_offline,
        "summary": final_summary[:1000],
        "confidence": spec.get("confidence", 0.0),
        "layers_researched": [p["level"] for p in BOTTOM_UP_LAYER_PLAN],
    }
    spec = _save_learned_progress(obj, concept, category, spec, final_history, [], final_summary, bump_research_count=True)

    # dedupe findings for return
    uniq_findings = []
    seen_urls = set()
    for f in all_findings:
        u = f.get("url") if isinstance(f, dict) else None
        if u and u in seen_urls:
            continue
        if u:
            seen_urls.add(u)
        uniq_findings.append(f)

    return {
        "knowledge": obj,
        "spec": spec,
        "research": {
            "queries": all_queries,
            "findings": uniq_findings[:40],
            "summary": final_summary,
            "offline": any_offline,
            "always_researched": True,
            "iterative_bottom_up": True,
            "layers_researched": [p["level"] for p in BOTTOM_UP_LAYER_PLAN],
        },
    }


# =========================
# EntityClass sentezi / uygulama
# =========================

def spec_to_entity_class_fields(spec: Dict[str, Any], concept: str) -> Dict[str, Any]:
    spec = _normalize_spec(spec or {}, concept)
    hierarchy = spec.get("hierarchy") or {}
    skeleton_template = spec.get("skeleton_template") or {"root": None, "bones": [], "hierarchy": []}
    # attach hierarchy to skeleton_template for bottom-up traceability
    if isinstance(skeleton_template, dict):
        skeleton_template = {**skeleton_template, "hierarchy": hierarchy}
    components_template = copy.deepcopy(spec.get("components_template") or {})
    # ensure skeleton embedded/consistent
    components_template["skeleton"] = copy.deepcopy(skeleton_template)
    # identity
    ident = components_template.setdefault("identity", {})
    ident.setdefault("label", concept)
    ident.setdefault("locked_fields", [])
    return {
        "name": concept[:200],
        "category": (spec.get("category") or "other"),
        "description": (spec.get("description") or "")[:5000],
        "components_template": components_template,
        "skeleton_template": skeleton_template,
        "render_template": copy.deepcopy(spec.get("render_template") or {"type": "auto", "parts": []}),
        "parameters": copy.deepcopy(spec.get("parameters") or {}),
    }


def apply_learned_spec_to_entity_class(entity_class, spec: Dict[str, Any], concept: Optional[str] = None) -> Any:
    """Araştırma ile öğrenilmiş spec'i EntityClass'a uygular (şablonsuz/dinamik)."""
    name = concept or getattr(entity_class, "name", "") or "Varlık"
    fields = spec_to_entity_class_fields(spec, name)
    entity_class.name = fields["name"]
    entity_class.category = fields["category"]
    entity_class.description = fields["description"]
    entity_class.components_template = fields["components_template"]
    entity_class.skeleton_template = fields["skeleton_template"]
    entity_class.render_template = fields["render_template"]
    entity_class.parameters = fields["parameters"]
    # learned, not system template
    if hasattr(entity_class, "is_system"):
        # user/learned classes must not be treated as fixed system templates
        entity_class.is_system = bool(getattr(entity_class, "user_id", None) is None and getattr(entity_class, "is_system", False))
    entity_class.version = int(getattr(entity_class, "version", 1) or 1) + 1
    entity_class.save()
    return entity_class


def research_and_apply_entity_class(entity_class, user=None, context: Optional[dict] = None) -> Dict[str, Any]:
    concept = getattr(entity_class, "name", "") or ""
    category = getattr(entity_class, "category", "") or "other"
    learned = research_and_learn_entity(concept, category=category, user=user, context=context)
    spec = learned["spec"]
    apply_learned_spec_to_entity_class(entity_class, spec, concept=concept)
    # link knowledge -> class
    knowledge = learned["knowledge"]
    try:
        knowledge.entity_class = entity_class
        if not knowledge.category:
            knowledge.category = entity_class.category
        knowledge.save(update_fields=["entity_class", "category", "updated_at"])
    except Exception:
        pass
    return {
        "entity_class": entity_class,
        "knowledge": knowledge,
        "spec": spec,
        "research": learned.get("research") or {},
    }


# =========================
# Video: başarılı üretimden öğrenme
# =========================

def _extract_video_insights(job) -> Dict[str, Any]:
    meta = getattr(job, "meta", None) or {}
    prompt = getattr(job, "prompt", "") or ""
    provider = getattr(job, "provider", "") or ""
    fmt = getattr(job, "format", "") or ""
    w = int(getattr(job, "width", 0) or 0)
    h = int(getattr(job, "height", 0) or 0)
    dur = int(getattr(job, "duration", 0) or 0)
    status = getattr(job, "status", "") or ""
    return {
        "job_id": str(getattr(job, "id", "")),
        "prompt": prompt[:500],
        "provider": provider,
        "format": fmt,
        "resolution": [w, h],
        "duration": dur,
        "status": status,
        "mode": getattr(job, "mode", "") or "",
        "has_output": bool(getattr(job, "output_path", "")),
        "meta_keys": list(meta.keys())[:30],
    }


def learn_from_successful_video_job(job, user=None) -> Optional[Any]:
    """Başarılı video üretiminden ayrı video öğrenen modelini günceller."""
    try:
        status = (getattr(job, "status", "") or "").lower()
        if status not in ("completed", "done", "ready", "success"):
            return None
        if not getattr(job, "output_path", ""):
            return None
        from .models import LearnedVideoKnowledge
        user_obj = user or getattr(job, "user", None)
        key = "global"
        obj, _ = LearnedVideoKnowledge.objects.get_or_create(
            user=user_obj if user_obj is not None else None,
            key=key,
            defaults={"profile": {}, "insights": [], "sources": [], "research_history": []},
        )
        insight = _extract_video_insights(job)
        insight["learned_at"] = _now().isoformat()
        insights = obj.insights if isinstance(obj.insights, list) else []
        insights.append(insight)
        obj.insights = insights[-200:]
        profile = obj.profile if isinstance(obj.profile, dict) else {}
        # aggregate simple learned preferences/stats
        by_provider = profile.setdefault("by_provider", {})
        prov = insight.get("provider") or "unknown"
        pstat = by_provider.setdefault(prov, {"success": 0})
        pstat["success"] = int(pstat.get("success", 0)) + 1
        by_format = profile.setdefault("by_format", {})
        fkey = insight.get("format") or "unknown"
        by_format[fkey] = int(by_format.get(fkey, 0)) + 1
        resolutions = profile.setdefault("resolutions", [])
        res = insight.get("resolution")
        if res and res not in resolutions:
            resolutions.append(res)
            profile["resolutions"] = resolutions[-20:]
        profile["last_success"] = insight
        obj.profile = profile
        obj.successful_jobs = int(obj.successful_jobs or 0) + 1
        obj.last_learned_at = _now()
        hist = obj.research_history if isinstance(obj.research_history, list) else []
        hist.append({"at": insight["learned_at"], "event": "successful_video_job", "job_id": insight["job_id"]})
        obj.research_history = hist[-200:]
        obj.save()
        return obj
    except Exception as exc:
        logger.warning("Video öğrenme kaydı başarısız: %s", exc)
        return None


# =========================
# Eğitim verisi dışa aktarımı (fine-tune / pipeline scaffold)
# =========================

def export_entity_training_examples(user=None, limit: int = 500) -> List[Dict[str, Any]]:
    """LearnedEntityKnowledge kayıtlarından fine-tune/dataset örnekleri üretir."""
    from .models import LearnedEntityKnowledge
    qs = LearnedEntityKnowledge.objects.all().order_by("-updated_at")
    if user is not None:
        qs = qs.filter(user=user) | qs.filter(user__isnull=True)
    examples = []
    for obj in qs[: max(1, int(limit or 500))]:
        spec = obj.learned_spec if isinstance(obj.learned_spec, dict) else {}
        hierarchy = obj.hierarchy if isinstance(obj.hierarchy, dict) else (spec.get("hierarchy") or {})
        prompt = (
            f"Varlık: {obj.concept}\nKategori: {obj.category or 'other'}\n"
            "Görev: En temel seviyeden başlayarak bottom-up hiyerarşik 3D sınıf tanımı üret (katmanlar, bileşenler, özellikler, fizik/animasyon/render ipuçları)."
        )
        completion = json.dumps({
            "concept": obj.concept,
            "category": obj.category,
            "hierarchy": hierarchy,
            "features": obj.features or {},
            "components_template": (spec.get("components_template") or {}),
            "skeleton_template": (spec.get("skeleton_template") or {}),
            "render_template": (spec.get("render_template") or {}),
            "parameters": (spec.get("parameters") or {}),
            "confidence": float(obj.confidence or 0.0),
        }, ensure_ascii=False)
        examples.append({
            "type": "entity_3d",
            "id": str(obj.id),
            "messages": [
                {"role": "system", "content": "Sen bottom-up 3D varlık/sınıf ontolojisi üreten bir asistansın. Hazır şablon kullanma; araştırmaya dayalı hiyerarşik JSON üret."},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": completion},
            ],
            "metadata": {
                "concept": obj.concept,
                "research_count": int(obj.research_count or 0),
                "confidence": float(obj.confidence or 0.0),
            },
        })
    return examples


def export_video_training_examples(user=None, limit: int = 500) -> List[Dict[str, Any]]:
    """LearnedVideoKnowledge ve başarılı job içgörülerinden dataset örnekleri üretir."""
    from .models import LearnedVideoKnowledge
    qs = LearnedVideoKnowledge.objects.all().order_by("-updated_at")
    if user is not None:
        qs = qs.filter(user=user) | qs.filter(user__isnull=True)
    examples = []
    for obj in qs[: max(1, int(limit or 500))]:
        profile = obj.profile if isinstance(obj.profile, dict) else {}
        insights = obj.insights if isinstance(obj.insights, list) else []
        # profile-level example
        examples.append({
            "type": "video_profile",
            "id": str(obj.id),
            "messages": [
                {"role": "system", "content": "Sen başarılı video üretimlerinden öğrenen bir planlama asistanısın."},
                {"role": "user", "content": "Başarılı video üretimleri için önerilen sağlayıcı/format/çözünürlük profili ve üretim ipuçlarını JSON olarak ver."},
                {"role": "assistant", "content": json.dumps({"profile": profile, "successful_jobs": int(obj.successful_jobs or 0)}, ensure_ascii=False)},
            ],
            "metadata": {"key": obj.key, "successful_jobs": int(obj.successful_jobs or 0)},
        })
        for ins in insights[-50:]:
            if not isinstance(ins, dict):
                continue
            prompt = ins.get("prompt") or ""
            if not prompt:
                continue
            examples.append({
                "type": "video_job",
                "id": f"{obj.id}:{ins.get('job_id','')}",
                "messages": [
                    {"role": "system", "content": "Sen video üretim parametrelerini başarılı örneklere göre öneren bir asistansın."},
                    {"role": "user", "content": f"Prompt: {prompt}\nHedef: başarılı video üretimi için parametre öner."},
                    {"role": "assistant", "content": json.dumps({
                        "provider": ins.get("provider"),
                        "format": ins.get("format"),
                        "resolution": ins.get("resolution"),
                        "duration": ins.get("duration"),
                        "mode": ins.get("mode"),
                    }, ensure_ascii=False)},
                ],
                "metadata": {"job_id": ins.get("job_id"), "provider": ins.get("provider")},
            })
        if len(examples) >= limit:
            break
    return examples[:limit]
