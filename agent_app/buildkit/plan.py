"""Yol haritasi: ayrıntili, sirali, dogrulanabilir is kalemleri.

Plan, "yapay zekâ kendi kendine yapsin" diyebildigimiz seyin denetlenebilir
surudur. Her kalem:

  * tek bir is kalemidir (bir seferde LLM'in kafasini doldurmaz)
  * kabul kriteri tasir (dogrulama komutu veya kontrol)
  * bagimliliklarini bildirir
  * calisma sonrasi "done / failed" durumu ve kaniti kalir

Kalicilik iki katmanlidir:
  * task_runs/<run_id>.plan.json  (atomic yazim; worker cokse bile plan durur)
  * BuildPlanItem tablosu          (sorgulanabilir, admin'de gorunur)
"""
import json
import logging
import re
import unicodedata
from typing import Dict, List, Optional

from django.conf import settings

from . import llm
from .events import RunLogger, utcnow

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


# Asla bitmeyen (dev sunucusu / izleme) komut ipuclari. Dogrulamada bunlari
# calistirmak timeout'tan baska bir sey degil: "npm run dev" 180 sn sonra
# hep "Command timed out after 180s" ile kalem basarisiz sayiyordu.
_LONG_RUNNING_HINTS = (
    " run dev", " start", " serve", " preview", " watch", " runserver",
    "flask run", "uvicorn", "next dev", "vite", "webpack-dev-server", "nodemon",
)


def is_long_running_command(cmd: str) -> bool:
    """'npm run dev' gibi bitmeyen komut mu? (build/test/lint bitirir)."""
    text = " ".join((cmd or "").lower().split())
    if not text:
        return False
    # Zincirdeki SON komut belirleyici: "npm run build && npm run dev" tehlikeli.
    tail = text.split("&&")[-1].strip()
    return any(hint in tail for hint in _LONG_RUNNING_HINTS)


def sanitize_verify_command(cmd: str) -> str:
    """Bitmeyen dogrulama komutlarini bosalt; executor dosya kontrolune duser."""
    text = str(cmd or "").strip()[:200]
    if is_long_running_command(text):
        logger.warning("Bitmeyen dogrulama komutu bos birakildi: %s", text)
        return ""
    return text


# Uygulama sirası. Model bunu bozabilir; biz duzeltiriz.
PHASE_ORDER = (
    "scaffold",
    "foundation",
    "data",
    "backend",
    "frontend",
    "integrations",
    "quality",
    "hardening",
    "delivery",
    "polish",
)
PHASE_LABELS = {
    "scaffold": "İskelet ve kurulum",
    "foundation": "Temel yapı",
    "data": "Veri modeli",
    "backend": "İş mantığı ve API",
    "frontend": "Arayüz",
    "integrations": "Entegrasyonlar",
    "quality": "Test ve kalite araçları",
    "hardening": "Güvenlik, SEO, performans",
    "delivery": "Teslim ve çalıştırma",
    "polish": "Son rötuş ve doğrulama",
}

# Blueprint grup id -> plan fazı
GROUP_PHASE = {
    "core": "foundation",
    "user_requested": "foundation",
    "design": "frontend",
    "experience": "frontend",
    "content": "frontend",
    "discovery": "frontend",
    "engagement": "frontend",
    "membership": "backend",
    "admin": "backend",
    "quality": "quality",
    "delivery": "delivery",
    "polish": "polish",
    "advanced": "frontend",
    "enhance": "frontend",
}

