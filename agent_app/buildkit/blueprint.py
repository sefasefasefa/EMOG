"""Mavi baskı: en gelişmiş hali ne olmalı?

Arastirma dosyası (neyin bilinmesi gerektiği) + rakip analizi (gerçekte ne
olduğu) + kullanıcının isteği (ne istendiği) -> TEK bir urun spesifikasyonu.

Bu belge plan uretimi, prompt'lar ve kalite kontrolleri icin tek dogruluk
kaynagidir. Uretilemezse (LLM yoksa) deterministik bir yedek kurulur; pipeline
hicbir zaman specsiz ilerlemez.
"""
import json
import logging
from typing import Dict

from django.conf import settings

from . import llm
from .events import RunLogger

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


BLUEPRINT_SYSTEM = """Sen baş mimar ve ürün sahibisin. Sana üç girdi verilir:
1) kullanıcının isteği,
2) alan araştırma dosyası,
3) rakip analizi.

Bunlardan **bu alandaki mümkün olan en gelişmiş, en üretim kalitesinde ürünün**
spesifikasyonunu yaz. "MVP" değil: kullanıcı bunu gerçekten kullanıp
paylaşabilsin. Kapsamı gereksiz şişirme; ama en az 24 özellik hedefini tut ve
her özellik gerçekten kullanılabilir, görünür ve test edilebilir olsun.

KRİTİK KURALLAR:
- **Derinlik hedefi:** En az 8 özellik grubu ve en az 30 özellik üret. Her özellik
  için en az 2 ölçülebilir kabul kriteri yaz. "Site çalışsın" gibi yüzeysel
  maddeler yasak; her madde görünen, tıklanabilir, test edilebilir bir davranış olsun.
- **İyileştirme isteği:** Kullanıcı "tasarımı geliştir / daha detaylı yap" gibi bir
  şey dediyse sadece renk veya boşluk değiştirme; yeni bölümler, kart/detay
  düzenleri, etkileşimler, durumlar (yükleniyor/boş/hata), responsive kırılma
  noktaları, erişilebilirlik ve keşif özellikleriyle görünür biçimde derinleştir.
- **Tasarım/deneyim ayrı grup olsun:** renk paleti/design token, tipografi ölçeği,
  boşluk/grid, bileşen kütüphanesi, responsive düzen, mobil menü, yükleniyor/boş/hata
  durumları, mikro etkileşimler, klavye erişimi, koyu mod gibi maddeleri unutma.
- **İçerik/keşif:** arama, filtreleme, sıralama, detay sayfası, ilgili içerik,
  sayfalama/daha fazla yükle, favoriler, paylaşma gibi ürüne uygun keşif özellikleri ekle.
- **Kalite:** erişilebilirlik, SEO (meta/OG/sitemap), performans (lazy load/önbellek),
  404, örnek veri, test ve dokümantasyon için ayrı maddeler yaz.
- Her özellik ölçülebilir bir kabul kriteri taşımalı.
- `stack` seçimleri **kullanıcı isteğine ve araştırmaya göre** olmalı:
  * Basit statik site (haber, blog, landing, tek sayfa, portfolio) → HTML/CSS/JS, vanilla, statik hosting
  * Web uygulaması (dashboard, admin, üyelik, real-time) → React/Vue, build step, API backend
  * Mobil/desktop → Native/Flutter/Electron
  * Backend API → FastAPI/Express, DB, Docker
  Her seçim için **gerekçe yaz**.
- `platform` araştırma kapsamıyla tutarlı olmalı.
- `user_requested_features` SADECE kullanıcının açıkça istediği şeyleri içermeli
  (istekte olmayanı uydurma). Bu madde plan üretiminde en yüksek öncelikli.
- Özellik grupları uygulama sırasına göre olmalı: temel çalışan ürün önce,
  ardından kalite, ardından gelişmiş özellikler.

JSON ŞEMA:
{
  "product_name": "kısa ürün adı",
  "one_liner": "tek cümle",
  "platform": "web|android|ios|desktop|game|backend_api|cli",
  "stack": {
    "language": "...", "framework": "...", "database": "...",
    "styling": "...", "build": "...", "testing": "...",
    "deployment": "...", "rationale": ["gerekçe..."]
  },
  "architecture": {
    "pattern": "...", "modules": ["..."], "data_model": ["ana varlıklar ve alanları"]
  },
  "user_requested_features": ["kullanıcının istediği, sadık listelenmiş maddeler"],
  "feature_groups": [
    {
      "id": "core|design|experience|content|discovery|engagement|membership|admin|quality|delivery|polish",
      "title": "grup başlığı",
      "priority": "must|should|could",
      "features": [
        {
          "key": "kisa_anahtar",
          "title": "...",
          "description": "ne yapar, nasıl çalışır",
          "acceptance": ["doğrulanabilir kabul kriteri", "..."],
          "user_requested": false
        }
      ]
    }
  ],
  "differentiation": ["bunu rakipte yok, şöyle yapıyoruz"],
  "quality_bar": {
    "performance": ["..."], "accessibility": ["..."], "seo": ["..."], "security": ["..."]
  },
  "navigation": ["ana menü/dashboard yapısı"],
  "out_of_scope": ["bilerek yapılmayacaklar - kapsamı korur"]
}"""


