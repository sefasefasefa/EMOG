"""Otonom proje uretim dongusu.

`run_build_pipeline()` cagrildiginda su dongu calisir:

    1. Arastirma   Konuyu ogren (coklu saglayici + derin okuma + sentez)
    2. Kiyas       Rakip siteleri ziyaret et, ozellik matrisini cikar
    3. Mavi baskı  En gelismis halin spesifikasyonunu yaz
    4. Plan        Sirali, bagimlilikli, dogrulanabilir kalemler uret
    5. Uygulama    Her kalemi yap + dogrula + bozulursa onar
    6. Eleştiri    Bosluk var mi? Yeni kalem uret -> 5'e don
    6.5 Tasarım   Görsel denetim (tarayıcı) -> yeni kalemler -> 5'e don
    7. Kalite      Son kontrol, dokumantasyon, teslim

Guvenlik frenleri (hepsi settings.BUILD_PIPELINE'dan ayarlanir):
    max_rounds, max_plan_items, max_item_iterations, max_repair_attempts,
    time_budget_seconds, critical_error_tolerance

Frenler olmadan model sonsuz donguye girebilir; "hiç durmadan" istegini
bu frenler sagliyor — dongu, is bitene kadar calisir ama maliyetli durdurma
sartlari her zaman vardir.
"""
import asyncio
import logging
import os
import time
import traceback
from typing import Dict, List

from django.conf import settings

from . import blueprint as blueprint_mod
from . import benchmark as benchmark_mod
from . import critic as critic_mod
from . import design_review as design_review_mod
from . import executor as executor_mod
from . import plan as plan_mod
from . import research as research_mod
from .events import RunLogger
from agent_app import task_control

# Import from executor
from .executor import _project_snapshot, _get_existing_files

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


_CODE_EXTENSIONS = {
    ".html", ".htm", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx",
    ".vue", ".svelte", ".py", ".php", ".rb", ".go", ".java", ".cs", ".sql",
}
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", "dist", "build", "out",
    ".next", ".venv", "venv", "task_runs",
}


def _project_code_files(limit: int = 5) -> list:
    """Projede gercekten kod dosyasi var mi? (sadece .md dokumanlari yetmez)"""
    try:
        from agent_app.project_scanner import get_project_root

        root = get_project_root()
        if not root or not os.path.isdir(root):
            return []
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if os.path.splitext(name)[1].lower() in _CODE_EXTENSIONS:
                    found.append(os.path.relpath(os.path.join(dirpath, name), root))
                    if len(found) >= limit:
                        return found
        return found
    except Exception as exc:
        logger.debug("kod dosyasi taramasi basarisiz: %s", exc)
        return []


def enabled() -> bool:
    return bool(_cfg("enabled", True))


def _project_update(project_id, **changes) -> None:
    if not project_id:
        return
    try:
        from agent_app import project_store

        project_store.update_project(project_id, **changes)
    except Exception as exc:
        logger.debug("proje metasi guncellenemedi: %s", exc)


def _project_language(spec: dict) -> str:
    stack = (spec or {}).get("stack") or {}
    if not isinstance(stack, dict):
        return "Unknown"
    for key in ("frontend", "framework", "language", "backend", "runtime"):
        value = stack.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text[:40]
    return "Unknown"


class Budget:
    """Zaman ve hata butcesi."""

    def __init__(self, seconds: float = None, max_critical_errors: int = None):
        self.seconds = float(seconds if seconds is not None else _cfg("time_budget_seconds", 0) or 0)
        self.max_critical_errors = int(
            max_critical_errors if max_critical_errors is not None
            else _cfg("critical_error_tolerance", 3)
        )
        self.started = time.monotonic()
        self.critical_errors = 0

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def exhausted(self) -> bool:
        return self.seconds > 0 and self.elapsed() > self.seconds

    def remaining(self) -> float:
        if self.seconds <= 0:
            return float("inf")
        return max(0.0, self.seconds - self.elapsed())

    def trip(self, error: str = "") -> bool:
        self.critical_errors += 1
        logger.warning("pipeline kritik hata %d/%d: %s",
                       self.critical_errors, self.max_critical_errors, error[:200])
        return self.critical_errors >= self.max_critical_errors


