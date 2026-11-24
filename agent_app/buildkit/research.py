"""Derin arastirma: konuyu gercekten ogren.

Asamalar:
  1. Kapsam cikar      Konunun ne oldugunu, kimin kullanacagini, platformu tespit et
  2. Sorgu uret       8-15 arama sorgusu (tanim, mimari, ozellikler, UX, yigin,
                       guvenlik, SEO/performance, rakip, tuzaklar, trendler)
  3. Fan-out arama    Her sorguyu coklu saglayicida calistir, sonuclari birlestir
  4. Derin okuma      En umut verici sayfalari indir, okunabilir metne cevir
  5. Not cikarim      Her belgeyi yapilandirilmis notlara indirge
  6. Dossier sentezi  Butun notlardan tek bir arastirma dosyasi olustur

Cikti `task_runs/<run_id>.research.json` olarak yazilir ve ResearchDossier
tablosuna yansitilir.
"""
import asyncio
import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from django.conf import settings

from . import llm, search
from .events import RunLogger, utcnow
from .extract import HtmlDoc, fetch_html

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


def _domain_of(url) -> str:
    try:
        return (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def _note_label(note: Dict) -> str:
    return (
        str(note.get("title") or "").strip()
        or _domain_of(note.get("url"))
        or str(note.get("url") or "belge")
    )


def _note_metrics(note: Dict) -> str:
    parts = []
    if note.get("takeaways"):
        parts.append(f"{len(note.get('takeaways') or [])} çıkarım")
    if note.get("features"):
        parts.append(f"{len(note.get('features') or [])} özellik")
    if note.get("pitfalls"):
        parts.append(f"{len(note.get('pitfalls') or [])} tuzak")
    if note.get("numbers"):
        parts.append(f"{len(note.get('numbers') or [])} sayısal hedef")
    return " · ".join(parts)


SCOPE_SYSTEM = """Sen kıdemli bir ürün stratejisti ve yazılım mimarısın.
Kullanıcının proje isteğini analiz edip araştırma kapsamını çıkar.

ALAN KURALLARI:
- "platform" alanı şunlardan biri olmalı: web | android | ios | desktop | game | backend_api | cli
- Birden fazla platform gerekiyorsa birincil olanı seç ve "extra_platforms" listesine diğerlerini koy.
- "domain" ürünün alan adı cümlesidir (örn: "haber portalı", "3D tarayıcı oyunu").
- "search_queries" cümle dışı, kısa ve arama motoruna verilebilir olmalı.
- Türkçe ve İngilizce sorguları birlikte ver: Türkçe kullanıcı için yerel kaynaklar,
  İngilizce için güncel teknik kaynaklar bulunur.

JSON ŞEMA (tam olarak bu anahtarlar):
{
  "domain": "...",
  "one_liner": "ürün tek cümlelik tanımı",
  "platform": "web|android|ios|desktop|game|backend_api|cli",
  "extra_platforms": [],
  "target_users": ["..."],
  "core_jobs": ["kullanıcının yapmak istediği 5-8 ana iş"],
  "search_queries": ["8-15 adet arama sorgusu"],
  "competitor_hints": ["alandaki bilinen ürün/site adları"]
}"""


SCOPE_USER = """Kullanıcının isteği:
\"\"\"{request}\"\"\"

Ek proje bağlamı (yoksa boş):
\"\"\"{context}\"\"\"

Buna göre araştırma kapsamını JSON olarak çıkar."""


NOTES_SYSTEM = """Sen teknik araştırmacısın. Sana verilen bir web belgesinin özetini
YAPIlandırılmış JSON olarak çıkar. Sadece belgede GERÇEKTEN yazan bilgiyi yaz;
tahmin etme, tamamlama. Bilgi yoksa alanı boş bırak.

JSON ŞEMA:
{
  "title": "belgenin başlığı",
  "takeaways": ["en önemli 3-6 madde, her biri tam cümle ve somut"],
  "features": ["bu alanda olması gereken özellikler (ürün/konu ile ilgili)"],
  "stack": ["önerilen teknoloji/kütüphane/framework adları"],
  "rules": ["kurallar, best practice'ler, uyarılar"],
  "pitfalls": ["yapılmaması gerekenler, sık yapılan hatalar"],
  "numbers": ["somut sayısal hedefler (performans, SEO, erişilebilirlik vb.)"]
}

Belge konuyla alakasızsa takeaways boş bırak ve bunu "irrelevant": true ile bildir."""


DOSSIER_SYSTEM = """Sen kıdemli ürün araştırmacısısın. Elindeki:
- kullanıcının isteği ve kapsam analizi
- arama sonuçlarından derlenen belge notları

...birbirinden **DOMAİN DOSYASI** sentezle. Bu dosya, daha sonra bir yazılım
ekibinin bu alanda ürün geliştirmesinde kılavuz olacak. Notlardaki bilgiyi
sentezle; eksik kalan boyutları alan bilginle tamamla ama kaynak uydurma.

JSON ŞEMA:
{
  "summary": "4-8 cümlelik yönetici özeti",
  "how_it_works": "bu tür bir sistem nasıl çalışır (akış, veri modeli, kullanıcı yolculuğu)",
  "domain_glossary": [{"term": "...", "meaning": "..."}],
  "must_have_features": [{"feature": "...", "why": "..."}],
  "advanced_features": [{"feature": "...", "why": "..."}],
  "pitfalls": [{"pitfall": "...", "consequence": "..."}],
  "tech_stack": [{"choice": "...", "rationale": "..."}],
  "ux_principles": ["..."],
  "quality_bar": {
    "performance": ["somut hedefler"],
    "accessibility": ["somut hedefler"],
    "seo": ["somut hedefler"],
    "security": ["somut hedefler"]
  },
  "differentiation_opportunities": ["hiçbir rakipte olmayan, yapılabilir fırsat"],
  "open_questions": ["araştırma sırasında netleşmeyen, geliştirme sırasında verilmesi gereken kararlar"],
  "sources": [{"title": "...", "url": "..."}]
}

DERİNLİK KURALLARI (bunlar eksik kalırsa ürün yüzeysel olur):
- `must_have_features`: en az 8 madde. En az 2'si tasarım sistemi/responsive,
  2'si içerik/keşif (arama, filtre, detay, ilgili içerik), 2'si kalite
  (erişilebilirlik, SEO, performans, hata durumları) olsun.
- `advanced_features`: en az 6 madde (kişiselleştirme, favoriler, paylaşma,
  gerçek zamanlı güncelleme, tema, öneri sistemi gibi).
- `ux_principles`: en az 6 madde; yükleniyor/boş/hata durumları ve mobil
  kullanım açıkça geçsin.
- `quality_bar` altındaki her liste en az 4 somut hedef içersin.
- `differentiation_opportunities`: en az 4 madde.
- Hiçbir liste boş olmasın."""


class DeepResearcher:
    """Konuyu çok kaynaklı araştırır ve dosyaya dönüştürür."""

    def __init__(self, run_logger: RunLogger, model: str = None):
        self.log = run_logger
        self.model = model
        self.max_queries = int(_cfg("research_queries", 12))
        self.max_deep_reads = int(_cfg("research_deep_reads", 12))
        self.per_query = int(_cfg("research_per_query", 6))
        self.crawl_delay = float(_cfg("crawl_delay", 0.4))
        self.read_chars = int(_cfg("research_read_chars", 5000))

    # ------------------------------------------------------------------
    def run(self, request: str, context: str = "") -> Dict:
        self.log.stage("research", "Araştırma başlıyor")
        scope = self._scope(request, context)
        self.log.info(
            f"Kapsam: {scope.get('domain')} | platform={scope.get('platform')} | "
            f"{len(scope.get('search_queries') or [])} sorgu"
        )

        queries = self._expand_queries(scope, request)
        self.log.status(
            f"Araştırma kapsamı: {scope.get('domain')} · platform {scope.get('platform')} · "
            f"{len(scope.get('search_queries') or [])} başlangıç sorgusu"
        )
        self.log.status(
            f"{len(queries)} arama sorgusu hazır: "
            + " | ".join(queries[:4])
            + (" …" if len(queries) > 4 else "")
        )
        harvest = self._harvest(queries)
        notes = self._deep_read(harvest)
        dossier = self._synthesize(request, scope, harvest, notes)

        dossier["generated_at"] = utcnow()
        dossier["scope"] = scope
        dossier["queries"] = queries
        dossier["provider_report"] = harvest["providers"]
        dossier["search_stats"] = harvest["stats"]
        dossier["documents"] = notes
        dossier["url_count"] = len(harvest["all"])

        self.log.save("research", dossier)
        self.log.info(
            f"Araştırma dosyası hazır: {len(dossier.get('must_have_features') or [])} zorunlu özellik, "
            f"{len(notes)} belge, {len(harvest['all'])} URL"
        )
        self.log.stage("research", "Araştırma tamamlandı")
        return dossier

    # ------------------------------------------------------------------
    def _scope(self, request: str, context: str) -> Dict:
        data = llm.ask_json(
            SCOPE_SYSTEM,
            SCOPE_USER.format(request=request[:4000], context=(context or "-")[:2000]),
            model=self.model,
            purpose="research",
        )
        scope = llm.as_dict(data)
        if not scope:
            # LLM yoksa sezgisel yedek: istekten alan adi ve platform cikar.
            scope = self._fallback_scope(request)
        scope.setdefault("domain", "proje")
        scope.setdefault("platform", self._guess_platform(request))
        scope.setdefault("search_queries", [])
        scope.setdefault("competitor_hints", [])
        scope.setdefault("target_users", [])
        scope.setdefault("core_jobs", [])
        return scope

    def _fallback_scope(self, request: str) -> Dict:
        text = (request or "").lower()
        domain = next(
            (
                word
                for word in ("haber", "oyun", "forum", "e-ticaret", "sosyal medya", "blog",
                             "panel", "uygulama", "site", "platform", "dashboard", "müzik",
                             "video", "pazar yeri", "Randevu", "Rehber", "Kütüphane")
                if word in text
            ),
            "proje",
        )
        return {
            "domain": domain,
            "one_liner": request[:200],
            "platform": self._guess_platform(request),
            "target_users": [],
            "core_jobs": [],
            "search_queries": [domain, f"{domain} nasıl yapılır", f"{domain} özellikleri",
                               f"best {domain} practices"],
            "competitor_hints": [],
        }

    @staticmethod
    def _guess_platform(text: str) -> str:
        lowered = (text or "").lower()
        if any(k in lowered for k in ("android", "play store", "kotlin")):
            return "android"
        if any(k in lowered for k in ("ios", "swift", "iphone", "app store")):
            return "ios"
        if any(k in lowered for k in ("3d", "oyun", "game", "unity", "unreal", "phaser")):
            return "game"
        if any(k in lowered for k in ("masaüstü", "desktop", "electron", "exe")):
            return "desktop"
        if any(k in lowered for k in ("api", "backend", "servis", "mikroservis")):
            return "backend_api"
        if any(k in lowered for k in ("cli", "terminal", "konsol")):
            return "cli"
        return "web"

    # ------------------------------------------------------------------
    def _expand_queries(self, scope: Dict, request: str) -> List[str]:
        queries = [q for q in (scope.get("search_queries") or []) if isinstance(q, str) and q.strip()]
        domain = (scope.get("domain") or "proje").strip()
        platform = scope.get("platform") or "web"

        # Sistematik kapsama: LLM'in unuttugu boyutlari ekle.
        fillers = [
            f"{domain} özellikleri ve kullanım senaryoları",
            f"{domain} architecture design best practices",
            f"{domain} user experience UX principles",
            f"{domain} teknoloji seçimi kütüphane framework",
            f"{domain} security checklist",
            f"{domain} SEO performance accessibility best practices",
            f"{domain} common mistakes pitfalls",
            f"{platform} {domain} real world examples",
        ]
        seen = {q.lower().strip() for q in queries}
        for filler in fillers:
            key = filler.lower().strip()
            if key not in seen:
                seen.add(key)
                queries.append(filler)

        return queries[: self.max_queries]

    # ------------------------------------------------------------------
    def _harvest(self, queries: List[str]) -> Dict:
        provider_count = len(search.configured_providers())
        self.log.status(
            f"Web araması başlıyor: {len(queries)} sorgu · {provider_count} sağlayıcı"
        )
        outcome = search.search_many(
            queries, per_query=self.per_query, log=lambda m: self.log.status(m)
        )
        self.log.count("search_results", len(outcome["all"]))
        failed = [n for n, i in outcome["providers"].items() if i.get("failed")]
        if failed:
            self.log.warn(f"{len(failed)} arama sağlayıcısı bu turda yanıt vermedi: {', '.join(failed[:6])}")
        stats = outcome.get("stats") or {}
        self.log.status(
            f"Arama tamamlandı: {len(outcome['all'])} benzersiz sonuç · "
            f"{stats.get('queries', len(queries))} sorgu · "
            f"{len(outcome.get('providers') or {})} sağlayıcı raporu"
        )
        return outcome

    # ------------------------------------------------------------------
    def _deep_read(self, harvest: Dict) -> List[Dict]:
        candidates = self._pick_documents(harvest["all"])
        if not candidates:
            self.log.warn("Derin okuma için uygun belge bulunamadı.")
            return []
        preview = ", ".join(
            f"{item.get('title') or _domain_of(item.get('url'))} ({_domain_of(item.get('url'))})"
            for item in candidates[:4]
        )
        self.log.status(
            f"{len(candidates)} belge derin okunuyor: {preview}"
            + (" …" if len(candidates) > 4 else "")
        )

        def read(url: str):
            try:
                status, html, _headers, final_url, _ms = fetch_html(url, timeout=10, max_bytes=900_000)
                doc = HtmlDoc(html, final_url)
                text = doc.text(max_chars=self.read_chars)
                if len(text) < 200:
                    return None
                return {"url": final_url, "status": status, "title": doc.title, "text": text}
            except Exception as exc:  # FetchError dahil
                logger.debug("derin okuma basarisiz %s: %s", url, exc)
                return None

        notes: List[Dict] = []
        with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as pool:
            futures = {pool.submit(read, item["url"]): item for item in candidates}
            for done_index, future in enumerate(as_completed(futures), start=1):
                item = futures[future]
                item_label = item.get("title") or _domain_of(item.get("url"))
                item_domain = _domain_of(item.get("url"))
                try:
                    page = future.result()
                except Exception:
                    page = None
                if not page:
                    self.log.count("read_failed")
                    self.log.status(
                        f"Belge okunamadı: {done_index}/{len(candidates)} — "
                        f"{item_label} ({item_domain})"
                    )
                    continue
                note = self._extract_notes(page, item)
                if note and not note.get("irrelevant"):
                    notes.append(note)
                    self.log.count("documents_read")
                    label = _note_label(note)
                    domain = _domain_of(note.get("url")) or item_domain
                    metrics = _note_metrics(note)
                    self.log.status(
                        f"Belge okundu: {done_index}/{len(candidates)} — {label} ({domain})"
                        + (f" · {metrics}" if metrics else " · notlar çıkarıldı")
                    )
                else:
                    self.log.status(
                        f"Belge ilgisiz bulundu: {done_index}/{len(candidates)} — "
                        f"{item_label} ({item_domain})"
                    )

        total_takeaways = sum(len(n.get("takeaways") or []) for n in notes)
        domains = {_domain_of(n.get("url")) for n in notes if _domain_of(n.get("url"))}
        self.log.status(
            f"{len(notes)} belge analiz edildi · {total_takeaways} çıkarım · "
            f"{len(domains)} kaynak alanı"
        )
        return notes

    def _pick_documents(self, results: List[dict]) -> List[dict]:
        """En degerli sayfalari sec: coklu saglayici dogrulamasi + kaliteli domain."""
        blocked = (
            "youtube.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
            "pinterest.", "linkedin.com", "tiktok.com", "reddit.com", "amazon.com",
            "play.google.com", "apps.apple.com", "docs.google.com", "quora.com",
        )
        scored = []
        for item in results:
            url = item.get("url") or ""
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            if not host or any(bad in host for bad in blocked):
                continue
            trust_bonus = 0
            if any(good in host for good in ("wikipedia", "github", "stackoverflow", "medium",
                                             "dev.to", "smashingmagazine", "css-tricks", "mdn",
                                             "mozilla", "w3.org", "google", "microsoft", "apple",
                                             "mozilla", "web.dev", "caniuse", "npm", "arxiv")):
                trust_bonus = 3
            if any(bad in host for bad in ("wixsite", "blogspot", "weebly", "wordpress.com", "pinterest")):
                trust_bonus -= 2
            confirmation = len(item.get("providers") or [])
            title_len = len(item.get("title") or "")
            score = confirmation * 3 + trust_bonus + min(title_len // 12, 3)
            scored.append({**item, "score": score, "domain": host})

        scored.sort(key=lambda r: r["score"], reverse=True)
        picked, seen_hosts = [], set()
        for item in scored:
            registrable = item["domain"]
            if registrable in seen_hosts and len(picked) > 4:
                continue  # tek domainden 3'ten fazla sayfa alma
            seen_hosts.add(registrable)
            picked.append(item)
            if len(picked) >= self.max_deep_reads:
                break
        return picked

    # ------------------------------------------------------------------
    def _extract_notes(self, page: Dict, item: Dict) -> Dict:
        user = (
            f"Belge: {page.get('title') or item.get('title')}\n"
            f"URL: {page['url']}\n"
            f"Arama sorgusu: {item.get('query', '-')}\n\n"
            f"Belge metni:\n{page['text']}"
        )
        note = llm.as_dict(
            llm.ask_json(NOTES_SYSTEM, user, model=self.model, purpose="research", temperature=0.1)
        )
        if not note:
            return None
        note["url"] = page["url"]
        note["query"] = item.get("query", "")
        note["providers"] = item.get("providers") or []
        return note

    # ------------------------------------------------------------------
    def _synthesize(self, request: str, scope: Dict, harvest: Dict, notes: List[Dict]) -> Dict:
        note_count = len(notes)
        domains = {_domain_of(n.get("url")) for n in notes if _domain_of(n.get("url"))}
        preview = " | ".join(
            f"{_note_label(n)} ({_domain_of(n.get('url'))})" for n in notes[:4]
        )
        self.log.status(
            f"Notlar sentezleniyor: {note_count} not · {len(domains)} kaynak alanı"
            + (f" — {preview}" + (" …" if note_count > 4 else "") if preview else "")
        )
        if not notes:
            self.log.warn("Okunabilir belge bulunamadı; istekten türetilen dosya kullanılacak")
            return self._fallback_dossier(request, scope)

        for idx, note in enumerate(notes[:10], start=1):
            metrics = _note_metrics(note)
            self.log.status(
                f"Not sentezleniyor {idx}/{note_count}: {_note_label(note)} "
                f"({_domain_of(note.get('url'))})"
                + (f" · {metrics}" if metrics else "")
            )
        self.log.status(
            "Sentez modeli çalışıyor: notlar tek domain dosyasında birleştiriliyor…"
        )

        notes_blob = "\n\n".join(
            f"### {n.get('title') or n.get('url')}\nURL: {n.get('url')}\n"
            + "\n".join(f"- {t}" for t in (n.get("takeaways") or [])[:6])
            + ("\n  Özellikler: " + ", ".join(n.get("features") or [])[:8] if n.get("features") else "")
            + ("\n  Yığın: " + ", ".join(n.get("stack") or [])[:8] if n.get("stack") else "")
            + ("\n  Kurallar: " + ", ".join(n.get("rules") or [])[:6] if n.get("rules") else "")
            + ("\n  Tuzaklar: " + ", ".join(n.get("pitfalls") or [])[:6] if n.get("pitfalls") else "")
            + ("\n  Sayısal hedefler: " + ", ".join(n.get("numbers") or [])[:6] if n.get("numbers") else "")
            for n in notes
        )
        # Çok uzun notları kırp: model bağlamını taşırmayalım.
        notes_blob = notes_blob[:60_000]

        user = (
            f"Kullanıcı isteği:\n{request[:3000]}\n\n"
            f"Kapsam analizi:\n{_compact_json(scope)}\n\n"
            f"Derlenen belge notları ({len(notes)} belge):\n{notes_blob}\n\n"
            "Bu verilere dayanarak DOMAİN DOSYASI üret."
        )
        dossier = llm.as_dict(
            llm.ask_json(DOSSIER_SYSTEM, user, model=self.model, purpose="research", temperature=0.25)
        )
        if not dossier:
            dossier = self._fallback_dossier(request, scope)

        dossier.setdefault("summary", f"{scope.get('domain')} alanında {len(notes)} belge incelendi.")
        for key in ("how_it_works", "ux_principles", "differentiation_opportunities", "open_questions"):
            if not dossier.get(key):
                dossier[key] = []
        for key in ("must_have_features", "advanced_features", "pitfalls", "tech_stack", "domain_glossary"):
            if not isinstance(dossier.get(key), list):
                dossier[key] = []
        if not isinstance(dossier.get("quality_bar"), dict):
            dossier["quality_bar"] = {}
        for key in ("performance", "accessibility", "seo", "security"):
            dossier["quality_bar"].setdefault(key, [])
        self.log.status(
            f"Sentez tamamlandı: {len(dossier.get('must_have_features') or [])} zorunlu özellik, "
            f"{len(dossier.get('advanced_features') or [])} gelişmiş özellik, "
            f"{len(dossier.get('pitfalls') or [])} tuzak, "
            f"{len(dossier.get('tech_stack') or [])} teknoloji, "
            f"{len(dossier.get('sources') or [])} kaynak"
        )
        return dossier

    def _fallback_dossier(self, request: str, scope: Dict) -> Dict:
        """LLM yoksa/bozuk cevap verirse istekten anlamli bir dosya uret."""
        domain = scope.get("domain", "proje")
        return {
            "summary": (
                f"'{domain}' isteği için hazırlanmış başlangıç dosyası. "
                "Araştırma aşamasında web erişimi kurulamadığı için model bilgisi kullanıldı."
            ),
            "how_it_works": f"Kullanıcı {domain} için {request[:200]}",
            "domain_glossary": [],
            "must_have_features": [
                {"feature": "Temel içerik/listeleme ekranı", "why": "ürünün varlık nedeni"},
                {"feature": "Arama ve filtreleme", "why": "kullanıcı içeriğe hızlı ulaşmalı"},
                {"feature": "Detay ekranı", "why": "her kayıt görüntülenebilmeli"},
                {"feature": "Üyelik/giriş", "why": "kişiselleştirme ve etkileşim için"},
                {"feature": "Yönetim paneli", "why": "içerik yönetimi olmadan ürün yaşayamaz"},
                {"feature": "Duyarlı (mobil) tasarım", "why": "mobillerden gelen trafiğin çoğunluğu"},
                {"feature": "Tasarım sistemi ve bileşen kütüphanesi", "why": "tutarlı ve hızlı geliştirme"},
                {"feature": "Yükleniyor/boş/hata durumları", "why": "kullanıcı her durumda ne olacağını bilmeli"},
            ],
            "advanced_features": [
                {"feature": "Gerçek zamanlı güncelleme", "why": "güncellik hissi"},
                {"feature": "Kişiselleştirme", "why": "geri dönüş oranını artırır"},
                {"feature": "Paylaşım/dışa aktarma", "why": "büyüme kanalı"},
                {"feature": "Favoriler/kaydetme", "why": "tekrar ziyaret ve bağlılık"},
                {"feature": "Koyu mod/tema", "why": "kullanıcı tercihine uyum"},
                {"feature": "İlgili içerik/öneriler", "why": "keşfi ve oturum süresini artırır"},
            ],
            "pitfalls": [
                {"pitfall": "Eksiksiz kapsama rağmen çalışmayan akış", "consequence": "kullanıcı ilk denemede vazgeçer"},
                {"pitfall": "Mobilde kırılan düzen", "consequence": "ziyaretçi kaybı ve SEO cezası"},
            ],
            "tech_stack": [],
            "ux_principles": [
                "İlk ekranda ne yapılacağı net olsun",
                "Her etkileşimde görünür geri bildirim ver",
                "Mobil öncelikli tasarla",
                "Hata mesajları ne yapılacağını söylesin",
                "Yükleniyor, boş ve hata durumlarını tasarla",
                "Klavye ve ekran okuyucu ile kullanılabilir olsun",
            ],
            "quality_bar": {
                "performance": ["İlk yükleme < 2.5 sn", "LCP < 2.5 sn", "Görsellerde lazy loading", "Tekrarlayan isteklerde önbellek"],
                "accessibility": ["Klavyeyle tam kullanılabilir", "Görsellerde alt metin", "ARIA ve semantik HTML", "Kontrast AA seviyesi"],
                "seo": ["Her sayfada benzersiz title/description", "canonical", "sitemap.xml", "Open Graph etiketleri"],
                "security": ["Giriş doğrulaması", "CSRF koruması", "Yetki kontrolü her uçta", "Güvenli varsayılanlar"],
            },
            "differentiation_opportunities": [
                "Daha hızlı ve akıcı bir keşif deneyimi",
                "Kişiselleştirilmiş öneriler",
                "Erişilebilir ve mobil öncelikli tasarım",
                "Şeffaf boş/hata durumlarıyla güven",
            ],
            "open_questions": ["Hedef kitlenin yaşı/cihaz profili?", "Çok dilli olacak mı?"],
            "sources": [],
        }


def _compact_json(payload, limit: int = 2500) -> str:
    import json

    try:
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
        text = str(payload)
    return text[:limit]


def run_deep_research(request: str, run_logger: RunLogger, context: str = "", model: str = None) -> Dict:
    """Tek cagrilik kolaylik."""
    return DeepResearcher(run_logger, model=model).run(request, context=context)


def quick_web_research(question: str, run_logger: RunLogger = None, depth: int = 6) -> Dict:
    """Ajan araci icin hafif arastirma: 3 sorgu + ozet.

    `deep_research` aracinin uzerinde calistigi icin burada pipeline'a girmiyor;
    sadece arama + kisa sentez yapar.
    """
    log = run_logger or RunLogger()
    queries = [question, f"{question} best practices", f"{question} 2025 trends"][:depth]
    outcome = search.search_many(queries, per_query=5, log=lambda m: log.info(m))
    top = outcome["all"][:8]
    if not top:
        return {"ok": False, "error": "arama sonucu bulunamadı", "question": question}

    blob = "\n\n".join(
        f"- {item.get('title')}\n  {item['url']}\n  {(item.get('snippet') or '')[:400]}"
        for item in top
    )
    summary = llm.ask_text(
        "Sen araştırmacısın. Verilen arama sonuçlarından soruya özlü, maddeli bir özet çıkar. "
        "Kaynak URL'lerini madde sonunda parantez içinde belirt.",
        f"Soru: {question}\n\nArama sonuçları:\n{blob[:12_000]}",
        purpose="research",
        temperature=0.2,
    )
    return {
        "ok": True,
        "question": question,
        "summary": summary,
        "results": [
            {"title": i.get("title"), "url": i["url"], "snippet": (i.get("snippet") or "")[:400]}
            for i in top
        ],
        "providers": list(outcome["providers"].keys()),
    }


import asyncio

# Async versions for multi-agent pipeline
async def run_deep_research_async(request: str, run_logger: RunLogger, context: str = "", model: str = None) -> Dict:
    """Async version of run_deep_research."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: run_deep_research(request, run_logger, context, model))

async def quick_web_research_async(question: str, run_logger: RunLogger = None, depth: int = 6, model: str = None) -> Dict:
    """Async version of quick_web_research."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: quick_web_research(question, run_logger, depth, model))