EXPAND_SYSTEM = """Sen deneyimli bir ürün stratejistisin. Sana mevcut ürün spesifikasyonu
ve kullanıcı isteği verilir. Spesifikasyon yüzeysel kaldıysa **ürüne özgü, eksik
kalan özellikleri** ekle. Mevcut özellikleri tekrarlama.

Kurallar:
- Sadece JSON döndür: {"feature_groups": [{"id": "...", "title": "...", "priority": "should", "features": [{"key": "...", "title": "...", "description": "...", "acceptance": ["...", "..."], "user_requested": false}]}]}
- En az istenen sayıda YENİ özellik ekle; hepsi bu ürüne özgü ve kullanılabilir olsun.
- "Daha iyi tasarım" gibi soyut maddeler yazma; somut ekran, akış, bileşen veya
  içerik özelliği yaz (örn. son dakika şeridi, trend konular, kaydedilenler,
  yazar takibi, hava durumu widget'ı, okuma listesi, çoklu dil, bildirim).
- Her özelliğe en az 2 ölçülebilir kabul kriteri yaz.
- Tasarım/UX/kalite temelleri zaten ayrıca eklenecek; burada ürüne özgü işlevlere odaklan.
"""


DEPTH_FEATURES = [
    # --- Tasarım sistemi / görsel dil ---
    ("design", "Tasarım sistemi ve görsel dil", "Renk paleti ve design token'ları", "Marka rengi, yüzey, metin ve durum renklerini tek bir token setinde tanımla; tüm bileşenler bunları kullansın.", ["Renkler tek yerden değiştirilebiliyor", "Kontrast oranları erişilebilir seviyede"]),
    ("design", "Tasarım sistemi ve görsel dil", "Tipografi ölçeği ve hiyerarşi", "Başlık, gövde ve yardımcı metinler için tutarlı boyut/ağırlık ölçeği oluştur.", ["Tüm sayfalarda tutarlı tipografi", "Mobilde okunabilir satır yüksekliği"]),
    ("design", "Tasarım sistemi ve görsel dil", "Boşluk, grid ve yerleşim sistemi", "Tutarlı boşluk ölçeği ve responsive grid tanımla.", ["Bölümler arası boşluklar tutarlı", "İçerik farklı ekranlarda hizalı"]),
    ("design", "Tasarım sistemi ve görsel dil", "Yeniden kullanılabilir bileşen kütüphanesi", "Buton, kart, form alanı, rozet ve modal gibi ortak bileşenleri standartlaştır.", ["Aynı bileşen tüm sayfalarda aynı görünüyor", "Bileşenler klavye ile kullanılabiliyor"]),
    # --- Deneyim / durumlar ---
    ("experience", "Kullanıcı deneyimi ve durumlar", "Responsive düzen ve mobil navigasyon", "Mobil, tablet ve masaüstü için kırılma noktaları; mobilde erişilebilir menü.", ["Yatay kaydırma yok", "Menü mobilde açılıp kapanıyor"]),
    ("experience", "Kullanıcı deneyimi ve durumlar", "Yükleniyor ve iskelet (skeleton) durumları", "Veri beklenirken boş ekran yerine yükleme göstergesi göster.", ["Yavaş bağlantıda kullanıcı ne olduğunu anlıyor", "İçerik yüklenince sorunsuz geçiş"]),
    ("experience", "Kullanıcı deneyimi ve durumlar", "Boş durum ekranları", "Arama/filtre sonucu yokken açıklayıcı mesaj ve çıkış aksiyonu göster.", ["Boş sonuçta anlamlı mesaj var", "Kullanıcı filtreyi temizleyebiliyor"]),
    ("experience", "Kullanıcı deneyimi ve durumlar", "Hata durumları ve yeniden deneme", "Ağ/veri hatasında kullanıcıya anlaşılır hata ve tekrar dene seçeneği sun.", ["Hata mesajı teknik jargon içermiyor", "Tekrar dene çalışıyor"]),
    ("experience", "Kullanıcı deneyimi ve durumlar", "Mikro etkileşimler ve geçişler", "Hover, focus, tıklama ve sayfa geçişlerinde tutarlı animasyonlar ekle.", ["Animasyonlar abartısız", "prefers-reduced-motion destekleniyor"]),
    ("experience", "Kullanıcı deneyimi ve durumlar", "Klavye ile gezinme ve odak yönetimi", "Tüm etkileşimli öğeler Tab ile erişilebilir; odak görünür ve mantıklı sırada.", ["Tab sırası doğru", "Odak göstergesi görünür"]),
    # --- Keşif / içerik ---
    ("discovery", "Keşif ve içerik", "Arama ve filtreleme", "Başlık/içerik araması, kategori filtresi ve sonuç sayısı göster.", ["Arama sonuçları doğru", "Filtreler birleşerek çalışıyor"]),
    ("content", "İçerik yapısı", "Detay sayfası ve içerik şablonu", "Liste öğesinden açılan, paylaşılabilir detay sayfası oluştur.", ["Her kart detaya gidiyor", "Geçersiz kimlikte anlaşılır mesaj"]),
    ("content", "İçerik yapısı", "İlgili içerik / öneriler", "Detay sayfasında aynı kategori veya benzer etiketlerden öneriler göster.", ["Öneriler boş kalmıyor", "Tıklanınca doğru detaya gidiyor"]),
    ("discovery", "Keşif ve içerik", "Sayfalama veya daha fazla yükle", "Uzun listeleri sayfalara böl veya kademeli yükle.", ["Liste sonu doğru yönetiliyor", "Sayfa durumu URL'de korunuyor"]),
    # --- Etkileşim ---
    ("engagement", "Etkileşim", "Favoriler / kaydetme", "Kullanıcının içerikleri kaydetmesini ve listelemesini sağla; durumu kalıcı tut.", ["Kaydetme durumu yenilemede korunuyor", "Favoriler listesi çalışıyor"]),
    ("engagement", "Etkileşim", "Paylaşma", "Web Share API ve kopyalama ile içerik paylaşımı ekle.", ["Paylaş butonu çalışıyor", "Kopyalama geri bildirimi var"]),
    ("engagement", "Etkileşim", "Tema / koyu mod", "Kullanıcı tercihine göre açık/koyu tema; tercih kalıcı.", ["Tema değişimi anında", "Yenilemede tema korunuyor"]),
    # --- Kalite ---
    ("quality", "Kalite ve güven", "Erişilebilirlik denetimi (ARIA, kontrast, semantik)", "Başlık hiyerarşisi, aria etiketleri, kontrast ve klavye erişimini tamamla.", ["Axe/erişilebilirlik taraması temiz", "Ekran okuyucu temel akışı okuyor"]),
    ("quality", "Kalite ve güven", "SEO: meta, Open Graph, sitemap/robots", "Sayfa başlıkları, açıklamalar, OG etiketleri ve robots/sitemap ekle.", ["Her sayfada benzersiz başlık/açıklama", "OG etiketleri mevcut"]),
    ("quality", "Kalite ve güven", "Performans: görsel optimizasyonu ve önbellek", "Lazy loading, boyut optimizasyonu ve uygun önbellek başlıkları uygula.", ["Görseller lazy yükleniyor", "İlk yükleme makul sürede"]),
    ("quality", "Kalite ve güven", "404 ve beklenmeyen hata sayfası", "Geçersiz URL ve çökmelerde kullanıcıyı ana sayfaya yönlendiren sayfa.", ["404 sayfası mevcut", "Ana sayfaya dönüş çalışıyor"]),
    ("quality", "Kalite ve güven", "Örnek/seed içerik", "Uygulama ilk açılışta boş görünmesin diye gerçekçi örnek veri ekle.", ["İlk açılışta içerik görünüyor", "Örnek veri kolayca değiştirilebiliyor"]),
    ("delivery", "Teslim ve sürdürülebilirlik", "Testler ve doğrulama komutları", "Kritik akışlar için otomatik test veya doğrulama adımları ekle.", ["Test/doğrulama komutu çalışıyor", "Kritik akışlar kapsanıyor"]),
    ("delivery", "Teslim ve sürdürülebilirlik", "README ve kullanım dokümantasyonu", "Kurulum, çalıştırma, yapı ve özellikleri açıklayan doküman yaz.", ["Yeni geliştirici projeyi çalıştırabiliyor"]),
]