def run_build_pipeline(
    prompt_wrapper, model_name=None, project_id=None, user_id=None,
    use_subagents: bool = False, agent_counts: Dict[str, int] = None
) -> dict:
    """Tasarim: worker thread'ine baglanır, `run_self_healing_loop` ile aynı sozlesme.

    Dönüş: {"success": bool, "output": str, "run_id": str}
    """
    started = time.monotonic()
    meta = {}
    if isinstance(prompt_wrapper, dict):
        meta = prompt_wrapper.get("__meta__") or {}
        request = prompt_wrapper.get("prompt") or prompt_wrapper.get("task") or ""
    else:
        request = prompt_wrapper or ""

    run_id = meta.get("run_id")
    project_id = project_id or meta.get("project_id")
    user_id = user_id or meta.get("user_id")

    run_logger = RunLogger(run_id=run_id, console=bool(meta.get("console")))
    run_id = run_logger.run_id

    if task_control.is_cancelled(run_id):
        return _cancelled_result(run_logger)

    if project_id:
        try:
            from agent_app.project_scanner import set_project_context

            set_project_context(project_id, user_id)
        except Exception as exc:
            logger.warning("proje baglami kurulamadi: %s", exc)
        _project_update(
            project_id,
            status="building",
            last_run_id=run_id,
            last_run_status="running",
            last_error="",
            description=request[:400],
        )

    run_logger.stage("start", "Otonom proje uretimi basliyor")
    run_logger.info(f"İstek: {request[:4000]}")
    _ensure_db(run_logger, request, model_name, project_id, user_id)

    budget = Budget()

    # Subagent modu mu?
    if use_subagents:
        try:
            return _run_subagent_pipeline(
                run_logger, request, model_name, project_id, user_id,
                budget, agent_counts
            )
        except Exception as exc:
            return _pipeline_failure(run_logger, project_id, exc)

    # Multi-Agent mode?
    if _cfg("multi_agent_enabled", True):
        import asyncio
        try:
            return asyncio.run(run_multi_agent_pipeline(
                request, run_logger, model_name, project_id, user_id, budget, meta
            ))
        except Exception as exc:
            return _pipeline_failure(run_logger, project_id, exc)

    stages = {}
    # Track plan for progress reporting
    build_plan = None
    try:
        # 1) Arastirma ----------------------------------------------------
        dossier = research_mod.run_deep_research(request, run_logger, model=model_name)
        stages["research"] = {"documents": len(dossier.get("documents") or []),
                              "urls": dossier.get("url_count", 0)}
        _update_db(run_logger, {"stage": "benchmark", "status": "benchmarking"})
        if budget.exhausted():
            return _abort(run_logger, budget, "Zaman bütçesi araştırma sonunda doldu")

        # 2) Rakip analizi ------------------------------------------------
        benchmark = benchmark_mod.run_benchmark(
            dossier.get("scope") or {}, {"all": _harvest_urls(dossier)}, run_logger, model=model_name
        )
        stages["benchmark"] = {"sites": benchmark.get("sites_analyzed", 0)}
        _update_db(run_logger, {"stage": "blueprint", "status": "blueprinting"})

        # 3) Mavi baskı ---------------------------------------------------
        spec = blueprint_mod.build_blueprint(request, dossier, benchmark, run_logger, model=model_name)
        stages["blueprint"] = {
            "features": sum(len(g.get("features") or []) for g in spec.get("feature_groups") or []),
            "platform": spec.get("platform"),
        }
        project_changes = {
            "description": str(spec.get("one_liner") or request or "")[:400],
            "language": _project_language(spec),
            "status": "building",
        }
        product_name = str(spec.get("product_name") or "").strip()
        if product_name:
            project_changes["name"] = product_name[:120]
        _project_update(project_id, **project_changes)
        _update_db(run_logger, {
            "stage": "planning", "status": "planning",
            "blueprint": spec, "tech_stack": spec.get("stack") or {},
            "user_requested_features": spec.get("user_requested_features") or [],
            "research_summary": (dossier.get("summary") or "")[:4000],
            "benchmark_summary": (benchmark.get("analysis") or {}).get("what_best_looks_like", [])[:12],
        })

        # 4-6) Plan -> Uygulama -> Eleştiri dongusu -------------------------
        build_plan = plan_mod.build_plan(request, spec, run_logger, dossier, model=model_name)
        stages["plan"] = build_plan.progress()
        run_logger.status(f"Plan hazır: {build_plan.progress()['total']} kalem",
                         progress=build_plan.progress())
        _update_db(run_logger, {"stage": "building", "status": "building",
                                "plan_total": build_plan.progress()["total"]})

        rounds = []
        max_rounds = int(_cfg("max_rounds", 1000))
        round_index = 0
        exec_stats = None
        reviewer = critic_mod.Critic(run_logger, spec, dossier, benchmark, model_name)

        while True:
            if task_control.is_cancelled(run_logger.run_id):
                run_logger.warn("Görev iptal edildi; uygulama döngüsü durduruluyor.")
                break
            round_index += 1
            prog = build_plan.progress() if build_plan else {"done": 0, "total": 0}
            run_logger.stage("build", f"Uygulama turu {round_index}/{max_rounds} — {prog['done']}/{prog['total']} tamam",
                           progress=prog)
            _update_db(run_logger, {"stage": "building", "status": "building", "rounds": round_index})

            exec_stats = executor_mod.execute_plan(
                build_plan, run_logger, spec, dossier, benchmark, model=model_name,
                round_index=round_index,
            )
            rounds.append(exec_stats)

            # Eleştiri: yeni iş var mı?
            report = reviewer.review(build_plan, round_index=round_index)
            rows = reviewer.to_items(report.get("missing_items") or [])
            added = plan_mod.append_items(build_plan, rows, run_logger, phase="polish")
            rounds[-1]["critic"] = {
                "verdict": report.get("verdict"),
                "coverage": report.get("coverage"),
                "blocking_issues": len(report.get("blocking_issues") or []),
                "suggested": len(rows),
                "added": added,
            }
            prog = build_plan.progress()
            run_logger.status(f"Eleştiri: {added} yeni kalem eklendi — {prog['done']}/{prog['total']} tamam",
                            progress=prog)
            _update_db(run_logger, {"stage": "reviewing", "status": "reviewing"})
            if not added:
                run_logger.info("Eleştiri yeni iş bulmadı — döngü tamamlandı")
                break

            # 6.5) Görsel tasarım denetimi --------------------------------------
            design_items = _run_design_review(run_logger, build_plan, spec, request, model_name)
            if design_items:
                added = plan_mod.append_items(build_plan, design_items, run_logger, phase="hardening")
                if added:
                    prog = build_plan.progress()
                    run_logger.status(f"Tasarım denetiminden {added} yeni kalem eklendi — {prog['done']}/{prog['total']} tamam",
                                    progress=prog)
                    continue
                else:
                    run_logger.info("Tasarım denetimi kalem eklemedi")

            if build_plan.progress()["done"] == 0 and exec_stats["done"] == 0 and exec_stats["failed"] > 0:
                budget.trip("uygulama: hiçbir kalem tamamlanamadı")
            if budget.critical_errors >= budget.max_critical_errors:
                run_logger.warn("Kritik hata sınırına ulaşıldı; döngü durduruluyor")
                break
            if budget.exhausted():
                run_logger.warn(f"Zaman bütçesi doldu ({budget.seconds:.0f}s); döngü durduruluyor")
                break
            if round_index >= max_rounds:
                run_logger.info("Tur sınırına ulaşıldı; son turda kalıyor")
                break

        # 6.8) Canlı önizleme testi + düzeltme turları ---------------------
        try:
            max_fix_rounds = max(0, int(_cfg("preview_qa_fix_rounds", 100)))
            fixes_done = 0
            qa_report = None
            while True:
                if task_control.is_cancelled(run_logger.run_id):
                    break
                qa_report, qa_items = _run_preview_qa(run_logger, project_id, spec)
                if not qa_items or fixes_done >= max_fix_rounds:
                    break
                qa_added = plan_mod.append_items(build_plan, qa_items, run_logger, phase="hardening")
                if not qa_added:
                    break
                run_logger.status(
                    f"Canlı testten {qa_added} düzeltme kalemi eklendi; uygulanıyor "
                    f"(tur {fixes_done + 1}/{max_fix_rounds})"
                )
                executor_mod.execute_plan(
                    build_plan, run_logger, spec, dossier, benchmark,
                    model=model_name, round_index=99,
                )
                fixes_done += 1
            if qa_report is not None and qa_report.blocking_issues():
                run_logger.warn(
                    f"Canlı testte {len(qa_report.blocking_issues())} sorun devam ediyor "
                    f"({fixes_done} düzeltme turu denendi)."
                )
        except Exception as exc:
            run_logger.warn(f"Canlı test entegrasyonu atlandı: {exc}")

        # 7) Kalite şeridi -------------------------------------------------
        _update_db(run_logger, {"stage": "polish", "status": "polishing"})
        polish = _polish(run_logger, build_plan, spec, request, model=model_name)

        # Kapanış ---------------------------------------------------------
        progress = build_plan.progress()
        elapsed = time.monotonic() - started
        missing_code = progress["done"] > 0 and not _project_code_files()
        success = (
            progress["done"] > 0
            and not missing_code
            and progress["done"] == progress["total"]
            and progress["failed"] == 0
            and progress["skipped"] == 0
        )
        summary = _summary(request, spec, dossier, benchmark, build_plan, rounds, polish, elapsed)
        if missing_code:
            run_logger.warn(
                "Proje dosyaları oluşturulmadı: ajan hiç kod dosyası yazmamış; görev başarısız sayılıyor."
            )
            summary["text"] = (summary.get("text") or "") + (
                "\n\n- **Hata**: Proje dosyaları oluşturulmadı (yalnızca doküman/plan üretilmiş)."
            )
        if not success:
            run_logger.warn(
                f"Plan tamamlanamadı: {progress['done']}/{progress['total']} tamam, "
                f"{progress['failed']} başarısız, {progress['skipped']} atlandı."
            )

        run_logger.save("summary", summary)
        run_logger.log("summary", summary["text"], **{
            "progress": progress,
            "stats": summary["stats"],
        })
        run_logger.stage(
            "done" if success else "error",
            "Üretim tamamlandı" if success else "Üretim tamamlanamadı",
        )
        run_logger.log("output", summary["text"])

        _update_db(run_logger, {
            "stage": "completed" if success else "failed",
            "status": (
                "completed" if success
                else ("partial" if progress["done"] > 0 else "failed")
            ),
            "progress": progress["percent"],
            "stats": summary["stats"],
            "summary": summary["text"],
            "finished_at": True,
        })

        return {"success": success, "output": summary["text"], "run_id": run_id,
                "plan": progress, "stages": stages}

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.exception("build pipeline coktu: %s", detail)
        run_logger.log("exception", detail)
        run_logger.log("traceback", traceback.format_exc()[:4000])
        run_logger.stage("failed", "Üretim sırasında hata oluştu")
        _update_db(run_logger, {"stage": "failed", "status": "failed", "error": detail[:1000]})
        return {"success": False, "output": f"Üretim sırasında hata oluştu: {detail}", "run_id": run_id}


