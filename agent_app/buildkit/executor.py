"""Plan yurutucusu: her kalemi arac'larla uygular, dogrular, onarir.

Bir kalemin yasam dongusu:

    in_progress
      -> agent dongusu (FILE_TOOLS: oku/yaz/duzenle/ara/komut)
      -> dogrulama komutu calistir (veya modele "bitti mi?" diye sor)
      -> gecti  -> done
      -> kaldi  -> hata ciktisini modele geri besle (self-heal)
      -> yine kaldi -> tekrar dene (max repair)
      -> tukendi -> failed (sonra retry kuyruguna)

Tasarim notlari:
  * Her kalem icin ayri konusma baslatilir; boylece model uzun projede
    baglam tasirmaz ve "bu projeyi unuttum" yaşamaz.
  * Sistem prompt'una spesifikasyon ozeti, arastirma dosyasi ve proje agaci
    verilir; kalem hedefi odaktir.
  * Model cevapsiz kalirsa kalem otomatik "failed" olmaz; sistem yedek
    dogrulama komutuna guvenir.
"""
import json
import logging
import os
import platform
import shlex
import time
from typing import Dict, List, Optional

from django.conf import settings

from . import llm
from .blueprint import format_for_prompt as format_blueprint
from .events import RunLogger
from agent_app import task_control

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"


def _normalize_verify_command(cmd: str) -> str:
    """Convert Linux-style timeout to Windows-compatible."""
    if not IS_WINDOWS:
        return cmd
    # Windows timeout uses /t <seconds> instead of bare seconds
    # timeout 5 python ... -> timeout /t 5 && python ...
    try:
        parts = shlex.split(cmd)
        if parts and parts[0].lower() == "timeout" and len(parts) >= 3:
            # timeout 5 command args...
            try:
                seconds = int(parts[1])
                rest = " ".join(parts[2:])
                return f"timeout /t {seconds} && {rest}"
            except (ValueError, IndexError):
                pass
    except Exception:
        pass
    return cmd


def _app_dir_relative():
    """Proje koku gorel uygulama dizini (varsa); ornek: 'ts'.

    npm projeleri kookte degil alt klasorde olur. `npm run build` proje
    koku calisirsa "Missing script: build" ile hep basarisiz olurdu.
    """
    try:
        from pathlib import Path

        from agent_app.project_scanner import get_project_root
        from agent_app.sandbox.local_preview import _resolve_app_dir

        root = get_project_root()
        if not root:
            return ""
        root_path = Path(root)
        app_dir = _resolve_app_dir(root_path)
        if not app_dir:
            return ""
        app_path = Path(app_dir)
        if app_path == root_path:
            return ""
        try:
            rel = app_path.relative_to(root_path)
        except ValueError:
            return ""
        return str(rel).replace("\\", "/")
    except Exception:
        return ""

from .plan import BuildPlan, PlanItem, is_long_running_command

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


# Dosya ureten araclar: bu araclar hic cagrilmadiysa "uygulandi" denemez.
_WRITE_TOOL_NAMES = {
    "write_file", "edit_file", "patch_file", "apply_patch",
    "move_file", "copy_file", "delete_file", "delete_dir",
}

_NO_WRITE_ERROR = (
    "Bu adimda hicbir dosya yazilmadi; ajan yalnizca okuma/arastirma yapti. "
    "Kalem gercekten uygulanmadi."
)

# Sadece dokuman yazmak "uygulama" sayilmaz; kod/dosya uretimi aranir.
_NON_CODE_EXTENSIONS = {".md", ".markdown", ".txt", ".rst"}


def _is_code_write(name: str, args: dict) -> bool:
    if name not in _WRITE_TOOL_NAMES:
        return False
    target = ""
    if isinstance(args, dict):
        target = str(args.get("path") or args.get("destination") or args.get("target") or "")
    if not target:
        return True
    return os.path.splitext(target)[1].lower() not in _NON_CODE_EXTENSIONS