def _feature_key(text: str, index: int = 0) -> str:
    import re as _re
    import unicodedata as _ud
    ascii_text = _ud.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    slug = _re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return (slug or f"feature_{index}")[:48]


def _norm_title(text: str) -> str:
    return " ".join(str(text or "").lower().replace("-", " ").split())


def _unique_feature_key(base: str, used) -> str:
    base = base or "feature"
    if base not in used:
        used.add(base)
        return base
    counter = 2
    while f"{base}_{counter}" in used:
        counter += 1
    key = f"{base}_{counter}"
    used.add(key)
    return key


class BlueprintBuilder:
    def __init__(self, run_logger: RunLogger, model: str = None):
        self.log = run_logger
        self.model = model

    def build(self, request: str, dossier: Dict, benchmark: Dict) -> Dict:
        self.log.stage("blueprint", "Ürün spesifikasyonu yazılıyor")

        blueprint = self._synthesize(request, dossier, benchmark)
        if not blueprint:
            self.log.warn("Spesifikasyon üretilemedi; yapısal yedek kullanılıyor")
            blueprint = self._fallback(request, dossier, benchmark)

        blueprint = self._normalize(blueprint)
        if self._needs_depth(blueprint):
            expanded = self._expand(request, dossier, benchmark, blueprint)
            if expanded:
                blueprint = self._merge_expansion(blueprint, expanded)
                blueprint = self._normalize(blueprint)
        blueprint = self._ensure_depth(blueprint)
        self.log.save("blueprint", blueprint)

        groups = blueprint.get("feature_groups") or []
        feature_count = sum(len(g.get("features") or []) for g in groups)
        self.log.status(
            f"Spesifikasyon hazır: {len(groups)} grup / {feature_count} özellik / "
            f"{len(blueprint.get('user_requested_features') or [])} kullanıcı isteği"
        )
        self.log.stage("blueprint", "Spesifikasyon tamamlandı")
        return blueprint

    # ------------------------------------------------------------------
    def _synthesize(self, request: str, dossier: Dict, benchmark: Dict) -> Dict:
        user = (
            f"=== KULLANICI İSTEĞİ ===\n{request[:4000]}\n\n"
            f"=== ALAN ARAŞTIRMA DOSYASI ===\n{_dossier_blob(dossier)}\n\n"
            f"=== RAKİP ANALİZİ ===\n{_benchmark_blob(benchmark)}\n\n"
            "Bu üçünden ürün spesifikasyonunu üret. "
            "STACK SEÇİMİ: Kullanıcı isteğine göre karar ver (statik site → vanilla HTML/CSS/JS; web app → React/Vue; API → FastAPI; vb.). Gerekçe yaz."
        )
        return llm.as_dict(
            llm.ask_json(BLUEPRINT_SYSTEM, user, model=self.model, purpose="research", temperature=0.2, max_tokens=12000, attempts=2)
        )

    # ------------------------------------------------------------------
    def _normalize(self, blueprint: Dict) -> Dict:
        """Yapıyı garantile: eksik alanlar doldurulur, listeler normalize edilir."""
        blueprint.setdefault("product_name", "Yeni Proje")
        blueprint.setdefault("one_liner", "")
        blueprint.setdefault("platform", "web")
        blueprint.setdefault("differentiation", [])
        blueprint.setdefault("navigation", [])
        blueprint.setdefault("out_of_scope", [])

        stack = blueprint.get("stack")
        if not isinstance(stack, dict) or not stack:
            blueprint["stack"] = self._default_stack(blueprint.get("platform") or "web")
        else:
            defaults = self._default_stack(blueprint.get("platform") or "web")
            for key, value in defaults.items():
                if key == "rationale":
                    stack.setdefault("rationale", [])
                elif not stack.get(key):
                    stack[key] = value

        if not isinstance(blueprint.get("architecture"), dict):
            blueprint["architecture"] = {"pattern": "", "modules": [], "data_model": []}
        arch = blueprint["architecture"]
        arch.setdefault("pattern", "")
        arch.setdefault("modules", [])
        arch.setdefault("data_model", [])

        requested = [f for f in (blueprint.get("user_requested_features") or []) if str(f).strip()]
        blueprint["user_requested_features"] = requested

        groups = blueprint.get("feature_groups")
        if not isinstance(groups, list) or not groups:
            groups = []
        cleaned = []
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            features = []
            for position, feature in enumerate(group.get("features") or []):
                if not isinstance(feature, dict):
                    continue
                title = str(feature.get("title") or feature.get("name") or "").strip()
                if not title:
                    continue
                features.append(
                    {
                        "key": str(feature.get("key") or _slug(title, position)),
                        "title": title,
                        "description": str(feature.get("description") or "").strip(),
                        "acceptance": [str(a).strip() for a in (feature.get("acceptance") or []) if str(a).strip()],
                        "user_requested": bool(feature.get("user_requested")),
                    }
                )
            if not features:
                continue
            cleaned.append(
                {
                    "id": str(group.get("id") or _slug(group.get("title") or "grup", index)),
                    "title": str(group.get("title") or "Grup"),
                    "priority": str(group.get("priority") or "must").lower(),
                    "features": features,
                }
            )

        # Kullanıcı isteklerinin hiçbiri grup feature'ı değilse ekle.
        covered = " ".join(
            (f.get("title", "") + " " + f.get("description", "")).lower()
            for g in cleaned for f in g["features"]
        )
        missing = [
            f for f in requested
            if _slug(f, 0)[:12] not in covered.replace(" ", "_") and f.lower()[:18] not in covered
        ]
        if missing:
            cleaned.insert(
                0,
                {
                    "id": "user_requested",
                    "title": "Kullanıcının istediği (öncelikli)",
                    "priority": "must",
                    "features": [
                        {
                            "key": _slug(text, i),
                            "title": text,
                            "description": "Kullanıcı bu özelliği açıkça istedi; ilk sırada ve eksiksiz yapılmalı.",
                            "acceptance": ["İstenen davranış uçtan uca çalışıyor",
                                           "Arayüzde keşfedilebilir ve belgelenmiş"],
                            "user_requested": True,
                        }
                        for i, text in enumerate(missing)
                    ],
                },
            )
        blueprint["feature_groups"] = cleaned
        return blueprint

    def _depth_counts(self, blueprint: Dict):
        groups = [g for g in (blueprint.get("feature_groups") or []) if isinstance(g, dict)]
        features = sum(len(g.get("features") or []) for g in groups)
        return len(groups), features

    def _needs_depth(self, blueprint: Dict) -> bool:
        min_features = int(_cfg("blueprint_min_features", 30))
        min_groups = int(_cfg("blueprint_min_groups", 8))
        group_count, feature_count = self._depth_counts(blueprint)
        return feature_count < min_features or group_count < min_groups

    def _expand(self, request: str, dossier: Dict, benchmark: Dict, blueprint: Dict) -> list:
        """Ilk spec yuzeyselse domain-specific ek ozellikler uret."""
        group_count, feature_count = self._depth_counts(blueprint)
        min_features = int(_cfg("blueprint_min_features", 30))
        min_groups = int(_cfg("blueprint_min_groups", 8))
        target_features = max(min_features - feature_count, 6)
        target_groups = max(min_groups - group_count, 2)
        existing = [
            str(f.get("title") or "")
            for g in (blueprint.get("feature_groups") or [])
            for f in (g.get("features") or [])
            if isinstance(f, dict) and f.get("title")
        ][:60]
        dossier = dossier or {}
        analysis = (benchmark or {}).get("analysis") or {}
        if not isinstance(analysis, dict):
            analysis = {}
        user = (
            f"=== KULLANICI İSTEĞİ ===\n{request[:2500]}\n\n"
            f"=== MEVCUT SPESİFİKASYON ===\n{self.format_for_prompt(blueprint, limit=3500)}\n\n"
            f"=== MEVCUT ÖZELLİK BAŞLIKLARI (TEKRARLAMA) ===\n"
            + "\n".join(f"- {title}" for title in existing)
            + f"\n\n=== HEDEF ===\nEn az {target_features} YENİ özellik ve en az "
              f"{target_groups} yeni grup ekle.\n"
            f"Araştırma özeti: {str(dossier.get('summary') or '')[:1000]}\n"
            f"Rakip analizi: {str(analysis.get('what_best_looks_like') or '')[:1000]}\n"
        )
        try:
            data = llm.as_dict(
                llm.ask_json(EXPAND_SYSTEM, user, model=self.model, purpose="coding", temperature=0.4)
            )
            groups = data.get("feature_groups") if isinstance(data, dict) else None
            return groups if isinstance(groups, list) else []
        except Exception as exc:
            self.log.warn(f"Spesifikasyon genişletme turu başarısız: {exc}")
            return []

    def _merge_expansion(self, blueprint: Dict, new_groups: list) -> Dict:
        groups = blueprint.get("feature_groups") or []
        if not isinstance(groups, list):
            groups = []
        groups = [g for g in groups if isinstance(g, dict)]
        by_id = {str(g.get("id")): g for g in groups if isinstance(g, dict)}
        existing_titles = {
            _norm_title(f.get("title"))
            for g in groups for f in (g.get("features") or [])
            if isinstance(f, dict)
        }
        existing_keys = {
            str(f.get("key"))
            for g in groups for f in (g.get("features") or [])
            if isinstance(f, dict) and f.get("key")
        }
        added = 0
        for index, group in enumerate(new_groups or []):
            if not isinstance(group, dict):
                continue
            group_id = str(group.get("id") or _slug(group.get("title") or "ek", index))
            group_title = str(group.get("title") or "Ek özellikler").strip()
            target = by_id.get(group_id)
            if not target:
                target = {
                    "id": group_id,
                    "title": group_title,
                    "priority": str(group.get("priority") or "should").lower(),
                    "features": [],
                }
                groups.append(target)
                by_id[group_id] = target
            for position, feature in enumerate(group.get("features") or []):
                if not isinstance(feature, dict):
                    continue
                title = str(feature.get("title") or "").strip()
                if not title:
                    continue
                norm = _norm_title(title)
                if any(norm == other or norm in other or other in norm
                       for other in existing_titles if other):
                    continue
                acceptance = feature.get("acceptance") or []
                if isinstance(acceptance, list):
                    acceptance_list = [str(a).strip() for a in acceptance if str(a).strip()]
                else:
                    acceptance_list = [str(acceptance).strip()] if acceptance else []
                target.setdefault("features", []).append({
                    "key": _unique_feature_key(_slug(title, position), existing_keys),
                    "title": title,
                    "description": str(feature.get("description") or "").strip(),
                    "acceptance": acceptance_list,
                    "user_requested": False,
                })
                existing_titles.add(norm)
                added += 1
        if added:
            self.log.info(f"Domain genişletme: {added} ek özellik eklendi")
        blueprint["feature_groups"] = groups
        return blueprint

    def _ensure_depth(self, blueprint: Dict) -> Dict:
        """LLM yuzeysel kaldiysa tasarim/UX/kalite derinligini deterministik ekle."""
        min_features = int(_cfg("blueprint_min_features", 30))
        min_groups = int(_cfg("blueprint_min_groups", 8))
        groups = blueprint.get("feature_groups") or []
        groups = [g for g in groups if isinstance(g, dict)]
        by_id = {str(g.get("id")): g for g in groups}
        existing_titles = {
            _norm_title(f.get("title"))
            for g in groups for f in (g.get("features") or [])
            if isinstance(f, dict)
        }
        existing_keys = {
            str(f.get("key"))
            for g in groups for f in (g.get("features") or [])
            if isinstance(f, dict) and f.get("key")
        }
        added = 0
        # Sadece sayıyı doldurmak yetmez: kalite ve teslim grupları da
        # tamamlanmadan derinlik turu bitmemeli (SEO/performans/404/README vb.).
        essential = {"design": 4, "experience": 1, "quality": 4, "delivery": 1}

        def _essential_ready() -> bool:
            return all(
                len(by_id.get(group_id, {}).get("features") or []) >= needed
                for group_id, needed in essential.items()
            )

        for index, (group_id, group_title, title, description, acceptance) in enumerate(DEPTH_FEATURES):
            feature_count = sum(len(g.get("features") or []) for g in groups)
            if (feature_count >= min_features and len(groups) >= min_groups
                    and _essential_ready()):
                break
            norm = _norm_title(title)
            if any(norm == other or norm in other or other in norm for other in existing_titles if other):
                continue
            group = by_id.get(group_id)
            if not group:
                group = {
                    "id": group_id,
                    "title": group_title,
                    "priority": "should",
                    "features": [],
                }
                groups.append(group)
                by_id[group_id] = group
            group.setdefault("features", []).append({
                "key": _unique_feature_key(_feature_key(title, index), existing_keys),
                "title": title,
                "description": description,
                "acceptance": list(acceptance),
                "user_requested": False,
            })
            existing_titles.add(norm)
            added += 1
        blueprint["feature_groups"] = groups
        if added:
            self.log.info(f"Derinlik katmanı: {added} ek tasarım/UX/kalite özelliği eklendi")
        return blueprint

    @staticmethod
    def _default_stack(platform: str) -> Dict:
        if platform == "android":
            return {
                "language": "Kotlin", "framework": "Jetpack Compose",
                "database": "Room (SQLite)", "styling": "Compose Material 3",
                "build": "Gradle (Kotlin DSL)", "testing": "JUnit + Espresso + Compose UI Test",
                "deployment": "Play Store (AAB) + CI/CD", "rationale": [],
            }
        if platform == "ios":
            return {
                "language": "Swift", "framework": "SwiftUI",
                "database": "SwiftData / Core Data", "styling": "SwiftUI Design System",
                "build": "Xcode", "testing": "XCTest + XCUITest",
                "deployment": "App Store Connect", "rationale": [],
            }
        if platform == "game":
            return {
                "language": "JavaScript/TypeScript", "framework": "Three.js",
                "database": "localStorage / IndexedDB", "styling": "CSS + HUD overlay",
                "build": "Vite", "testing": "Vitest",
                "deployment": "statik barındırma", "rationale": [],
            }
        if platform == "backend_api":
            return {
                "language": "Python", "framework": "FastAPI",
                "database": "PostgreSQL + SQLAlchemy", "styling": "-",
                "build": "uv / Docker", "testing": "pytest",
                "deployment": "Docker + ters vekil", "rationale": [],
            }
        return {
            "language": "JavaScript", "framework": "React + Vite",
            "database": "SQLite (sunucu tarafı) veya localStorage",
            "styling": "CSS (design token tabanlı)", "build": "Vite",
            "testing": "Vitest", "deployment": "statik barındırma",
            "rationale": [],
        }

    @staticmethod
    def _fallback(request: str, dossier: Dict, benchmark: Dict) -> Dict:
        """LLM failsafe - smart stack selection based on request type."""
        domain = ((dossier.get("scope") or {}).get("domain")) or "proje"
        platform = (dossier.get("scope") or {}).get("platform", "web")
        must = [f.get("feature") for f in (dossier.get("must_have_features") or []) if isinstance(f, dict)]
        should = [f.get("feature") for f in (dossier.get("advanced_features") or []) if isinstance(f, dict)]
        analysis = (benchmark or {}).get("analysis") or {}

        # Detect simple static site from request/domain
        request_lower = (request or "").lower()
        domain_lower = (domain or "").lower()
        is_simple_static = platform == "web" and any(k in request_lower or k in domain_lower for k in [
            "haber", "news", "landing", "blog", "tek sayfa", "single page", 
            "static", "statik", "basit", "simple", "portfolio", "cv", "özgeçmiş",
            "documentation", "docs", "showcase", "gallery", "coming soon", "maintenance"
        ])

        def features(titles, prefix, user_requested=False):
            return [
                {
                    "key": _slug(t, i),
                    "title": t,
                    "description": "",
                    "acceptance": ["İstenen davranış çalışıyor"],
                    "user_requested": user_requested,
                }
                for i, t in enumerate([x for x in titles if x][:20])
            ]

        if is_simple_static:
            stack = {
                "language": "HTML/CSS/JS", "framework": "Vanilla (no framework)",
                "database": "none (static JSON data)", "styling": "CSS (vanilla, mobile-first)",
                "build": "none (static files)", "testing": "manual / browser",
                "deployment": "static hosting (Netlify, Vercel, GitHub Pages, etc.)",
                "rationale": ["Simple static site - no build step needed", "Zero dependencies, zero config"],
            }
            feature_groups = [
                {"id": "core", "title": "Temel", "priority": "must", "features": features([
                    "HTML structure (semantic, accessible)",
                    "CSS responsive layout (mobile-first)",
                    "Static news data (JSON/JS array)",
                    "News card grid with images",
                    "Header with site title/navigation",
                    "Footer with copyright"
                ], "core")},
                {"id": "enhance", "title": "Geliştirme", "priority": "should", "features": features([
                    "Category filter tabs",
                    "Search/filter news",
                    "Lazy-load images",
                    "Smooth scroll navigation"
                ], "enhance")},
            ]
        else:
            stack = BlueprintBuilder._default_stack(platform)
            feature_groups = [
                {"id": "core", "title": "Temel", "priority": "must", "features": features(must, "core")},
                {"id": "advanced", "title": "Gelişmiş", "priority": "should", "features": features(should, "adv")},
            ]

        return {
            "product_name": str(domain).title(),
            "one_liner": request[:200],
            "platform": platform,
            "stack": stack,
            "architecture": {"pattern": "", "modules": [], "data_model": []},
            "user_requested_features": [],
            "feature_groups": feature_groups,
            "differentiation": analysis.get("what_best_looks_like") or [],
            "quality_bar": dossier.get("quality_bar") or {},
            "navigation": [],
            "out_of_scope": [],
        }

    # ------------------------------------------------------------------
    @staticmethod
    def format_for_prompt(blueprint: Dict, limit: int = 9000) -> str:
        """Spesifikasyonu prompt'a sığdır (kalite bütçesi: token)."""
        blueprint = blueprint or {}
        stack = blueprint.get("stack") or {}
        lines = ["ÜRÜN SPESİFİKASYONU"]
        if blueprint.get("product_name"):
            lines.append(f"Ürün: {blueprint['product_name']} — {blueprint.get('one_liner', '')}")
        lines.append(f"Platform: {blueprint.get('platform', 'web')}")

        stack_bits = [
            f"{k}={v}" for k, v in stack.items()
            if k != "rationale" and v and v != "-"
        ]
        if stack_bits:
            lines.append("Teknoloji: " + ", ".join(stack_bits))
        for reason in (stack.get("rationale") or [])[:5]:
            lines.append(f"  gerekçe: {reason}")

        arch = blueprint.get("architecture") or {}
        if arch.get("pattern"):
            lines.append(f"Mimari: {arch['pattern']}")
        if arch.get("modules"):
            mod_items = []
            for item in arch["modules"][:20]:
                if isinstance(item, dict):
                    mod_items.append(item.get("name") or item.get("title") or str(item))
                else:
                    mod_items.append(str(item))
            lines.append("Modüller: " + ", ".join(mod_items))
        if arch.get("data_model"):
            dm_items = []
            for item in arch["data_model"][:25]:
                if isinstance(item, dict):
                    dm_items.append(item.get("name") or item.get("title") or str(item))
                else:
                    dm_items.append(str(item))
            lines.append("Veri modeli: " + ", ".join(dm_items))

        if blueprint.get("navigation"):
            nav_items = []
            for item in blueprint["navigation"][:15]:
                if isinstance(item, dict):
                    nav_items.append(item.get("label") or item.get("title") or str(item))
                else:
                    nav_items.append(str(item))
            lines.append("Navigasyon: " + " | ".join(nav_items))

        if blueprint.get("user_requested_features"):
            lines.append("KULLANICININ AÇIK İSTEKLERİ (bunlar kesinlikle yapılacak):")
            lines += [f"  ★ {f}" for f in blueprint["user_requested_features"][:20]]

        lines.append("Özellik grupları:")
        feature_total = sum(
            len(g.get("features") or [])
            for g in (blueprint.get("feature_groups") or [])
            if isinstance(g, dict)
        )
        # Cok ozellikli spec'lerde prompt'u kisaltirken SON gruplari kesmemek
        # icin once aciklama/kabul metinlerini sikistir; tum basliklar kalsin.
        compact = feature_total >= 18
        for group in blueprint.get("feature_groups") or []:
            lines.append(f"  [{group.get('priority', 'must')}] {group.get('title')} ({group.get('id')})")
            for feature in (group.get("features") or [])[:30]:
                star = "★" if feature.get("user_requested") else "-"
                description = str(feature.get("description") or "")
                if compact:
                    description = description[:90]
                else:
                    description = description[:180]
                acceptance_list = feature.get("acceptance") or []
                if isinstance(acceptance_list, list):
                    accepted = acceptance_list[:1 if compact else 3]
                    acceptance = "; ".join(str(a)[:90] for a in accepted if a)
                else:
                    acceptance = str(acceptance_list)[:90] if acceptance_list else ""
                lines.append(f"    {star} {feature.get('title')}: {description}")
                if acceptance:
                    lines.append(f"        kabul: {acceptance}")

        if blueprint.get("differentiation"):
            diff_items = []
            for item in blueprint["differentiation"][:10]:
                if isinstance(item, dict):
                    diff_items.append(item.get("title") or item.get("label") or str(item))
                else:
                    diff_items.append(str(item))
            lines.append("Farklılaştırıcılar: " + "; ".join(diff_items))

        quality = blueprint.get("quality_bar") or {}
        if quality:
            lines.append("Kalite çıtası:")
            for key in ("performance", "accessibility", "seo", "security"):
                items = quality.get(key) or []
                if items:
                    lines.append(f"  {key}: " + "; ".join(str(i)[:120] for i in items[:6]))

        if blueprint.get("out_of_scope"):
            oos_items = []
            for item in blueprint["out_of_scope"][:10]:
                if isinstance(item, dict):
                    oos_items.append(item.get("title") or item.get("label") or str(item))
                else:
                    oos_items.append(str(item))
            lines.append("Kapsam dışı: " + "; ".join(oos_items))

        return "\n".join(lines)[:limit]