# ----------------------------------------------------------------------
# Multi-Agent Pipeline (async)
# ----------------------------------------------------------------------
def _load_resume_state(run_logger: RunLogger):
    """Yarim kalmis build icin plan + arastirma/spec durumunu yukle."""
    try:
        from .plan import BuildPlan
        plan = BuildPlan.load(run_logger)
        if not plan:
            return None
        if not (
            plan.pending() or plan.failed() or plan.skipped()
            or any(i.status == "in_progress" for i in plan.items)
        ):
            return None
        spec = run_logger.load("blueprint") or {}
        dossier = run_logger.load("research") or {}
        benchmark = run_logger.load("benchmark") or {}
        if not isinstance(spec, dict) or not spec:
            return None
        return plan, spec, dossier, benchmark
    except Exception as exc:
        logger.warning("Resume durumu yuklenemedi: %s", exc)
        return None


async def run_multi_agent_pipeline(
    request: str,
    run_logger: RunLogger,
    model_name: str = None,
    project_id: str = None,
    user_id: str = None,
    budget: Budget = None,
    meta: Dict = None,
) -> dict:
    """Run the entire build process using multi-agent Orchestrator."""
    from .executor import execute_plan_orchestrated
    from .events import RunLogger
    import asyncio
    
    # Initialize RunLogger if not provided
    if not isinstance(run_logger, RunLogger):
        run_logger = RunLogger(run_id=meta.get("run_id") if meta else None)

    if task_control.is_cancelled(run_logger.run_id):
        return _cancelled_result(run_logger)
    
    # Research & Blueprint (resume varsa bunlari atla)
    from . import research as research_mod
    from . import benchmark as benchmark_mod
    from . import blueprint as blueprint_mod
    from .research import DeepResearcher

    resume_state = _load_resume_state(run_logger)
    if resume_state:
        resumed_plan, spec, dossier, benchmark = resume_state
        run_logger.status(
            "Kaldığı yerden devam ediliyor: "
            f"{len(resumed_plan.done())}/{len(resumed_plan)} kalem tamam."
        )
    else:
        resumed_plan = None
        run_logger.stage("research", "Derin araştırma başlıyor")
        _update_db(run_logger, {"stage": "research", "status": "researching"})

        # Use the configured research depth. The old "2 queries / 0 deep reads"
        # values made every site shallow; depth is the whole point of this step.
        researcher = DeepResearcher(run_logger, model=model_name)
        researcher.max_queries = max(2, int(_cfg("research_queries", 8)))
        researcher.max_deep_reads = max(0, int(_cfg("research_deep_reads", 3)))
        researcher.per_query = max(1, int(_cfg("research_per_query", 4)))
        researcher.crawl_delay = float(_cfg("crawl_delay", 0.4))

        dossier = await asyncio.get_running_loop().run_in_executor(
            None, lambda: researcher.run(request)
        )

        run_logger.stage("benchmark", "Rakip analizi başlıyor")
        _update_db(run_logger, {"stage": "benchmark", "status": "benchmarking"})
        benchmark = await benchmark_mod.run_benchmark_async(
            dossier.get("scope") or {}, {"all": _harvest_urls(dossier)}, run_logger, model=model_name
        )

        run_logger.stage("blueprint", "Spesifikasyon oluşturuluyor")
        _update_db(run_logger, {"stage": "blueprint", "status": "blueprinting"})
        spec = await blueprint_mod.build_blueprint_async(
            request, dossier, benchmark, run_logger, model=model_name
        )

    project_changes = {
        "description": str(spec.get("one_liner") or request or "")[:400],
        "language": _project_language(spec),
        "status": "building",
    }
    product_name = str(spec.get("product_name") or "").strip()
    if product_name:
        project_changes["name"] = product_name[:120]
    _project_update(project_id, **project_changes)
    _update_db(run_logger, {
        "stage": "building",
        "status": "building",
        "blueprint": spec,
        "tech_stack": spec.get("stack") or {},
        "user_requested_features": spec.get("user_requested_features") or [],
        "research_summary": (dossier.get("summary") or "")[:4000],
        "benchmark_summary": (benchmark.get("analysis") or {}).get("what_best_looks_like", [])[:12],
    })
    
    # Prepare context for orchestrator
    if task_control.is_cancelled(run_logger.run_id):
        return _cancelled_result(run_logger)
    context = {
        "blueprint": spec,
        "dossier": dossier,
        "benchmark": benchmark,
        "project_snapshot": _project_snapshot(),
        "existing_files": list(_get_existing_files()),
    }
    
    # Run multi-agent orchestration
    run_logger.stage("orchestrator", "Çok ajan orkestrasyonu başlıyor")
    
    result = await execute_plan_orchestrated(
        goal=request,
        run_logger=run_logger,
        blueprint=spec,
        dossier=dossier,
        benchmark=benchmark,
        model=model_name,
        context=context,
        max_workers=_cfg("multi_agent_max_workers", 3),
        resume_plan=resumed_plan,
    )

    stats = result.get("stats") or {}
    plan_obj = result.get("plan")

    if task_control.is_cancelled(run_logger.run_id):
        return _cancelled_result(run_logger)

    # Orkestratör retry'larına rağmen eksik kalan kalemler için son bir
    # kurtarma turu: bir site isteği tek görevde bitsin, yarım kalmasın.
    if plan_obj is not None and (
        int(stats.get("failed") or 0) > 0 or int(stats.get("skipped") or 0) > 0
    ):
        run_logger.warn(
            f"Plan eksik kaldı ({stats.get('failed', 0)} başarısız, "
            f"{stats.get('skipped', 0)} atlandı); kurtarma turu başlatılıyor."
        )
        try:
            executor_mod.execute_plan(
                plan_obj, run_logger, spec, dossier, benchmark, model_name,
                round_index=98,
            )
            progress = plan_obj.progress()
            stats.update({
                "total": progress.get("total", stats.get("total", 0)),
                "done": progress.get("done", stats.get("done", 0)),
                "failed": progress.get("failed", stats.get("failed", 0)),
                "skipped": progress.get("skipped", stats.get("skipped", 0)),
            })
            result["stats"] = stats
            result["ok"] = (
                stats["total"] > 0
                and stats["done"] == stats["total"]
                and stats["failed"] == 0
                and stats["skipped"] == 0
            )
        except Exception as exc:
            run_logger.warn(f"Kurtarma turu başarısız: {exc}")

    # 5) Canlı önizleme fonksiyonel testi + otomatik düzeltme -------------
    # QA her build sonunda çalışır. Sorun bulursa düzeltme kalemleri eklenip
    # uygulanır ve yeniden test edilir (en fazla `preview_qa_fix_rounds` tur).
    qa_final_report = None
    try:
        from . import plan as plan_mod
        from .executor import execute_plan as _execute_plan_sync

        plan_obj = result.get("plan")
        max_fix_rounds = max(0, int(_cfg("preview_qa_fix_rounds", 100)))
        fixes_done = 0
        while True:
            if task_control.is_cancelled(run_logger.run_id):
                return _cancelled_result(run_logger)
            qa_report, qa_items = _run_preview_qa(run_logger, project_id, spec)
            qa_final_report = qa_report
            if not qa_items or plan_obj is None or fixes_done >= max_fix_rounds:
                break
            added = plan_mod.append_items(plan_obj, qa_items, run_logger, phase="hardening")
            if not added:
                break
            run_logger.status(
                f"Canlı testten {added} düzeltme kalemi eklendi; uygulanıyor "
                f"(tur {fixes_done + 1}/{max_fix_rounds})"
            )
            _execute_plan_sync(
                plan_obj, run_logger, spec, dossier, benchmark, model_name,
                round_index=99,
            )
            try:
                progress = plan_obj.progress()
                stats.update({
                    "total": progress.get("total", stats.get("total", 0)),
                    "done": progress.get("done", stats.get("done", 0)),
                    "failed": progress.get("failed", stats.get("failed", 0)),
                    "skipped": progress.get("skipped", stats.get("skipped", 0)),
                })
            except Exception:
                pass
            fixes_done += 1
        if qa_final_report is not None and qa_final_report.blocking_issues():
            run_logger.warn(
                f"Canlı testte {len(qa_final_report.blocking_issues())} sorun devam ediyor "
                f"({fixes_done} düzeltme turu denendi)."
            )
    except Exception as exc:
        run_logger.warn(f"Canlı test entegrasyonu atlandı: {exc}")

    total = int(stats.get("total") or 0)
    done = int(stats.get("done") or 0)
    failed = int(stats.get("failed") or 0)
    skipped = int(stats.get("skipped") or 0)
    complete = total > 0 and done == total and failed == 0 and skipped == 0
    ok = bool(result.get("ok")) and complete
    forced_error = ""
    if qa_final_report is not None and qa_final_report.blocking_issues():
        qa_blocking = qa_final_report.blocking_issues()
        qa_infra_failure = all(i.category == "runtime" for i in qa_blocking)
        qa_msg = f"Canlı test: {qa_final_report.summary_text()}"
        if qa_infra_failure:
            run_logger.warn(qa_msg + " (test altyapısı çalışmadı; build başarısız sayılmadı)")
        else:
            ok = False
            forced_error = (forced_error + " " + qa_msg).strip() if forced_error else qa_msg
            run_logger.warn(qa_msg)
    if ok:
        # "1/1 kalem tamam" dese bile projede tek bir kod dosyasi yoksa is bitmis sayilmaz.
        code_files = _project_code_files()
        if not code_files:
            ok = False
            forced_error = (
                "Proje dosyaları oluşturulmadı: ajan yalnızca doküman/plan üretmiş, "
                "hiç kod dosyası (HTML/CSS/JS vb.) yazmamış."
            )
            run_logger.warn(forced_error)
    percent = int(done * 100 / total) if total else 0
    product = str(spec.get("product_name") or "Proje")
    if ok:
        output = "\n".join([
            f"## {product} hazır",
            "",
            f"- **Plan**: {done}/{total} kalem tamam, {failed} başarısız",
            f"- **Platform**: {spec.get('platform', 'web')}",
            f"- **Teknoloji**: {_project_language(spec)}",
            "",
            "Proje dosyaları hazır. Önizlemeyi açarak sonucu inceleyebilirsin.",
        ])
    else:
        error_text = forced_error or str(result.get("error") or "").strip()
        output = "\n".join([
            f"## {product} tamamlanamadı",
            "",
            f"- **Plan**: {done}/{total} kalem tamam, {failed} başarısız",
            f"- **Hata**: {error_text or 'Hiçbir plan kalemi tamamlanamadı'}",
        ])
    run_logger.log("output", output)
    run_logger.save("summary", {"text": output, "stats": stats, "spec": spec})
    _update_db(run_logger, {
        "stage": "completed" if ok else "failed",
        "status": "completed" if ok else "failed",
        "progress": percent,
        "stats": stats,
        "summary": output,
        "error": "" if ok else output[:1000],
        "finished_at": True,
    })
    _project_update(
        project_id,
        status="ready" if ok else "error",
        last_run_id=run_logger.run_id,
        last_run_status="completed" if ok else "failed",
        last_error="" if ok else output[:500],
    )
    return {
        "success": ok,
        "output": output,
        "run_id": run_logger.run_id,
        "plan": stats,
    }