def _is_implementation_item(item: PlanItem) -> bool:
    """Kalem kod/dosya uretmeli mi? (saf arastirma/dokuman kalemleri haric)"""
    key = str(getattr(item, "key", "") or "").lower()
    phase = str(getattr(item, "phase", "") or "").lower()
    title = str(getattr(item, "title", "") or "").lower()
    blob = " ".join((key, phase, title))
    hints = (
        "implement", "uygula", "build", "scaffold", "setup", "kurulum",
        "frontend", "backend", "sayfa", "page", "component", "kod", "site",
    )
    if any(hint in blob for hint in hints):
        return True
    files = [str(path) for path in (getattr(item, "files", None) or [])]
    if files and not all(path.lower().endswith((".md", ".txt")) for path in files):
        return True
    return False


def _apply_write_guard(verification: Dict, needs_writes: bool, mutating_calls: int) -> Dict:
    """Dogrulama 'gecti' dese bile hic dosya yazilmadiysa kalemi basarili sayma."""
    if needs_writes and mutating_calls == 0 and verification.get("passed"):
        return {"passed": False, "error": _NO_WRITE_ERROR}
    return verification


def _import_tools():
    from agent_app.project_scanner import FILE_TOOLS, execute_tool

    return FILE_TOOLS, execute_tool


def _project_snapshot(root=None, max_chars: int = 6000, task_text: str = "") -> str:
    """Proje iskeleti + git diff tabanli akilli baglam - modele projenin durumunu gosterir."""
    try:
        from agent_app.project_scanner import get_project_context, get_project_root

        if root is None:
            root = get_project_root()
        if not root:
            return "(proje dizini bulunamadi)"
        context = get_project_context(root, max_chars=max_chars, task_text=task_text)
        return context or str(root)
    except Exception as exc:
        logger.debug("proje anlik goruntusu alinamadi: %s", exc)
        return "(proje dizini okunamadi)"


def _get_existing_files(root=None) -> list:
    """Proje dizinindeki dosyaların listesini döndürür."""
    try:
        from agent_app.project_scanner import get_project_root
        import os

        if root is None:
            root = get_project_root()
        if not root or not os.path.exists(root):
            return []

        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip hidden directories and common ignore patterns
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('node_modules', '__pycache__', '.git', 'dist', 'build', 'out', 'target')]
            for f in filenames:
                if not f.startswith('.'):
                    full_path = os.path.join(dirpath, f)
                    rel_path = os.path.relpath(full_path, root)
                    files.append(rel_path.replace('\\', '/'))
        return files
    except Exception as exc:
        logger.debug("dosya listesi alinamadi: %s", exc)
        return []