# Modul seviyesi kisayol: diger moduller `from .blueprint import
# format_for_prompt` ile dogrudan cagirabilsin.
format_for_prompt = BlueprintBuilder.format_for_prompt


# ----------------------------------------------------------------------
def _slug(text: str, index: int = 0) -> str:
    import re
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return (slug or f"item_{index}")[:48]


def _dossier_blob(dossier: Dict, limit: int = 14000) -> str:
    dossier = dossier or {}
    lines = []
    if dossier.get("summary"):
        lines.append(f"Özet: {dossier['summary']}")
    if dossier.get("how_it_works"):
        lines.append(f"Nasıl çalışır: {dossier['how_it_works']}")
    for key, label in (
        ("must_have_features", "Zorunlu özellikler"),
        ("advanced_features", "Gelişmiş özellikler"),
        ("pitfalls", "Tuzaklar"),
        ("tech_stack", "Önerilen teknoloji"),
    ):
        items = dossier.get(key) or []
        if items:
            lines.append(f"{label}:")
            for item in items[:20]:
                if isinstance(item, dict):
                    title = item.get("feature") or item.get("pitfall") or item.get("choice") or ""
                    detail = item.get("why") or item.get("consequence") or item.get("rationale") or ""
                    lines.append(f"  - {title}: {detail}")
                else:
                    lines.append(f"  - {item}")
    if dossier.get("ux_principles"):
        ux_items = []
        for item in dossier["ux_principles"][:12]:
            if isinstance(item, dict):
                ux_items.append(item.get("title") or item.get("label") or str(item))
            else:
                ux_items.append(str(item))
        lines.append("UX ilkeleri: " + "; ".join(ux_items))
    quality = dossier.get("quality_bar") or {}
    for key in ("performance", "accessibility", "seo", "security"):
        if quality.get(key):
            lines.append(f"Kalite/{key}: " + "; ".join(str(i)[:120] for i in quality[key][:8]))
    if dossier.get("differentiation_opportunities"):
        diff_items = []
        for item in dossier["differentiation_opportunities"][:10]:
            if isinstance(item, dict):
                diff_items.append(item.get("title") or item.get("label") or str(item))
            else:
                diff_items.append(str(item))
        lines.append("Fırsatlar: " + "; ".join(diff_items))
    if dossier.get("open_questions"):
        oq_items = []
        for item in dossier["open_questions"][:10]:
            if isinstance(item, dict):
                oq_items.append(item.get("title") or item.get("label") or str(item))
            else:
                oq_items.append(str(item))
        lines.append("Açık sorular: " + "; ".join(oq_items))
    return "\n".join(lines)[:limit]