# ----------------------------------------------------------------------
def _harvest_urls(dossier: dict):
    urls = []
    for note in dossier.get("documents") or []:
        url = note.get("url")
        if url:
            urls.append({"url": url, "title": note.get("title") or "",
                         "providers": note.get("providers") or ["research"],
                         "snippet": "", "query": note.get("query", "")})
    return urls


def _polish(run_logger: RunLogger, build_plan, spec: dict, request: str, model: str = None) -> dict:
    """Son kontrol: dokumantasyon uyumu + kalan basarisiz kalemlere son deneme."""
    run_logger.stage("polish", "Son kontrol ve rötuş")
    failed = build_plan.failed()
    result = {"docs": {}, "retried": 0}

    if failed:
        run_logger.status(f"{len(failed)} kalem hâlâ başarısız — son bir deneme yapılıyor")
        for item in failed:
            item.status = "pending"
        result["retried"] = len(failed)
        build_plan.save(run_logger)
        executor_mod.PlanExecutor(run_logger, spec, run_logger.load("research"),
                                  run_logger.load("benchmark"), model).run(build_plan, round_index=99)
        build_plan.save(run_logger)

    # Dokümantasyon guncelligi: proje hakkinda gercek dosya durumuyla yaz.
    try:
        from agent_app.project_scanner import execute_tool

        written = execute_tool("generate_readme", {"path": "."})
        result["docs"]["readme"] = bool(isinstance(written, dict) and written.get("ok"))
    except Exception as exc:
        logger.debug("README üretimi atlandı: %s", exc)
        result["docs"]["readme"] = False

    progress = build_plan.progress()
    run_logger.status(f"Son kontrol bitti: {progress['done']}/{progress['total']} tamam")
    run_logger.save("polish", result)
    return result