# LLM plan yuzeysel kalirsa eklenecek zorunlu derinlik kalemleri.
DEPTH_PLAN_ITEMS = (
    ("design token", "frontend", "Tasarım token'larını ve görsel dili kur",
     "Renk, tipografi, boşluk ve durum stillerini tek bir tasarım sisteminde tanımla.",
     "CSS değişkenleri/design token dosyası oluştur; tüm bileşenler bunları kullansın.",
     ["Renkler tek yerden değiştirilebiliyor", "Kontrast erişilebilir seviyede"]),
    ("tipografi", "frontend", "Tipografi ölçeğini ve hiyerarşiyi uygula",
     "Başlık, gövde ve yardımcı metinler için tutarlı ölçek oluştur.",
     "H1-H6, gövde ve caption stillerini tanımla; mobilde okunabilirliği doğrula.",
     ["Başlık hiyerarşisi tutarlı", "Mobil satır yüksekliği okunabilir"]),
    ("responsive", "frontend", "Responsive düzeni ve mobil navigasyonu tamamla",
     "Mobil, tablet ve masaüstü kırılma noktalarını uygula.",
     "Grid/flex düzenini kırılma noktalarına göre ayarla; mobil menü ekle.",
     ["Yatay kaydırma yok", "Mobil menü açılıp kapanıyor"]),
    ("bileşen kütüphanesi", "frontend", "Yeniden kullanılabilir bileşenleri standartlaştır",
     "Buton, kart, form, rozet ve modal bileşenlerini ortak stil ve davranışa bağla.",
     "Tekrarlanan UI parçalarını bileşenleştir; hover/focus/disabled durumlarını uygula.",
     ["Aynı bileşen her yerde tutarlı", "Tüm durumlar (hover/focus/disabled) çalışıyor"]),
    ("yükleniyor", "frontend", "Yükleniyor, boş ve hata durumlarını ekle",
     "Veri beklenirken, sonuç yokken ve hata olduğunda anlamlı ekranlar göster.",
     "Skeleton/spinner, empty state ve retry butonu ekle.",
     ["Boş sonuçta mesaj var", "Hata durumunda tekrar dene çalışıyor"]),
    ("erişilebilirlik", "quality", "Erişilebilirlik denetimini tamamla",
     "Semantik HTML, ARIA, klavye gezinmesi ve kontrastı düzelt.",
     "Başlık hiyerarşisi, alt metinler, focus görünürlüğü ve Tab sırasını doğrula.",
     ["Klavye ile tüm akış kullanılabiliyor", "Kontrast ve alt metinler tamam"]),
    ("seo", "hardening", "SEO meta etiketlerini ve sitemap'i ekle",
     "Sayfa başlıkları, açıklamalar, Open Graph ve robots/sitemap ekle.",
     "Her sayfa için benzersiz title/description; OG etiketleri; robots.txt/sitemap.",
     ["Her sayfada benzersiz başlık/açıklama", "OG etiketleri mevcut"]),
    ("performans", "hardening", "Performans iyileştirmelerini uygula",
     "Görselleri optimize et, lazy loading ve önbellek ekle.",
     "Görsel boyutları, lazy loading, kritik CSS ve önbellek başlıkları.",
     ["Görseller lazy yükleniyor", "İlk yükleme makul sürede"]),
    ("404", "delivery", "404 ve hata sayfalarını ekle",
     "Geçersiz URL ve beklenmeyen hatalar için kullanıcı dostu sayfa oluştur.",
     "404 sayfası ve ana sayfaya dönüş bağlantısı ekle.",
     ["404 sayfası mevcut", "Ana sayfaya dönüş çalışıyor"]),
    ("seed", "data", "Örnek/seed içerik ekle",
     "Uygulama ilk açılışta boş görünmesin diye gerçekçi örnek veri ekle.",
     "Seed veri dosyası ve ilk yükleme mantığı ekle.",
     ["İlk açılışta içerik görünüyor", "Örnek veri kolayca değiştirilebiliyor"]),
)


PLAN_SYSTEM = """Sen teknik ekip liderisin. Sana bir ürün spesifikasyonu verilir.
Bunu **uygulanabilir, sıralı iş kalemlerine** dönüştür.

KURALLAR:
- Her kalem TEK bir işi tarif etsin. "Site yap" gibi kalemler yasak.
- Kalemler bağımlılık sırasına göre dizilsin; bir kalem öncekine dayanıyorsa
  `depends_on` ile belirt.
- `files` alanında kalemin dokunacağı/yapacağı DOSYA YOLLARINI önceden yaz.
  Bu yollar proje köküne görelidir (örn. `src/App.jsx`). Dosya adını uydurma:
  yığın (stack) ile tutarlı olsun.
- `verify_command` çalıştırılabilir ve **biten** bir komut olsun (build/lint/test)
  ya da boş bırak. `npm run dev`, `npm start`, `vite`, `next dev`, `npm run preview`
  gibi asla bitmeyen sunucu komutları YASAK — bunlar doğrulamayı her seferinde
  zaman aşımına uğratır. Bitmeyen komut verirsen sistem onu boş sayar.
- Kullanıcının açıkça istediği her madde mutlaka bir kaleme dönüşmeli; bu
  kalemler `user_requested: true` taşır ve `must` önceliklidir.
- Spesifikasyondaki HER özellik en az bir kalemde karşılanmalı; özellik atlama.
- Tasarım sistemi/token, tipografi, responsive düzen, mobil menü, bileşen
  kütüphanesi, yükleniyor/boş/hata durumları, mikro etkileşimler, erişilebilirlik,
  SEO, performans, 404, örnek veri, test ve dokümantasyon için ayrı kalemler ekle.
- Kalem sayısı 32-70 arasında olsun. Her fazda en az bir kalem olsun.
- `objective` tek cümle, `detail` ise ne yapılacağını anlatan 1-4 cümledir.

JSON ŞEMA:
{
  "phases": [
    {
      "id": "scaffold|foundation|data|backend|frontend|integrations|quality|hardening|delivery|polish",
      "items": [
        {
          "title": "kısa fiil cümlesi",
          "phase": "scaffold",
          "objective": "tek cümle",
          "detail": "yapılacaklar",
          "files": ["yol/buraya.js"],
          "acceptance": ["kontrol 1", "kontrol 2"],
          "verify_command": "npm run build",
          "depends_on": ["önceki kalemin kısa başlığı"],
          "complexity": 1-5,
          "user_requested": false
        }
      ]
    }
  ]
}"""