def _benchmark_blob(benchmark: Dict, limit: int = 8000) -> str:
    if not benchmark:
        return "(rakip analizi yok)"
    analysis = benchmark.get("analysis") or {}
    matrix = benchmark.get("feature_matrix") or {}
    lines = []
    for key, label in (
        ("baseline_features", "Taban özellikler"),
        ("differentiators", "Farklılaştırıcılar"),
        ("gaps", "Boşluklar/fırsatlar"),
    ):
        items = analysis.get(key) or []
        if items:
            lines.append(f"{label}:")
            for item in items[:16]:
                if isinstance(item, dict):
                    title = item.get("feature") or item.get("gap") or ""
                    detail = item.get("why") or item.get("value") or item.get("opportunity") or ""
                    seen = item.get("seen_in")
                    lines.append(f"  - {title}{f' [{seen}]' if seen else ''}: {detail}")
                else:
                    lines.append(f"  - {item}")
    best = analysis.get("what_best_looks_like") or []
    if best:
        lines.append("En iyi hali: " + "; ".join(best[:10]))
    averages = matrix.get("averages") or {}
    if averages:
        lines.append("Ortalama ölçümler: " + ", ".join(f"{k}={v}" for k, v in averages.items()))
    features = matrix.get("features") or []
    if features:
        lines.append("Özellik yaygınlığı: " + ", ".join(
            f"{f['feature']}({f['sites']}/{f['of']})" for f in features[:25]
        ))
    return "\n".join(lines)[:limit]


def build_blueprint(request: str, dossier: Dict, benchmark: Dict, run_logger: RunLogger,
                    model: str = None) -> Dict:
    return BlueprintBuilder(run_logger, model=model).build(request, dossier, benchmark)


import asyncio

async def build_blueprint_async(request: str, dossier: Dict, benchmark: Dict, run_logger: RunLogger,
                                model: str = None) -> Dict:
    """Async version of build_blueprint."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: build_blueprint(request, dossier, benchmark, run_logger, model))