def _summary(request: str, spec: dict, dossier: dict, benchmark: dict, build_plan,
             rounds: list, polish: dict, elapsed: float) -> dict:
    progress = build_plan.progress()
    product = spec.get("product_name") or "Proje"
    requested = spec.get("user_requested_features") or []
    complete = (
        progress["total"] > 0
        and progress["done"] == progress["total"]
        and progress["failed"] == 0
        and progress["skipped"] == 0
    )

    lines = [
        f"## {product} {'hazır' if complete else 'kısmen hazır'}",
        "",
        f"- **İstek**: {request[:220]}",
        f"- **Platform**: {spec.get('platform', 'web')}",
        f"- **Süre**: {elapsed:.0f} saniye, {len(rounds)} uygulama turu",
        f"- **Plan**: {progress['total']} kalem → {progress['done']} tamam, "
        f"{progress['failed']} başarısız, {progress['skipped']} atlandı (%{progress['percent']})",
        "",
    ]
    if requested:
        lines.append("### Kullanıcının istediği (doğrulandı)")
        lines += [f"- {item}" for item in requested[:12]]
        lines.append("")
    if dossier.get("summary"):
        lines += ["### Araştırma", dossier["summary"][:900], ""]
    benchmark_analysis = (benchmark or {}).get("analysis") or {}
    if benchmark_analysis.get("what_best_looks_like"):
        lines.append("### En iyi hali böyle olmalı")
        lines += [f"- {b}" for b in benchmark_analysis["what_best_looks_like"][:8]]
        lines.append("")
    lines.append("### Tamamlanan fazlar")
    for phase, items in build_plan.by_phase().items():
        done = sum(1 for i in items if i.done())
        if done:
            lines.append(f"- {items[0].phase_label}: {done}/{len(items)}")
    remaining = build_plan.failed()
    if remaining:
        lines += ["", "### Tamamlanamayanlar"]
        lines += [f"- {i.title} ({i.result_summary[:120] or i.verify_output[:120]})" for i in remaining[:10]]

    stats = {
        "elapsed_seconds": int(elapsed),
        "rounds": rounds,
        "progress": progress,
        "documents": len(dossier.get("documents") or []),
        "sites_analyzed": (benchmark or {}).get("sites_analyzed", 0),
        "features": sum(len(g.get("features") or []) for g in spec.get("feature_groups") or []),
        "polish": polish,
    }
    return {"text": "\n".join(lines), "stats": stats}