class PlanItem:
    """Tek bir plan kalemi."""

    __slots__ = ("key", "order", "phase", "title", "objective", "detail", "files",
                 "acceptance", "verify_command", "depends_on", "complexity",
                 "user_requested", "status", "attempts", "result_summary",
                 "verify_output", "source")

    def __init__(self, **kwargs):
        self.key = kwargs.get("key") or ""
        self.order = int(kwargs.get("order") or 0)
        self.phase = kwargs.get("phase") or "foundation"
        self.title = kwargs.get("title") or ""
        self.objective = kwargs.get("objective") or ""
        self.detail = kwargs.get("detail") or ""
        self.files = list(kwargs.get("files") or [])
        self.acceptance = list(kwargs.get("acceptance") or [])
        self.verify_command = sanitize_verify_command(kwargs.get("verify_command"))
        self.depends_on = list(kwargs.get("depends_on") or [])
        self.complexity = int(kwargs.get("complexity") or 3)
        self.user_requested = bool(kwargs.get("user_requested"))
        self.status = kwargs.get("status") or "pending"
        self.attempts = int(kwargs.get("attempts") or 0)
        self.result_summary = kwargs.get("result_summary") or ""
        self.verify_output = kwargs.get("verify_output") or ""
        self.source = kwargs.get("source") or "plan"

    def to_dict(self) -> Dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    @classmethod
    def from_dict(cls, data: Dict) -> "PlanItem":
        return cls(**{k: v for k, v in (data or {}).items() if k in cls.__slots__})

    @property
    def phase_label(self) -> str:
        return PHASE_LABELS.get(self.phase, self.phase)

    def done(self) -> bool:
        return self.status == "done"

    def failed(self) -> bool:
        return self.status in ("failed", "skipped")


class BuildPlan:
    """Kalem listesi + faz gruplari."""

    def __init__(self, items: List[PlanItem], meta: Optional[Dict] = None):
        self.items = [
            item if isinstance(item, PlanItem) else PlanItem.from_dict(item)
            for item in (items or [])
        ]
        self.meta = dict(meta or {})

    # -- erisim ---------------------------------------------------------
    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def pending(self) -> List[PlanItem]:
        return [i for i in self.items if i.status == "pending"]

    def done(self) -> List[PlanItem]:
        return [i for i in self.items if i.status == "done"]

    def failed(self) -> List[PlanItem]:
        return [i for i in self.items if i.status == "failed"]

    def skipped(self) -> List[PlanItem]:
        return [i for i in self.items if i.status == "skipped"]

    def by_phase(self) -> Dict[str, List[PlanItem]]:
        groups: Dict[str, List[PlanItem]] = {}
        for item in self.items:
            groups.setdefault(item.phase, []).append(item)
        return groups

    def progress(self) -> Dict:
        total = len(self.items)
        done = len(self.done())
        failed = len(self.failed())
        skipped = len(self.skipped())
        return {
            "total": total,
            "done": done,
            "failed": failed,
            "skipped": skipped,
            "pending": total - done - failed - skipped,
            "percent": int(done * 100 / total) if total else 0,
        }

    def ready_items(self) -> List[PlanItem]:
        """Bagimliliklari tamamlanmis, henuz denenmemis kalemler."""
        finished = {i.key for i in self.items if i.status == "done"}
        out = []
        for item in self.items:
            if item.status != "pending":
                continue
            if all(self._resolve(dep) in finished for dep in item.depends_on):
                out.append(item)
        return out

    def _resolve(self, dep: str) -> str:
        """Bagimlilik adi -> kalem anahtari (baslik eslesmesi)."""
        needle = _slug(dep, 0)
        for item in self.items:
            if item.key == dep or needle in item.key or needle == _slug(item.title, 0):
                return item.key
        return _slug(dep, 0)

    # -- donusum --------------------------------------------------------
    def to_dict(self) -> Dict:
        return {"meta": self.meta, "items": [i.to_dict() for i in self.items]}

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict) -> "BuildPlan":
        data = data or {}
        
        # Support both formats: {items: [...], meta: {...}} and {phases: [...]}
        items = []
        meta = data.get("meta") or {}
        
        if "items" in data:
            # New format: items directly
            items = [PlanItem.from_dict(i) for i in (data.get("items") or [])]
        elif "phases" in data:
            # Legacy format: phases with items inside
            for phase in data.get("phases") or []:
                for item in phase.get("items") or []:
                    item_with_phase = dict(item)
                    item_with_phase.setdefault("phase", phase.get("id"))
                    item_with_phase.setdefault("phase_label", phase.get("label"))
                    items.append(PlanItem.from_dict(item_with_phase))
            # Also check for flat items in phases
            if "items" in data and not isinstance(data.get("items"), list):
                pass  # handled above
        
        return cls(items, meta)

    def save(self, run_logger: RunLogger) -> bool:
        return run_logger.save("plan", self.to_dict())

    @classmethod
    def load(cls, run_logger: RunLogger) -> Optional["BuildPlan"]:
        data = run_logger.load("plan")
        if not data:
            return None
        return cls.from_dict(data)

    def sync_db(self, run_logger: RunLogger):
        """DB tablosunu guncelle (migration uygulanmamissa sessizce gecer)."""
        try:
            from agent_app.models import BuildPlanItem
        except Exception:
            return
        try:
            run = _db_run(run_logger)
            if run is None:
                return
            for item in self.items:
                BuildPlanItem.objects.update_or_create(
                    run=run,
                    key=item.key,
                    defaults={
                        "order": item.order,
                        "phase": item.phase,
                        "title": item.title,
                        "objective": item.objective,
                        "detail": item.detail,
                        "acceptance": item.acceptance,
                        "expected_files": item.files,
                        "verify_command": item.verify_command[:200],
                        "depends_on": item.depends_on,
                        "complexity": item.complexity,
                        "source": item.source,
                        "status": item.status,
                        "attempts": item.attempts,
                        "result_summary": item.result_summary[:4000],
                        "verify_output": item.verify_output[:8000],
                    },
                )
        except Exception as exc:
            logger.debug("plan DB senkronu atlandi: %s", exc)

    def format_for_prompt(self, include_done: bool = False, limit: int = 8000) -> str:
        lines = []
        for phase, items in self.by_phase().items():
            shown = [i for i in items if include_done or i.status != "done"]
            if not shown:
                continue
            lines.append(f"[{PHASE_LABELS.get(phase, phase)}]")
            for item in shown:
                mark = {"done": "[x]", "failed": "[!]", "in_progress": "[>]"}.get(item.status, "[ ]")
                lines.append(f"  {mark} {item.title} — {item.objective}")
        return "\n".join(lines)[:limit]