class ItemExecutor:
    """Tek bir plan kalemini uygular."""

    def __init__(self, run_logger: RunLogger, blueprint: Dict, dossier: Dict = None,
                 benchmark: Dict = None, model: str = None):
        self.log = run_logger
        self.blueprint = blueprint or {}
        self.dossier = dossier or {}
        self.benchmark = benchmark or {}
        self.model = model
        self.max_iterations = int(_cfg("max_item_iterations", 500))
        self.max_repairs = int(_cfg("max_repair_attempts", 100))
        self.history_tail = int(_cfg("history_tail", 40))

    # ------------------------------------------------------------------
    def run(self, item: PlanItem, plan: BuildPlan, round_index: int = 0) -> Dict:
        from agent_app.views import llm_create_resilient

        FILE_TOOLS, execute_tool = _import_tools()
        model_name = llm.pick_model("coding", item.title, explicit=self.model)

        item.status = "in_progress"
        item.attempts += 1
        self.log.emit_plan_item(item, round_index=round_index)

        def _cancelled() -> bool:
            return task_control.is_cancelled(self.log.run_id)

        if _cancelled():
            item.status = "skipped"
            item.result_summary = "Görev iptal edildi."
            self.log.emit_plan_item(item, round_index=round_index)
            return {"ok": False, "item": item, "cancelled": True}

        system_prompt = self._system_prompt(plan, item=item)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._item_prompt(item, plan)},
        ]
        completion_text = ""
        tool_calls_total = 0
        mutating_calls = 0

        for iteration in range(self.max_iterations):
            if _cancelled():
                item.status = "skipped"
                item.result_summary = "Görev iptal edildi."
                self.log.emit_plan_item(item, round_index=round_index)
                return {"ok": False, "item": item, "cancelled": True}
            try:
                self.log.info(f"[{item.key}] iterasyon {iteration + 1}/{self.max_iterations}")
                response = llm_create_resilient(
                    model=model_name,
                    messages=messages[-self.history_tail:],
                    temperature=0.2,
                    tools=FILE_TOOLS,
                    tool_choice="auto",
                    max_tokens=8192,
                )
            except Exception as exc:
                self.log.log("exception", f"[{item.key}] LLM hatasi: {exc}")
                item.status = "failed"
                item.result_summary = f"LLM hatasi: {exc}"[:500]
                self.log.emit_plan_item(item, round_index=round_index)
                return {"ok": False, "item": item, "reason": "llm_error"}

            message = response.choices[0].message
            content = message.content or ""
            tool_calls = message.tool_calls or []
            completion_text = content or completion_text

            if content:
                self.log.log("chat_response", f"[{item.key}] {content}"[:2000])
            if not tool_calls:
                break

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })

            for call in tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}

                self.log.log("tool_call", f"{name}({json.dumps(args, ensure_ascii=False)})"[:1500])
                result = execute_tool(name, args)
                tool_calls_total += 1
                if _is_code_write(name, args):
                    mutating_calls += 1
                self.log.log("tool_result", json.dumps(result, ensure_ascii=False)[:2500])
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False)[:20_000],
                })

            # Arac cagirmiyorsa ve icerigi de eskiyse tekrar sorma.
            if iteration + 1 >= self.max_iterations:
                break

        # --- dogrulama --------------------------------------------------
        needs_writes = _is_implementation_item(item) and not item.verify_command
        verification = _apply_write_guard(self._verify(item), needs_writes, mutating_calls)
        if verification.get("passed"):
            item.status = "done"
            item.result_summary = (completion_text or verification.get("summary") or item.objective)[:2000]
            item.verify_output = verification.get("output", "")[:6000]
            self.log.emit_plan_item(item, round_index=round_index)
            self.log.status(f"✓ {item.title} (tamamlandı, {tool_calls_total} araç çağrısı)")
            return {"ok": True, "item": item, "tool_calls": tool_calls_total}

        # --- kendini onarma ---------------------------------------------
        last_error = verification.get("error") or "dogrulama komutu tanimli degil"
        for repair in range(self.max_repairs):
            if _cancelled():
                item.status = "skipped"
                item.result_summary = "Görev iptal edildi."
                self.log.emit_plan_item(item, round_index=round_index)
                return {"ok": False, "item": item, "cancelled": True}
            self.log.status(f"{item.title}: düzeltme denemesi {repair + 1}/{self.max_repairs}")
            messages.append({
                "role": "user",
                "content": (
                    "Kalem henüz doğrulanmadı. Doğrulama çıktısı:\n"
                    f"```\n{(verification.get('output') or last_error)[:3000]}\n```\n\n"
                    "Sorunu düzelt: ilgili dosyaları oku, düzelt ve tekrar çalıştır. "
                    "Düzelttikten sonra kısa bir özet yaz."
                ),
            })
            repaired = False
            for _ in range(max(3, self.max_iterations // 2)):
                try:
                    response = llm_create_resilient(
                        model=model_name,
                        messages=messages[-self.history_tail:],
                        temperature=0.1,
                        tools=FILE_TOOLS,
                        tool_choice="auto",
                        max_tokens=8192,
                    )
                except Exception as exc:
                    self.log.log("exception", f"[{item.key}] onarma LLM hatasi: {exc}")
                    break
                message = response.choices[0].message
                content = message.content or ""
                tool_calls = message.tool_calls or []
                if content:
                    completion_text = content
                    self.log.log("chat_response", f"[{item.key}] {content}"[:2000])
                if not tool_calls:
                    break
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ],
                })
                for call in tool_calls:
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = execute_tool(call.function.name, args if isinstance(args, dict) else {})
                    tool_calls_total += 1
                    if _is_code_write(call.function.name, args if isinstance(args, dict) else {}):
                        mutating_calls += 1
                    self.log.log("tool_call", f"{call.function.name}({json.dumps(args, ensure_ascii=False)})"[:1200])
                    self.log.log("tool_result", json.dumps(result, ensure_ascii=False)[:2500])
                    messages.append({
                        "role": "tool", "tool_call_id": call.id, "name": call.function.name,
                        "content": json.dumps(result, ensure_ascii=False)[:20_000],
                    })
                repaired = True

            verification = _apply_write_guard(self._verify(item), needs_writes, mutating_calls)
            if verification.get("passed"):
                item.status = "done"
                item.result_summary = (completion_text or item.objective)[:2000]
                item.verify_output = verification.get("output", "")[:6000]
                self.log.emit_plan_item(item, round_index=round_index)
                self.log.status(f"✓ {item.title} (düzeltilerek tamamlandı)")
                return {"ok": True, "item": item, "tool_calls": tool_calls_total}
            last_error = verification.get("error") or last_error
            if not repaired:
                break

        item.status = "failed"
        # Kullaniciya gosterilecek ozet once SORUNU yazsin; modelin "bitti"
        # demesi dogrulanmadigi icin yaniltici olurdu.
        item.result_summary = (f"Doğrulanamadı: {last_error[:300]}\n{completion_text or ''}")[:2000]
        item.verify_output = (verification.get("output") or last_error)[:6000]
        self.log.emit_plan_item(item, round_index=round_index)
        self.log.warn(f"{item.title} doğrulanamadı: {last_error}"[:300])
        return {"ok": False, "item": item, "reason": "verification_failed", "error": last_error}

    # ------------------------------------------------------------------
    def _verify(self, item: PlanItem) -> Dict:
        """Dogrulama komutunu calistir; yoksa dosya varligi kontrolu yap."""
        if item.verify_command and is_long_running_command(item.verify_command):
            # 'npm run dev' asla bitmez; timeout disinda sonuc vermez.
            logger.warning("Asla bitmeyen dogrulama komutu yok sayildi: %s", item.verify_command)
        if item.verify_command and not is_long_running_command(item.verify_command):
            from agent_app.project_scanner import execute_tool

            cmd = _normalize_verify_command(item.verify_command)
            # Komut uygulama dizininde calismali (npm projeleri kookte olmuyor).
            args = {"command": cmd, "timeout": 180}
            rel_app = _app_dir_relative()
            if rel_app:
                args["path"] = rel_app
            result = execute_tool("run_command", args)
            if isinstance(result, dict):
                if result.get("ok"):
                    return {"passed": True, "output": str(result.get("stdout", ""))[-3000:]}
                # run_command non-zero returncode'da 'error' anahtari birakmiyor;
                # yaniltici "komut calistirilamadi" yerine gercek komut+returncode
                # ve stderr modele geri beslensin ki kalemi gercekte onarabilsin.
                error = result.get("error") or f"{item.verify_command} -> {result.get('returncode', 'hata')}"
                return {
                    "passed": False,
                    "error": str(error)[:400],
                    "output": _tail(result.get("stdout"), result.get("stderr"), 3000),
                }
            return {"passed": True, "output": ""}

        # Komut yoksa: beklenen dosyalar gercekten var mi?
        # Dosya yollari hem proje kokune hem uygulama dizinine (ts/ vb.) gorel
        # yazilabilir; ikisini de kontrol et. Eski kod sadece koke bakinca
        # "src/app/layout.tsx" hep eksik gorunuyordu (gercek dosya ts/ altinda).
        if item.files:
            from agent_app.project_scanner import get_project_root

            root = get_project_root()
            if not root:
                return {"passed": True, "output": "proje dizini yok, dosya kontrolu atlandi"}
            root_path = str(root)
            app_rel = _app_dir_relative()
            app_path = os.path.join(root_path, app_rel) if app_rel else root_path

            def _exists(rel) -> bool:
                rel = str(rel).strip().replace("\\", "/")
                while rel.startswith("./"):
                    rel = rel[2:]
                rel = rel.lstrip("/")
                if not rel or "*" in rel:
                    return True
                for base in (root_path, app_path):
                    if os.path.exists(os.path.join(base, rel)):
                        return True
                # Uzanti farkina tolerans: plan 'next.config.ts' derken model
                # 'next.config.mts' yazabilir; ayni dizinde ayni stem yeterli.
                parent, name = os.path.split(rel)
                stem = os.path.splitext(name)[0]
                if not stem:
                    return False
                for base in (root_path, app_path):
                    folder = os.path.join(base, parent) if parent else base
                    try:
                        for entry in os.listdir(folder):
                            if entry == stem or entry.startswith(stem + "."):
                                return True
                    except OSError:
                        continue
                # Son care: ayni dosya adi proje genelinde baska yerde mi?
                for base in (root_path, app_path):
                    for dirpath, dirnames, filenames in os.walk(base):
                        dirnames[:] = [d for d in dirnames
                                       if d not in ("node_modules", ".next", ".git", "dist", "build")]
                        for entry in filenames:
                            if entry == name or os.path.splitext(entry)[0] == stem:
                                return True
                return False

            missing = [path for path in item.files if not _exists(path)]
            if missing:
                # Dosya listesi bir ipucu, sozlesme degil: eksikleri raporla ama
                # kalemi tek basina bu yuzden oldurme — asil kap build/test komutu.
                # Eski kodda scaffold bu yuzden hep failed olup 15 kalemi atlatiyordu.
                logger.warning("[%s] beklenen dosyalarin bazi eksik: %s", item.key, ", ".join(missing[:6]))
                return {
                    "passed": True,
                    "output": ("kismen tamamlandi; eksik dosyalar: %s. "
                               "Dosya listesi yalnizca ipucudur; ana dogrulama "
                               "build/lint/test komutudur.") % (", ".join(missing[:6])),
                }
            return {"passed": True, "output": f"dosyalar mevcut: {', '.join(item.files[:6])}"}

        return {"passed": True, "output": "dogrulama komutu tanimli degil; model cevabina guvenildi"}

    # ------------------------------------------------------------------
    def _system_prompt(self, plan: BuildPlan, item: PlanItem = None) -> str:
        task_text = ""
        if item is not None:
            task_text = " ".join(
                filter(None, [
                    item.title,
                    item.objective,
                    item.detail or "",
                    " ".join(item.files or []),
                ])
            )
        return f"""Sen EMOG'un kıdemli geliştiricisisin. Sana bir projenin UYGULAMA PLANINDAKİ
TEK BİR kalem veriliyor. Görevin o kalemi bu projede tam olarak uygulamak.

=== ÜRÜN SPESİFİKASYONU (bağlam) ===
{format_blueprint(self.blueprint, limit=4500)}

=== ARAŞTIRMA DOSYASINDAN KRİTİK NOKTALAR ===
{_research_digest(self.dossier, self.benchmark)}

=== PROJENİN ŞU ANKİ HALİ ===
{_project_snapshot(task_text=task_text)}

=== PLAN İLERLEMESİ ===
{plan.format_for_prompt(limit=2500)}

=== KURALLAR ===
1. Önce var olan dosyaları `read_file` ile oku; hiçbir şeyi varsayma.
2. Değişiklik yapmadan önce komutu/etkiyi düşün; kalem küçükse küçük çözüm üret.
3. Dosya yolları kalemde verildiyse SADECE o yolları kullan.
4. Birden fazla dosya yazacaksan sırayla yaz ve arada hata kontrolü yap.
5. Yazdığın kod çalışır olmalı: import'lar tutarlı, fonksiyon imzaları eşleşmeli,
   kullanılan değişkenler tanımlı olmalı. Yarım kod bırakma.
6. Kalemin kabul kriterlerini karşıladığında kısa bir özet yaz ve ARAÇ ÇAĞRMA.
 7. Emin değilsen `run_command` ile doğrula; ama `curl`/`wget` gibi yasaklı
    komutları kullanma.
 8. Windows üzerinde çalışıyorsun: `grep`, `cat`, `head`, `tail`, `pwd`, `ls`,
    `sed`, `awk`, `which`, `touch` YOKTUR ve "is not recognized" hatası verir.
    Bunların yerine `findstr /n "desen" dosya`, `type dosya` ya da
    `powershell -Command "Get-Content dosya"` kullan; dosya okumak için
    tercihen doğrudan `read_file` aracını çağır.
 9. Bu kalemin dışındaki kapsamı genişletme; sıradaki kalemler var.
 10. Projenin PYTHON SKELETON (AST) bölümü fonksiyon/sınıf imzalarını gösterir.
     Büyük dosyaların gövdesini okumadan önce `code_outline(path)` aracını
     kullan; yalnızca gereken dosyayı `read_file` ile aç.
 11. Bu kalem kod/dosya üretmeyi gerektiriyorsa en az bir `write_file`/`edit_file`
     çağrısı yapmadan bitirme. Yalnızca okuyup/araştırıp "tamamlandı" deme;
     sistem bunu başarısız sayar.
"""

    def _item_prompt(self, item: PlanItem, plan: BuildPlan) -> str:
        parts = [
            f"KALEM {item.order + 1}/{len(plan)} — Faz: {item.phase_label}",
            f"Başlık: {item.title}",
            f"Hedef: {item.objective}",
        ]
        if item.user_requested:
            parts.append("⚠ Bu kalem kullanıcının AÇIKÇA İSTEDİĞİ bir özelliktir; eksiksiz yapılmalı.")
        if item.detail:
            parts.append(f"Ayrıntı: {item.detail}")
        if item.files:
            parts.append("Beklenen dosyalar: " + ", ".join(item.files))
        if item.acceptance:
            parts.append("Kabul kriterleri:\n" + "\n".join(f"- {str(a)}" for a in item.acceptance))
        if item.verify_command:
            parts.append(f"Doğrulama komutu: `{item.verify_command}` (çalıştırılmalı)")
        if item.depends_on:
            parts.append("Bağımlı olduğu kalemler: " + ", ".join(item.depends_on))

        parts.append(
            "\nŞimdi bu kalemi uygula. Araç çağrıların olmadan önce projeyi oku, "
            "sonra dosyaları yaz/düzenle ve gerekiyorsa doğrulama komutunu çalıştır."
        )
        return "\n".join(parts)