# ----------------------------------------------------------------------
def _ensure_db(run_logger: RunLogger, request: str, model: str, project_id: str, user_id: str):
    try:
        from agent_app.models import BuildRun

        BuildRun.objects.get_or_create(
            run_id=run_logger.run_id,
            defaults={
                "request": request[:4000],
                "model": (model or "")[:120],
                "project_id": (project_id or "")[:64],
                "user_id": (user_id or "")[:64],
                "status": "researching",
                "stage": "research",
                "progress": 0,
            },
        )
    except Exception as exc:
        logger.debug("BuildRun kaydi olusturulamadi (migration gerekli olabilir): %s", exc)


def _update_db(run_logger: RunLogger, changes: dict):
    try:
        from agent_app.models import BuildRun

        finished = bool(changes.pop("finished_at", False))
        run = BuildRun.objects.filter(run_id=run_logger.run_id).first()
        if not run:
            return
        for key, value in changes.items():
            if hasattr(run, key):
                setattr(run, key, value)
        if finished:
            from django.utils import timezone

            run.finished_at = timezone.now()
        run.save()
    except Exception as exc:
        logger.debug("BuildRun guncellenemedi: %s", exc)


def _run_preview_qa(
    run_logger: RunLogger,
    project_id: str = None,
    spec: dict = None,
):
    """Canlı önizlemeyi gerçek tarayıcıda test et.

    Dönen değer: (QAReport | None, critic kalemleri listesi).
    Konsol/JS hataları, kırık linkler ve çalışmayan butonlar critic
    kalemine dönüşür; ajan bunları düzelterek tekrar test edilir.
    """
    try:
        from . import preview_qa as preview_qa_mod
    except Exception as exc:
        run_logger.warn(f"Canlı test modülü yüklenemedi: {exc}")
        return None, []

    project_root = None
    try:
        from agent_app.project_scanner import get_project_root
        project_root = get_project_root()
    except Exception:
        project_root = None

    if not project_id:
        try:
            from agent_app.project_scanner import get_project_guid
            project_id = get_project_guid()
        except Exception:
            project_id = None

    url = preview_qa_mod.detect_preview_url(
        str(project_root) if project_root else None, project_id
    )
    if not url:
        run_logger.warn("Canlı test için önizleme URL'si bulunamadı; atlandı.")
        return None, []

    run_logger.stage("tests", f"Canlı önizleme test ediliyor: {url}")
    try:
        report = preview_qa_mod.run_preview_qa_sync(
            url, run_logger,
            timeout=float(_cfg("preview_qa_timeout_seconds", 120)),
            max_buttons=int(_cfg("preview_qa_max_buttons", 8)),
            max_links=int(_cfg("preview_qa_max_links", 10)),
        )
    except Exception as exc:
        run_logger.warn(f"Canlı test çalıştırılamadı: {exc}")
        return None, []

    try:
        run_logger.save("preview_qa", report.to_dict())
    except Exception:
        pass

    checked = report.checked or {}
    run_logger.status(
        f"Canlı test: {report.summary_text()} — "
        f"{checked.get('controls_tested', 0)} buton, "
        f"{checked.get('links_checked', 0)} link kontrol edildi"
    )
    return report, preview_qa_mod.to_critic_items(report)