def _db_run(run_logger: RunLogger):
    from agent_app.models import BuildRun

    return BuildRun.objects.filter(run_id=run_logger.run_id).first()


# ----------------------------------------------------------------------
class PlanBuilder:
    """Spesifikasyondan uygulanabilir plan uretir."""

    def __init__(self, run_logger: RunLogger, model: str = None):
        self.log = run_logger
        self.model = model
        self.max_items = int(_cfg("max_plan_items", 500))
        self.min_items = int(_cfg("min_plan_items", 32))

    def build(self, request: str, blueprint: Dict, dossier: Dict = None) -> BuildPlan:
        self.log.stage("plan", "Uygulama planı oluşturuluyor")

        items = self._synthesize(request, blueprint)
        if not items:
            items = self._fallback(blueprint)
        items = self._harden(items, blueprint)

        plan = BuildPlan(items, meta={
            "generated_at": utcnow(),
            "request": request[:2000],
            "product_name": (blueprint or {}).get("product_name", ""),
            "platform": (blueprint or {}).get("platform", "web"),
        })
        plan.save(self.log)
        plan.sync_db(self.log)

        progress = plan.progress()
        self.log.emit_plan(plan)
        self.log.status(
            f"Plan hazır: {progress['total']} kalem, {len(plan.by_phase())} faz "
            f"({progress['pending']} bekliyor)"
        )
        self.log.stage("plan", f"Plan tamamlandı ({progress['total']} kalem)")
        return plan

    # ------------------------------------------------------------------
    def _synthesize(self, request: str, blueprint: Dict) -> List[PlanItem]:
        from .blueprint import format_for_prompt

        user = (
            f"=== KULLANICI İSTEĞİ ===\n{request[:3000]}\n\n"
            f"=== SPESİFİKASYON ===\n{format_for_prompt(blueprint)}\n\n"
            f"Kalem sayısı hedefi: {self.min_items}-{self.max_items}.\n"
            "Bu spesifikasyonu uygulama planına dönüştür."
        )
        data = llm.as_dict(
            llm.ask_json(PLAN_SYSTEM, user, model=self.model, purpose="coding", temperature=0.25)
        )
        raw_items = []
        for phase in (data.get("phases") or []):
            if not isinstance(phase, dict):
                continue
            for entry in phase.get("items") or []:
                if isinstance(entry, dict):
                    entry.setdefault("phase", phase.get("id", "foundation"))
                    raw_items.append(entry)

        items: List[PlanItem] = []
        used_keys = set()
        for index, entry in enumerate(raw_items):
            item = self._to_item(entry, index, used_keys)
            if item:
                items.append(item)
        return items

    def _to_item(self, entry: Dict, index: int, used_keys: set) -> Optional[PlanItem]:
        title = str(entry.get("title") or "").strip()
        if not title:
            return None
        phase = str(entry.get("phase") or "foundation").strip().lower()
        if phase not in PHASE_ORDER:
            phase = "foundation"
        key = _unique_key(_slug(title, index), used_keys)
        files = [str(f).strip().lstrip("/") for f in (entry.get("files") or []) if str(f).strip()]
        acceptance = entry.get("acceptance") or []
        if isinstance(acceptance, list):
            acceptance_list = [str(a).strip() for a in acceptance if str(a).strip()]
        else:
            acceptance_list = [str(acceptance).strip()] if acceptance else []
        return PlanItem(
            key=key,
            order=index,
            phase=phase,
            title=title,
            objective=str(entry.get("objective") or "").strip() or title,
            detail=str(entry.get("detail") or "").strip(),
            files=files,
            acceptance=acceptance_list,
            verify_command=sanitize_verify_command(entry.get("verify_command")),
            depends_on=[str(d).strip() for d in (entry.get("depends_on") or []) if str(d).strip()],
            complexity=max(1, min(5, int(entry.get("complexity") or 3))),
            user_requested=bool(entry.get("user_requested")),
            source="plan",
        )

    # ------------------------------------------------------------------
    def _harden(self, items: List[PlanItem], blueprint: Dict) -> List[PlanItem]:
        """Eksikleri kapat: zorunlu fazlar, kullanıcı istekleri, bagimlilik onarimi."""
        requested = [str(f) for f in ((blueprint or {}).get("user_requested_features") or [])]
        covered = " ".join(f"{i.title} {i.objective} {i.detail}".lower() for i in items)

        # 1) Kullanıcı istekleri karşılanmamışsa özel kalemler ekle.
        #    Faz "foundation": iskelet (scaffold) kurulduktan hemen sonra,
        #    ama ürünün diğer iş mantığından önce çalışsınlar.
        user_items: List[PlanItem] = []
        used_keys = {i.key for i in items}
        for index, text in enumerate(requested):
            needle = _slug(text, 0)[:14]
            if needle and needle in covered.replace(" ", "_"):
                continue
            user_items.append(PlanItem(
                key=_unique_key(_slug(text, 0), used_keys),
                order=index,
                phase="foundation",
                title=f"Kullanıcı isteği: {text}",
                objective=f"Kullanıcının açıkça istediği '{text}' özelliğini eksiksiz uygula.",
                detail=(
                    "Bu madde kullanıcının doğrudan isteğidir; planın en önünde durur ve "
                    "kabul kriterleri doğrulanmadan tamamlanmış sayılmaz."
                ),
                acceptance=[
                    "İstenen davranış uçtan uca çalışıyor",
                    "Arayüzde keşfedilebilir",
                    "Hata durumları da ele alınmış",
                ],
                complexity=4,
                user_requested=True,
                source="user",
            ))
            covered += " " + text.lower()
        if user_items:
            items = user_items + items

        # 2) Spesifikasyondaki her özellik için plan kalemi garantile.
        #    LLM planı özellikleri atlarsa tasarım/özellik derinliği kayboluyordu.
        covered_cores = " ".join(_core_slug(f"{i.title} {i.objective}") for i in items)
        feature_items: List[PlanItem] = []
        used_keys = {i.key for i in items}
        for group in (blueprint or {}).get("feature_groups") or []:
            if not isinstance(group, dict):
                continue
            phase = GROUP_PHASE.get(str(group.get("id")), "frontend")
            for feature in group.get("features") or []:
                if not isinstance(feature, dict):
                    continue
                title = str(feature.get("title") or "").strip()
                if not title:
                    continue
                probe = _core_slug(title)
                if probe and probe in covered_cores:
                    continue
                acceptance = feature.get("acceptance") or []
                if isinstance(acceptance, list):
                    acceptance_list = [str(a).strip() for a in acceptance if str(a).strip()]
                else:
                    acceptance_list = [str(acceptance).strip()] if acceptance else []
                feature_items.append(PlanItem(
                    key=_unique_key(_slug(title, 0), used_keys),
                    order=len(items) + len(feature_items),
                    phase=phase,
                    title=f"{title} özelliğini uygula",
                    objective=str(feature.get("description") or title).strip() or title,
                    detail=(
                        "Spesifikasyondaki bu özellik atlanamaz; kabul kriterleri "
                        "doğrulanmadan kalem tamamlanmış sayılmaz."
                    ),
                    acceptance=acceptance_list or ["Özellik uçtan uca çalışıyor"],
                    complexity=3,
                    user_requested=bool(feature.get("user_requested")),
                    source="spec",
                ))
                covered_cores += " " + probe
        if feature_items:
            items.extend(feature_items)

        # 3) Hiç kalem yoksa / çok az ise iskelet fazları ekle.
        if len(items) < self.min_items:
            items.extend(self._scaffold_items(blueprint, start=len(items)))

        # 4) Zorunlu kalite kalemleri yoksa ekle.
        titles = " ".join(i.title.lower() for i in items)
        mandatory = (
            ("test", "quality", "Temel testleri yaz", "En kritik akışlar için otomatik testler ekle."),
            ("readme", "delivery", "README ve çalıştırma dokümanı yaz", "Kurulum, çalıştırma ve test talimatlarını yaz."),
            ("hata", "quality", "Hata takibi ve loglama kur", "Merkezi hata toplama ve istek loglaması ekle."),
            ("güvenlik", "hardening", "Güvenlik seridi uygula", "Input doğrulama, yetki kontrolü ve güvenli varsayılanları uygula."),
            ("seed", "data", "Örnek/seed verisi ekle", "Ürünü ilk açılışta anlamlı gösterecek örnek veri ekle."),
        )
        for needle, phase, title, detail in mandatory:
            if needle in titles:
                continue
            items.append(PlanItem(
                key=_unique_key(_slug(title, 0), {i.key for i in items}),
                order=len(items),
                phase=phase,
                title=title,
                objective=title,
                detail=detail,
                acceptance=["Madde doğrulandı ve projeye işlendi"],
                complexity=2,
                source="guard",
            ))
            titles += " " + title.lower()

        # 5) Tasarım/UX/kalite derinliği: plan hâlâ minimumun altındaysa
        #    deterministik kalemlerle tamamla (LLM'in "kısa plan" davranışına karşı).
        existing_titles = [i.title for i in items]
        for needle, phase, title, objective, detail, acceptance in DEPTH_PLAN_ITEMS:
            if len(items) >= self.min_items:
                break
            if needle in titles or _title_seen(title, existing_titles):
                continue
            items.append(PlanItem(
                key=_unique_key(_slug(title, 0), {i.key for i in items}),
                order=len(items),
                phase=phase,
                title=title,
                objective=objective,
                detail=detail,
                acceptance=list(acceptance),
                complexity=3,
                source="guard",
            ))
            existing_titles.append(title)
            titles += " " + title.lower()

        # 6) Sıralama + bağımlılık onarımı.
        #    Aşırı uzun planlarda önce LLM kaynaklı fazla kalemleri kırp;
        #    kullanıcı istekleri, spesifikasyon kapsamı ve kalite guard'ları korunur.
        if len(items) > self.max_items:
            protected = [i for i in items if i.source in ("user", "spec", "guard")]
            rest = [i for i in items if i.source not in ("user", "spec", "guard")]
            items = protected + rest[: max(0, self.max_items - len(protected))]
        items.sort(key=lambda i: (PHASE_ORDER.index(i.phase), i.order, i.title.lower()))
        known = {i.key for i in items}
        seen_titles = {}
        for position, item in enumerate(items):
            item.order = position
            repaired = []
            for dep in item.depends_on:
                resolved = _match_key(dep, items)
                if resolved and resolved != item.key and resolved not in repaired:
                    repaired.append(resolved)
            item.depends_on = repaired
            # Hiç bağımlılığı yoksa aynı fazın bir önceki kalemine zincirle: sıra garanti olsun.
            if not repaired and position > 0:
                previous = items[position - 1]
                if previous.phase == item.phase:
                    item.depends_on = [previous.key]
        return items[: self.max_items]

    def _scaffold_items(self, blueprint: Dict, start: int) -> List[PlanItem]:
        stack = (blueprint or {}).get("stack") or {}
        language = stack.get("language") or "JavaScript"
        framework = stack.get("framework") or "React + Vite"
        build_cmd = _guess_build_command(stack)
        test_cmd = _guess_test_command(stack)
        used = set()

        def make(key, phase, title, objective, detail, files, acceptance, verify, complexity=2):
            return PlanItem(
                key=_unique_key(key, used), order=start, phase=phase, title=title,
                objective=objective, detail=detail, files=files, acceptance=acceptance,
                verify_command=verify, complexity=complexity, source="guard",
            )

        return [
            make("proje_iskeleti", "scaffold", "Proje iskeletini kur",
                 f"{language} + {framework} projesini ayağa kaldır",
                 "Gereken klasör yapısını, bağımlılık dosyasını ve geliştirme komutlarını oluştur.",
                 ["package.json", "README.md"],
                 ["Proje kurulum komutu çalışıyor", "Geliştirme komutu başlıyor"], build_cmd, 3),
            make("yapilandirma", "scaffold", "Yapılandırma ve ortam yönetimi kur",
                 "Ortam değişkenleri ve temel yapılandırmayı ekle",
                 "Örnek ortam dosyası, güvenli varsayılanlar ve okuma/yazma yardımcılarını oluştur.",
                 [".env.example", "src/config.js"],
                 ["Uygulama eksik ortam değişkeniyle çökmez"], ""),
            make("temel_veri_modeli", "data", "Temel veri modelini tanımla",
                 "Ürünün ana varlıklarını ve alanlarını oluştur",
                 "Spesifikasyondaki veri modeline uygun şema/migration/veri katmanı yaz.",
                 [], ["Ana varlıklar oluşturuldu", "Kalıcılık katmanı hazır"], ""),
            make("uygulama_iskeleti", "frontend", "Uygulama kabuğunu kur",
                 "Yönlendirme, düzen ve temel ekran iskeleti",
                 "Menü, yönlendirme, hata sınırı ve boş durum ekranlarını oluştur.",
                 [], ["Uygulama açılıyor ve gezinilebiliyor"], ""),
            make("temel_testler", "quality", "Temel test altyapısını kur",
                 "Test koşumu çalışan bir altyapı kur",
                 "Test çerçevesini yapılandır ve bir örnek test yaz.",
                 [], ["Test komutu hatasız çalışıyor"], test_cmd, 2),
        ]

    def _fallback(self, blueprint: Dict) -> List[PlanItem]:
        """LLM plan üretemezse: spesifikasyon özelliklerinden deterministik plan."""
        groups = (blueprint or {}).get("feature_groups") or []
        items: List[PlanItem] = []
        used = set()
        order = 0
        for group in groups:
            phase = GROUP_PHASE.get(str(group.get("id")), "foundation")
            for feature in group.get("features") or []:
                title = str(feature.get("title") or "").strip()
                if not title:
                    continue
                acceptance = feature.get("acceptance") or []
                if isinstance(acceptance, list):
                    acceptance_str = "; ".join(str(a) for a in acceptance if a)
                    acceptance_list = [str(a).strip() for a in acceptance if str(a).strip()]
                else:
                    acceptance_str = str(acceptance)
                    acceptance_list = [str(acceptance).strip()] if acceptance else []
                items.append(PlanItem(
                    key=_unique_key(_slug(title, 0), used), order=order, phase=phase,
                    title=f"{title} özelliğini uygula",
                    objective=str(feature.get("description") or title)[:400] or title,
                    detail=acceptance_str,
                    acceptance=acceptance_list,
                    complexity=3,
                    user_requested=bool(feature.get("user_requested")),
                    source="plan",
                ))
                order += 1
        return items


