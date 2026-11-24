"""Rakip/Referans analizi.

Arastirma dosyasi "bu alanda ne olmali" der. Bu modul "gercekte olanlar ne
yapiyor" der ve ikisini birlestirerek farkli lastigi bulur.

Akis:
  1. Aday URL topla  (arastirma sonuclarindan + scope'un ipuclarindan + alan
     adlarindan turetilen tahminlerden)
  2. Her adayi ziyaret et ve profille (extract.analyze_site)
  3. Ozellik matrisini cikar: her ozelligi kac sitede goruyoruz?
  4. LLM ile sentezle: taban gereksinimler, farklilastirici ozellikler,
     hicbirinde olmayan bosluklar, "en iyi hali" nasil olur
"""
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from django.conf import settings

from . import llm
from .events import RunLogger, utcnow
from .extract import analyze_site, summarize_site

logger = logging.getLogger(__name__)


def _cfg(key, default=None):
    return (getattr(settings, "BUILD_PIPELINE", {}) or {}).get(key, default)


def _host(url) -> str:
    try:
        return (urllib.parse.urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


SYNTHESIS_SYSTEM = """Sen rakip analizi uzmanısın. Elde ettiğin ham site profillerini
karşılaştırıp şu kararları ver:

- **baseline_features**: Bu alandaki her ciddi üründe MUTLAKA olan özellikler
  (kural: 3 veya daha fazla sitede görülmüş veya alanın tanımı gereği zorunlu).
- **differentiators**: Sadece bazılarında olan, ama gerçekten değer katan özellikler.
- **gaps**: HİÇBİRİSİNDE olmayan ama kullanıcıların beklediği fırsatlar.
- **quality_bar**: Bu alandaki iyi ürünlerin tutturduğu ölçülebilir standart
  (SEO, performans, erişilebilirlik, güvenlik).
- **what_best_looks_like**: Bu alandaki en iyi ürün nasıl olur, maddeler hâlinde.
- **warnings**: Kopyalanmaması gerekenler (kötü pattern'ler, aşırı karmaşıklık).

Ham profiller kanıttır; olmayan bir şeyi uydurma. Sayıları profile'daki
gerçek değerlerden al.

JSON ŞEMA:
{
  "baseline_features": [{"feature": "...", "seen_in": "4/6 site", "why": "..."}],
  "differentiators": [{"feature": "...", "seen_in": "...", "value": "..."}],
  "gaps": [{"gap": "...", "opportunity": "..."}],
  "quality_bar": {"seo": [], "performance": [], "accessibility": [], "security": []},
  "what_best_looks_like": ["..."],
  "warnings": ["..."],
  "observed_tech": ["..."]
}"""


class Benchmarker:
    """Alandaki gerçek ürünleri ziyaret edip kıyaslar."""

    def __init__(self, run_logger: RunLogger, model: str = None):
        self.log = run_logger
        self.model = model
        self.max_sites = int(_cfg("benchmark_sites", 8))
        self.max_workers = int(_cfg("benchmark_workers", 5))
        self.timeout = int(_cfg("benchmark_timeout", 15))
        self.sample_chars = int(_cfg("benchmark_sample_chars", 500))

    def run(self, scope: Dict, harvest: Dict) -> Dict:
        self.log.stage("benchmark", "Rakipler inceleniyor")
        candidates = self._collect_candidates(scope, harvest)
        self.log.info(f"Rakip adayı: {len(candidates)} URL")
        self.log.status(
            f"{len(candidates)} rakip adayı bulundu: "
            + ", ".join(_host(url) for url in candidates[:6])
            + (" …" if len(candidates) > 6 else "")
        )

        profiles = self._visit(candidates)
        profiles = [p for p in profiles if p.get("seo")]
        feature_total = sum(len(p.get("features") or {}) for p in profiles)
        self.log.status(
            f"{len(profiles)} site profillendi · {feature_total} özellik kaydı · "
            f"{len({p.get('url') for p in profiles})} benzersiz site"
        )

        matrix = self._feature_matrix(profiles)
        analysis = self._synthesize(profiles, matrix, scope)

        result = {
            "profiles": profiles,
            "feature_matrix": matrix,
            "analysis": analysis,
            "sites_analyzed": len(profiles),
            "generated_at": utcnow(),
        }
        self.log.save("benchmark", result)
        self.log.stage("benchmark", f"Rakip analizi tamamlandı ({len(profiles)} site)")
        return result

    # ------------------------------------------------------------------
    def _collect_candidates(self, scope: Dict, harvest: Dict) -> List[str]:
        urls: List[str] = []
        for item in harvest.get("all", []):
            url = item.get("url") or ""
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            if host and host not in ("wikipedia.org", "stackoverflow.com", "github.com"):
                urls.append(url)

        for hint in scope.get("competitor_hints") or []:
            if isinstance(hint, str) and hint.strip():
                candidate = hint.strip()
                if candidate.startswith("http"):
                    urls.append(candidate)
                else:
                    urls.append(f"https://{candidate}")

        # Ayni domaini tekrarlama.
        seen_hosts, unique = set(), []
        for url in urls:
            host = (urllib.parse.urlparse(url).hostname or "").lower()
            if not host or host in seen_hosts:
                continue
            seen_hosts.add(host)
            unique.append(url)
            if len(unique) >= self.max_sites * 3:
                break
        return unique[: self.max_sites * 2]

    # ------------------------------------------------------------------
    def _visit(self, candidates: List[str]) -> List[Dict]:
        profiles: List[Dict] = []

        def visit(url: str) -> Dict:
            try:
                profile = analyze_site(url, timeout=self.timeout, max_text=4000)
            except Exception as exc:
                logger.debug("rakip ziyareti basarisiz %s: %s", url, exc)
                return {"url": url, "ok": False, "error": str(exc)[:200]}
            profile["summary"] = summarize_site(profile, max_chars=self.sample_chars)
            profile.pop("content_sample", None)  # ham metni saklamaya gerek yok
            return profile

        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(candidates)))) as pool:
            futures = {pool.submit(visit, url): url for url in candidates}
            for index, future in enumerate(as_completed(futures), start=1):
                try:
                    profile = future.result()
                except Exception:
                    continue
                profiles.append(profile)
                if index % 3 == 0 or index == len(candidates):
                    title = profile.get("title") or _host(profile.get("url"))
                    self.log.status(
                        f"Rakip ziyareti: {index}/{len(candidates)} — "
                        f"{title} ({_host(profile.get('url'))})"
                    )
        return profiles

    # ------------------------------------------------------------------
    def _feature_matrix(self, profiles: List[Dict]) -> Dict:
        """Her özelliğin kaç sitede görüldüğü + ortalama kalite metrikleri."""
        totals: Dict[str, int] = {}
        for profile in profiles:
            for name, on in (profile.get("features") or {}).items():
                if on:
                    totals[name] = totals.get(name, 0) + 1

        count = max(1, len(profiles))
        features = [
            {"feature": name, "sites": hits, "of": len(profiles), "share": round(hits / count, 2)}
            for name, hits in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        def average(path: List[str]):
            values = []
            for profile in profiles:
                cursor = profile
                for step in path:
                    cursor = (cursor or {}).get(step) if isinstance(cursor, dict) else None
                if isinstance(cursor, (int, float)):
                    values.append(cursor)
            return round(sum(values) / len(values), 2) if values else None

        return {
            "features": features,
            "site_count": len(profiles),
            "tech_frequency": self._frequency(profiles, "tech"),
            "analytics_frequency": self._frequency(profiles, "analytics"),
            "ads_frequency": self._frequency(profiles, "ads"),
            "averages": {
                "html_kb": average(["perf", "html_kb"]),
                "scripts": average(["perf", "script_count"]),
                "images": average(["perf", "image_count"]),
                "alt_ratio": average(["a11y", "images_alt_ratio"]),
                "has_description_share": average(["seo", "has_description"]),
                "jsonld_share": average(["seo", "jsonld_types"]),
                "has_viewport_share": average(["a11y", "has_viewport"]),
            },
        }

    @staticmethod
    def _frequency(profiles: List[Dict], key: str, limit: int = 15) -> List[dict]:
        counter: Dict[str, int] = {}
        for profile in profiles:
            for name in profile.get(key) or []:
                counter[name] = counter.get(name, 0) + 1
        return [
            {"name": name, "count": hits}
            for name, hits in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
        ]

    # ------------------------------------------------------------------
    def _synthesize(self, profiles: List[Dict], matrix: Dict, scope: Dict) -> Dict:
        if not profiles:
            self.log.warn("Hiçbir rakip siteye erişilemedi; kıyaslama atlandı")
            return {
                "baseline_features": [], "differentiators": [], "gaps": [],
                "quality_bar": {}, "what_best_looks_like": [], "warnings": [],
                "observed_tech": [],
                "note": "Rakip sitelere erişilemediği için kıyaslama yapılamadı.",
            }

        site_block = "\n\n".join(
            f"### {p.get('title') or p.get('domain')} — {p.get('final_url') or p.get('url')}\n{p.get('summary') or ''}"
            for p in profiles
        )
        feature_rows = "\n".join(
            f"- {f['feature']}: {f['sites']}/{f['of']} sitede"
            for f in matrix["features"][:40]
        )
        averages = "\n".join(f"- {k}: {v}" for k, v in (matrix.get("averages") or {}).items())

        user = (
            f"Alanda: {scope.get('domain')} (platform: {scope.get('platform')})\n"
            f"Rakip aday ipuçları: {', '.join(scope.get('competitor_hints') or []) or '-'}\n\n"
            f"İNCELELEN SİTELER ({len(profiles)}):\n{site_block[:40_000]}\n\n"
            f"ÖZELLİK MATRİSİ:\n{feature_rows}\n\n"
            f"ORTALAMALAR:\n{averages}\n\n"
            "Bu verilere dayanarak kıyaslama sentezini üret."
        )
        analysis = llm.as_dict(
            llm.ask_json(SYNTHESIS_SYSTEM, user, model=self.model, purpose="research", temperature=0.25)
        )
        for key in ("baseline_features", "differentiators", "gaps", "what_best_looks_like",
                    "warnings", "observed_tech"):
            if not isinstance(analysis.get(key), list):
                analysis[key] = []
        if not isinstance(analysis.get("quality_bar"), dict):
            analysis["quality_bar"] = {}
        for key in ("seo", "performance", "accessibility", "security"):
            analysis["quality_bar"].setdefault(key, [])
        return analysis

    # ------------------------------------------------------------------
    @staticmethod
    def format_for_prompt(profiles: List[Dict], analysis: Dict, limit: int = 6000) -> str:
        """Benchmark ciktisini plan/prompt'a sokulacak metne cevirir."""
        analysis = analysis or {}
        lines = ["RAKİP ANALİZİ ÖZETİ"]
        baseline = analysis.get("baseline_features") or []
        if baseline:
            lines.append("Taban gereksinimler (herkesin var olduğu):")
            lines += [f"  - {_item_title(f)} ({f.get('seen_in', '')})" for f in baseline[:18]]
        differentiators = analysis.get("differentiators") or []
        if differentiators:
            lines.append("Farklılaştırıcı özellikler:")
            lines += [f"  - {_item_title(f)} → {f.get('value', '')}" for f in differentiators[:12]]
        gaps = analysis.get("gaps") or []
        if gaps:
            lines.append("Hiçbirinde olmayan fırsatlar:")
            lines += [f"  - {_item_title(g)} → {g.get('opportunity', '')}" for g in gaps[:12]]
        best = analysis.get("what_best_looks_like") or []
        if best:
            lines.append("En iyi hali böyle olmalı:")
            lines += [f"  - {b}" for b in best[:12]]
        warnings = analysis.get("warnings") or []
        if warnings:
            lines.append("Uyarılar:")
            lines += [f"  - {w}" for w in warnings[:8]]
        return "\n".join(lines)[:limit]


def _item_title(item) -> str:
    if isinstance(item, dict):
        return str(item.get("feature") or item.get("gap") or item.get("pitfall") or item)
    return str(item)


def run_benchmark(scope: Dict, harvest: Dict, run_logger: RunLogger, model: str = None) -> Dict:
    return Benchmarker(run_logger, model=model).run(scope, harvest)


import asyncio

async def run_benchmark_async(scope: Dict, harvest: Dict, run_logger: RunLogger, model: str = None) -> Dict:
    """Async version of run_benchmark."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: run_benchmark(scope, harvest, run_logger, model))