def _run_design_review(
    run_logger: RunLogger,
    build_plan,
    spec: dict,
    request: str,
    model: str = None,
) -> List[Dict]:
    """Görsel tasarım denetimini çalıştır, critic kalemleri olarak döndür."""
    run_logger.stage("design_review", "Görsel tasarım denetimi yapılıyor")

    # Proje kök dizinini bul
    try:
        from agent_app.project_scanner import get_project_root
        project_root = get_project_root()
    except Exception:
        project_root = str(run_logger.log_path).rsplit("task_runs", 1)[0].rstrip("\\/")

    if not project_root or not os.path.exists(project_root):
        run_logger.warn("Proje kök dizini bulunamadı, tasarım denetimi atlandı")
        return []

    # Geliştirme sunucusu URL'si
    base_url = getattr(settings, "DESIGN_REVIEW_BASE_URL", "http://localhost:8000")

    try:
        # Senkron wrapper kullan (thread-safe)
        result = design_review_mod.run_design_review_sync(
            run_logger, project_root, base_url=base_url, model=model
        )
    except Exception as exc:
        run_logger.log("exception", f"Tasarım denetimi hatası: {exc}")
        logger.exception("Design review failed")
        return []

    run_logger.save("design_review", vars(result))

    run_logger.status(
        f"Tasarım denetimi: skor {result.overall_score:.0f}/100, "
        f"{result.summary.get('total_findings', 0)} bulgu "
        f"(crit={result.summary.get('by_severity', {}).get('critical', 0)}, "
        f"maj={result.summary.get('by_severity', {}).get('major', 0)})"
    )

    # Critic kalemlerine çevir (sadece critical/major)
    items = design_review_mod.to_critic_items(result)
    return items