# ----------------------------------------------------------------------
def build_plan(request: str, blueprint: Dict, run_logger: RunLogger,
               dossier: Dict = None, model: str = None) -> BuildPlan:
    return PlanBuilder(run_logger, model=model).build(request, blueprint, dossier)


def ensure_plan_depth(plan: BuildPlan, blueprint: Dict = None,
                      min_items: int = None, max_items: int = None) -> BuildPlan:
    """Multi-agent planini da ayni derinlik kurallarindan gecirir.

    PlanningAgent kendi planini uretiyor; bu fonksiyon spesifikasyondaki her
    ozellik icin kalem oldugundan ve tasarim/UX/kalite kalemlerinin eksik
    kalmadigindan emin olur.
    """
    builder = PlanBuilder(None)
    if min_items is not None:
        builder.min_items = max(int(min_items), builder.min_items)
    if max_items is not None:
        builder.max_items = max(int(max_items), builder.min_items)
    plan.items = builder._harden(list(plan.items or []), blueprint or {})
    return plan


def append_items(plan: BuildPlan, new_items: List[Dict], run_logger: RunLogger,
                 phase: str = "polish") -> int:
    """Eleştiri turundan gelen yeni kalemleri plana ekler (yinelenmeden)."""
    if not new_items:
        return 0
    seen_keys = {i.key for i in plan.items}
    seen_titles = [i.title for i in plan.items]
    added = 0
    next_order = max([i.order for i in plan.items] or [0]) + 1

    for entry in new_items:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        # Ayni ya da benzer baslikli kalem tekrar eklenmez: "Haber listesi" ile
        # "Haber listesi ozelligini uygula" ayni is kalemidir.
        if _match_key(title, plan.items) or _title_seen(title, seen_titles):
            continue
        key = _unique_key(_slug(title, 0), seen_keys)
        item_phase = str(entry.get("phase") or phase)
        if item_phase not in PHASE_ORDER:
            item_phase = phase if phase in PHASE_ORDER else "polish"
        acceptance = entry.get("acceptance") or []
        if isinstance(acceptance, list):
            acceptance_list = [str(a).strip() for a in acceptance if str(a).strip()]
        else:
            acceptance_list = [str(acceptance).strip()] if acceptance else []
        plan.items.append(PlanItem(
            key=key, order=next_order, phase=item_phase, title=title,
            objective=str(entry.get("objective") or "").strip() or title,
            detail=str(entry.get("detail") or "").strip(),
            files=[str(f).strip().lstrip("/") for f in (entry.get("files") or []) if str(f).strip()],
            acceptance=acceptance_list,
            verify_command=sanitize_verify_command(entry.get("verify_command")),
            depends_on=[str(d).strip() for d in (entry.get("depends_on") or []) if str(d).strip()],
            complexity=max(1, min(5, int(entry.get("complexity") or 3))),
            source="critic",
        ))
        seen_keys.add(key)
        seen_titles.append(title)
        next_order += 1
        added += 1

    if added:
        plan.save(run_logger)
        plan.sync_db(run_logger)
    return added