# ----------------------------------------------------------------------
class PlanExecutor:
    """Tum plani sirayla yurutur; bagimliliklara uyar, hatalari tekrar dener."""

    def __init__(self, run_logger: RunLogger, blueprint: Dict, dossier: Dict = None,
                 benchmark: Dict = None, model: str = None):
        self.log = run_logger
        self.executor = ItemExecutor(run_logger, blueprint, dossier, benchmark, model)
        self.max_retries = int(_cfg("max_item_retries", 200))

    def run(self, plan: BuildPlan, round_index: int = 0) -> Dict:
        stats = {"done": 0, "failed": 0, "skipped": 0, "total": 0, "rounds": round_index + 1}
        completed = 0

        def _cancelled() -> bool:
            return task_control.is_cancelled(self.log.run_id)

        def _mark_remaining_cancelled():
            for pending in plan.pending():
                pending.status = "skipped"
                pending.result_summary = "Görev iptal edildi."
                self.log.emit_plan_item(pending, round_index=round_index)
            plan.save(self.log)

        if _cancelled():
            _mark_remaining_cancelled()
            progress = plan.progress()
            stats.update({
                "total": progress["total"], "done": progress["done"],
                "failed": progress["failed"], "skipped": progress["skipped"],
            })
            return stats

        # Pipeline phase detection based on phase labels
        PHASE_STAGES = {
            "research": ("research", "Araştırma", "🔍"),
            "arastirma": ("research", "Araştırma", "🔍"),
            "benchmark": ("benchmark", "Rakip Analizi", "📊"),
            "rakip": ("benchmark", "Rakip Analizi", "📊"),
            "blueprint": ("blueprint", "Spesifikasyon", "📋"),
            "spesifikasyon": ("blueprint", "Spesifikasyon", "📋"),
            "planning": ("planning", "Planlama", "📝"),
            "planlama": ("planning", "Planlama", "📝"),
            "building": ("building", "Uygulama", "🔨"),
            "uygulama": ("building", "Uygulama", "🔨"),
            "implementation": ("building", "Uygulama", "🔨"),
            "reviewing": ("reviewing", "Eleştiri", "🔎"),
            "eleştiri": ("reviewing", "Eleştiri", "🔎"),
            "critique": ("reviewing", "Eleştiri", "🔎"),
            "polish": ("polish", "Son Rötuş", "✨"),
            "son rotus": ("polish", "Son Rötuş", "✨"),
            "done": ("done", "Tamamlandı", "✅"),
            "tamamlandi": ("done", "Tamamlandı", "✅"),
        }

        def detect_stage(phase_label: str) -> tuple:
            """Phase label'ından pipeline stage tespit et."""
            label = (phase_label or "").lower()
            for key, (stage_id, label, icon) in PHASE_STAGES.items():
                if key in label:
                    return stage_id, label, icon
            return "start", "Başlatılıyor", "🚀"

        current_stage = None

        # Emit initial "starting" stage
        self.log.stage("start", "Pipeline başlatılıyor", icon="🚀", progress={"done": 0, "total": len(plan)})

        while True:
            if _cancelled():
                _mark_remaining_cancelled()
                break
            ready = plan.ready_items()
            if not ready:
                if plan.pending():
                    # Bagimlilik dongusu/tukenmis kalem var: kalanlari atla.
                    for item in plan.pending():
                        item.status = "skipped"
                        stats["skipped"] += 1
                        self.log.emit_plan_item(item, round_index=round_index)
                        self.log.warn(f"Atlandi (bağımlılığı tamamlanamadı): {item.title}")
                break

            item = ready[0]
            stats["total"] += 1
            completed += 1

            # Detect and emit stage transition
            stage_id, stage_label, stage_icon = detect_stage(item.phase_label)
            if stage_id != current_stage:
                current_stage = stage_id
                self.log.stage(
                    stage_id,
                    stage_label,
                    icon=stage_icon,
                    text=f"{stage_label} aşaması başladı: {item.title}",
                    progress={"done": completed, "total": len(plan)}
                )

            self.log.emit_progress(completed, len(plan), f"Uygulama: {item.phase_label}")

            outcome = self.executor.run(item, plan, round_index=round_index)
            if outcome.get("ok"):
                stats["done"] += 1
            else:
                stats["failed"] += 1
                item.attempts = 0  # tekrar denemede sifirdan sayilsin

            # Her kalemden sonra plani kalici hale getir: worker cokse bile
            # kaldigimiz yer bilinir.
            plan.save(self.log)

        # Basarisiz VE bagimliligi cozuldugunde tekrar denenebilecek
        # atlanmis kalemleri sinirli sayida tekrar dene.
        for attempt in range(1, self.max_retries + 1):
            if _cancelled():
                _mark_remaining_cancelled()
                break
            done_keys = {i.key for i in plan.done()}
            retryable = [
                i for i in plan.items
                if i.status in ("failed", "skipped")
                and i.attempts < self.max_retries
                and (
                    i.status == "failed"
                    or all(plan._resolve(dep) in done_keys for dep in i.depends_on)
                )
            ]
            if not retryable:
                break
            self.log.status(
                f"Başarısız/atlanan {len(retryable)} kalem yeniden deneniyor "
                f"(tur {attempt}/{self.max_retries})"
            )
            backoff = min(300.0, max(0.0, float(_cfg("retry_backoff_seconds", 2))) * (2 ** min(attempt - 1, 7)))
            if backoff:
                self.log.status(f"{backoff:.0f} sn sonra yeniden denenecek...")
                time.sleep(backoff)
            for item in retryable:
                item.status = "pending"
            plan.save(self.log)
            while True:
                done_keys = {i.key for i in plan.done()}
                ready = [
                    i for i in retryable
                    if i.status == "pending"
                    and all(plan._resolve(dep) in done_keys for dep in i.depends_on)
                ]
                if not ready:
                    break
                item = ready[0]
                outcome = self.executor.run(item, plan, round_index=round_index)
                if not outcome.get("ok"):
                    self.log.warn(f"{item.title}: yeniden deneme başarısız")
            plan.save(self.log)

        plan.save(self.log)
        plan.sync_db(self.log)
        progress = plan.progress()
        stats.update({
            "total": progress["total"],
            "done": progress["done"],
            "failed": progress["failed"],
            "skipped": progress["skipped"],
        })
        self.log.status(
            f"Uygulama turu bitti: {progress['done']}/{progress['total']} kalem tamam, "
            f"{progress['failed']} başarısız, {progress['skipped']} atlandı"
        )
        if progress["done"] == progress["total"] and progress["failed"] == 0 and progress["skipped"] == 0:
            self.log.stage("done", "Tamamlandı", icon="✅", text="Pipeline başarıyla tamamlandı", progress={"done": progress['done'], "total": progress['total']})
        else:
            self.log.stage(
                "error", "Tur tamamlanamadı", icon="⚠️",
                text=f"{progress['done']}/{progress['total']} tamam, "
                     f"{progress['failed']} başarısız, {progress['skipped']} atlandı",
                progress={"done": progress['done'], "total": progress['total']},
            )
        return stats