def _run_subagent_pipeline(
    run_logger: RunLogger,
    request: str,
    model: str = None,
    project_id: str = None,
    user_id: str = None,
    budget: Budget = None,
    agent_counts: Dict[str, int] = None,
) -> dict:
    """Subagent tabanlı pipeline çalıştır."""
    from . import subagents as subagents_mod

    run_logger.stage("start", "Subagent pipeline başlıyor")
    run_logger.info(f"İstek: {request[:4000]}")

    # Proje kök dizini
    try:
        from agent_app.project_scanner import get_project_root
        project_root = get_project_root()
    except Exception:
        project_root = str(run_logger.log_path).rsplit("task_runs", 1)[0].rstrip("\\/")

    base_url = getattr(settings, "DESIGN_REVIEW_BASE_URL", "http://localhost:8000")

    coordinator = subagents_mod.SubagentCoordinator(
        run_logger, {}, {}, {},  # spec/dossier/benchmark coordinator içinde üretilecek
        model=model, project_root=project_root, base_url=base_url
    )
    coordinator.pool.agent_counts = agent_counts or {
        "research": 2,
        "blueprint": 1,
        "plan": 1,
        "executor": int(_cfg("subagent_executors", 6)),
        "critic": 1,
        "design_review": 1,
    }
    coordinator.pool.spawn_all()

    result = coordinator.run_full_pipeline(request)

    # Budget kontrolü
    if budget and budget.exhausted():
        return _abort(run_logger, budget, "Zaman bütçesi doldu")

    elapsed = time.monotonic() - budget.started if budget else 0
    progress = result.get("plan", {})
    missing_code = progress.get("done", 0) > 0 and not _project_code_files()
    if missing_code:
        run_logger.warn(
            "Proje dosyaları oluşturulmadı: ajan hiç kod dosyası yazmamış; görev başarısız sayılıyor."
        )

    run_logger.stage("done", "Subagent pipeline tamamlandı")
    run_logger.log("output", f"Subagent pipeline: {progress.get('done', 0)}/{progress.get('total', 0)} kalem")

    _update_db(run_logger, {
        "stage": "completed" if not missing_code else "failed",
        "status": "failed" if missing_code else ("completed" if progress.get("failed", 0) == 0 else "partial"),
        "progress": progress.get("percent", 0),
        "summary": f"Subagent pipeline: {progress.get('done', 0)}/{progress.get('total', 0)} kalem",
        "finished_at": True,
    })

    success = progress.get("done", 0) > 0 and not missing_code
    output = f"Subagent pipeline tamamlandı: {progress}"
    if missing_code:
        output = "Proje dosyaları oluşturulmadı: ajan hiç kod dosyası yazmamış (yalnızca doküman/plan üretilmiş)."
    return {"success": success, "output": output, "run_id": run_logger.run_id,
            "plan": progress, "stages": result.get("stages", {})}


def _cancelled_result(run_logger: RunLogger) -> dict:
    output = (
        "## Görev iptal edildi\n\n"
        "Tarayıcı kapandı ve bu hesap için arka planda devam etme kapalı. "
        "Görev durduruldu; tekrar başlatabilirsin."
    )
    try:
        run_logger.warn(output)
        run_logger.log("output", output)
        run_logger.save("summary", {"text": output, "cancelled": True})
    except Exception:
        pass
    return {
        "success": False,
        "cancelled": True,
        "output": output,
        "error": "Görev iptal edildi.",
        "run_id": run_logger.run_id,
    }


def _pipeline_failure(run_logger: RunLogger, project_id: str, exc: Exception) -> dict:
    detail = f"{type(exc).__name__}: {exc}"
    logger.exception("build pipeline coktu: %s", detail)
    run_logger.log("exception", detail)
    run_logger.log("traceback", traceback.format_exc()[:4000])
    run_logger.stage("failed", "Uretim sirasinda hata olustu")
    _update_db(run_logger, {"stage": "failed", "status": "failed", "error": detail[:1000], "finished_at": True})
    _project_update(project_id, status="error", last_run_status="failed", last_error=detail[:500])
    return {
        "success": False,
        "output": f"Uretim sirasinda hata olustu: {detail}",
        "run_id": run_logger.run_id,
    }


def _abort(run_logger: RunLogger, budget: Budget, reason: str) -> dict:
    run_logger.warn(f"Üretim durduruldu: {reason}")
    _update_db(run_logger, {"stage": "aborted", "status": "failed", "error": reason[:500]})
    return {
        "success": False,
        "output": f"Üretim durduruldu: {reason} (süre {budget.elapsed():.0f}s)",
        "run_id": run_logger.run_id,
    }