# ----------------------------------------------------------------------
def _slug(text: str, index: int = 0) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return (slug or f"item_{index}")[:48]


def _unique_key(base: str, used) -> str:
    base = base or "item"
    if base not in used:
        used.add(base)
        return base
    counter = 2
    while f"{base}_{counter}" in used:
        counter += 1
    key = f"{base}_{counter}"
    used.add(key)
    return key


_ACTION_SUFFIXES = (
    "eklemek", "ekle", "yapmak", "yap", "kurmak", "kur", "olusturmak", "olustur",
    "uygulamak", "uygula", "yazmak", "yaz", "koymak", "koy", "baglamak", "bagla",
    "gelistirmek", "gelistir", "hazirlamak", "hazirla", "kurgulamak", "kurgula",
    "implement", "add", "create", "build", "make", "implementasyonu", "kullanimi",
)


def _core_slug(text: str) -> str:
    """Basligi fiil ekleri Soyulmus cekirdege indirger."""
    slug = _slug(text, 0)
    for suffix in _ACTION_SUFFIXES:
        for tail in (f"_{suffix}", suffix):
            if slug.endswith(tail) and len(slug) > len(tail) + 2:
                slug = slug[: -len(tail)]
                break
    return slug.strip("_")


def _title_seen(candidate: str, existing_titles: List[str]) -> bool:
    """Yeni baslik, planda zaten benzer bir kalem mi?

    Tam esitlik yeterli degil: "Haber listesi" ile "Haber listesi ozelligini
    uygula" ayni is kalemidir; ikisini de plana koymak is duplikasyonu yaratir.
    """
    normalized = " ".join(candidate.lower().split())
    if any(normalized == " ".join(str(t).lower().split()) for t in existing_titles):
        return True
    core = _core_slug(candidate)
    if not core:
        return False
    for title in existing_titles:
        other = _core_slug(title)
        if not other:
            continue
        if core == other or (len(core) >= 8 and (core in other or other in core)):
            return True
    return False