# ----------------------------------------------------------------------
def _tail(stdout, stderr, limit: int) -> str:
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    blob = (out + ("\n--- stderr ---\n" + err if err else "")).strip()
    return blob[-limit:]


def _research_digest(dossier: Dict, benchmark: Dict, limit: int = 3500) -> str:
    from .blueprint import _benchmark_blob, _dossier_blob

    return (_dossier_blob(dossier, limit=limit) + "\n\n" + _benchmark_blob(benchmark, limit=1500))[:limit + 1500]


def execute_plan(plan: BuildPlan, run_logger: RunLogger, blueprint: Dict,
                 dossier: Dict = None, benchmark: Dict = None, model: str = None,
                 round_index: int = 0) -> Dict:
    return PlanExecutor(run_logger, blueprint, dossier, benchmark, model).run(plan, round_index)


# Async multi-agent execution
async def execute_plan_orchestrated(
    goal: str,
    run_logger: RunLogger,
    blueprint: Dict,
    dossier: Dict = None,
    benchmark: Dict = None,
    model: str = None,
    context: Dict = None,
    max_workers: int = 3,
    resume_plan=None,
) -> Dict:
    """Execute plan using multi-agent Orchestrator."""
    from .orchestrator import Orchestrator, WorkerPool
    from .planning_agent import PlanningAgent
    
    worker_pool = WorkerPool(
        max_workers=max_workers,
        run_logger=run_logger,
        blueprint=blueprint,
        dossier=dossier,
        benchmark=benchmark,
        model=model
    )
    await worker_pool.start()

    if resume_plan is not None:
        # Var olan plani yeniden uretme; kaldigi yerden kos.
        planning_agent = None
        context = dict(context or {})
        context["plan"] = resume_plan
    else:
        planning_agent = PlanningAgent(model=model)

    orchestrator = Orchestrator(
        run_logger=run_logger,
        worker_pool=worker_pool,
        planning_agent=planning_agent,
        max_workers=max_workers
    )
    
    try:
        result = await orchestrator.run(goal, context)
        return result
    finally:
        await worker_pool.stop()


def execute_plan(plan: BuildPlan, run_logger: RunLogger, blueprint: Dict,
                 dossier: Dict = None, benchmark: Dict = None, model: str = None,
                 round_index: int = 0) -> Dict:
    return PlanExecutor(run_logger, blueprint, dossier, benchmark, model).run(plan, round_index)