def _match_key(dep: str, items: List[PlanItem]) -> str:
    """Bagimlilik metnini (baslik veya anahtar) mevcut kalem anahtarina bagla."""
    raw = str(dep or "").strip()
    if not raw:
        return ""
    for item in items:
        if item.key == raw:
            return item.key
    needle = _slug(raw, 0)
    for item in items:
        if needle and needle in item.key:
            return item.key
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", raw.lower()) if len(w) > 3]
    if words:
        for item in items:
            haystack = f"{item.title} {item.objective}".lower()
            if all(word in haystack for word in words[:3]):
                return item.key
    return ""


def _guess_build_command(stack: Dict) -> str:
    framework = (stack.get("framework") or "").lower()
    if any(k in framework for k in ("django", "flask", "fastapi")):
        return "python manage.py check"
    if any(k in framework for k in ("react", "vue", "svelte", "angular", "next", "nuxt")):
        return "npm run build"
    if "vite" in (stack.get("build") or "").lower():
        return "npm run build"
    if (stack.get("language") or "").lower() in ("python",):
        return "python -c \"print('ok')\""
    if (stack.get("language") or "").lower() in ("kotlin", "java", "swift"):
        return ""
    return "npm run build"


def _guess_test_command(stack: Dict) -> str:
    testing = (stack.get("testing") or "").lower()
    if "vitest" in testing or "jest" in testing:
        return "npm test"
    if "pytest" in testing:
        return "pytest -q"
    return "npm test"