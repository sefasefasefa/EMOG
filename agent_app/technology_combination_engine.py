"""
EMOG Technology Combination Engine (Teknoloji Kombinasyon Motoru)

Farkli teknolojileri otomatik olarak birlestirerek yeni cozum yollari
ureten motor. Kullanici tarafindan herhangi bir secim yapmaya gerek yoktur,
EMOG otomatik olarak en uygun kombinasyonu secer ve harekete gecer.

Ornek:
Web Crawler + Semantic Search + Knowledge Graph + Entity Resolution + Automation
   = Yeni musteri keşif sistemi
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import itertools
import uuid
import time


# ---------------------------------------------------------------------------
# Teknoloji Modeli
# ---------------------------------------------------------------------------

@dataclass
class Technology:
    """Tek bir teknoloji bileşeni."""
    id: str
    name: str
    category: str
    description: str
    capabilities: List[str]
    requirements: List[str]
    outputs: List[str]
    cost_level: int = 1
    complexity_level: int = 1

    def __repr__(self) -> str:
        return f"Technology({self.name}, {self.category})"


# ---------------------------------------------------------------------------
# Teknoloji Kombinasyonu
# ---------------------------------------------------------------------------

@dataclass
class TechnologyCombination:
    """Birden fazla teknolojinin birlesimiyle oluşan yeni cozum."""
    id: str
    name: str
    description: str
    technologies: List[Technology]
    combined_capabilities: List[str]
    synergy_score: float
    implementation_complexity: int
    estimated_cost: int
    use_cases: List[str]
    auto_executable: bool = True  # Bu kombinasyon otomatik olarak calistirilir mi?

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "technologies": [t.name for t in self.technologies],
            "combined_capabilities": self.combined_capabilities,
            "synergy_score": self.synergy_score,
            "implementation_complexity": self.implementation_complexity,
            "estimated_cost": self.estimated_cost,
            "use_cases": self.use_cases,
            "auto_executable": self.auto_executable,
        }


# ---------------------------------------------------------------------------
# Teknoloji Kombinasyon Motoru
# ---------------------------------------------------------------------------

class TechnologyCombinationEngine:
    """
    Farkli teknolojileri otomatik olarak birlestirerek yeni cozum yollari
    ureten motor. Kullanici tarafindan herhangi bir secim yapmaya gerek yoktur,
    EMOG otomatik olarak en uygun kombinasyonu secer ve harekete gecer.
    """

    def __init__(self) -> None:
        self.technologies: List[Technology] = []
        self.combinations: List[TechnologyCombination] = []
        self._initialize_default_technologies()

    def _initialize_default_technologies(self) -> None:
        """Onceden tanımlanmis teknoloji kutukphanesini yukler."""
        self.technologies = [
            Technology(
                id="web_crawler",
                name="Web Crawler",
                category="data_collection",
                description="Web sitelerinden veri toplama",
                capabilities=["web_scraping", "data_extraction", "link_crawling"],
                requirements=["python", "requests", "beautifulsoup"],
                outputs=["structured_data", "html_content"],
                cost_level=2,
                complexity_level=2,
            ),
            Technology(
                id="semantic_search",
                name="Semantic Search",
                category="data_collection",
                description="Anlamsal arama ve anlam tabanli veri bulma",
                capabilities=["semantic_search", "vector_search", "embedding_search"],
                requirements=["sentence_transformers", "faiss", "pinecone"],
                outputs=["semantic_vectors", "ranked_results"],
                cost_level=3,
                complexity_level=3,
            ),
            Technology(
                id="knowledge_graph",
                name="Knowledge Graph",
                category="data_storage",
                description="Bilgi grafigi ile iliskisel veri saklama",
                capabilities=["graph_storage", "relationship_mapping", "entity_linking"],
                requirements=["neo4j", "networkx", "rdflib"],
                outputs=["graph_data", "relationship_maps"],
                cost_level=4,
                complexity_level=4,
            ),
            Technology(
                id="entity_resolution",
                name="Entity Resolution",
                category="data_processing",
                description="Ayni varligin farkli kayitlarini birlestirme",
                capabilities=["entity_matching", "record_linkage", "deduplication"],
                requirements=["recordlinkage", "dedupe", "fuzzywuzzy"],
                outputs=["resolved_entities", "golden_records"],
                cost_level=3,
                complexity_level=3,
            ),
            Technology(
                id="automation_engine",
                name="Automation Engine",
                category="automation",
                description="Otomatik is akislari ve eylem yurutme",
                capabilities=["workflow_automation", "task_scheduling", "api_integration"],
                requirements=["celery", "prefect", "airflow"],
                outputs=["automated_workflows", "execution_logs"],
                cost_level=3,
                complexity_level=4,
            ),
            Technology(
                id="nlp_engine",
                name="NLP Engine",
                category="data_processing",
                description="Dogal dil isleme ve metin analizi",
                capabilities=["text_analysis", "sentiment_analysis", "ner"],
                requirements=["spacy", "nltk", "transformers"],
                outputs=["extracted_entities", "sentiment_scores", "summaries"],
                cost_level=3,
                complexity_level=4,
            ),
            Technology(
                id="database",
                name="Database",
                category="data_storage",
                description="Veritabani saklama ve sorgulama",
                capabilities=["data_storage", "querying", "indexing"],
                requirements=["postgresql", "mongodb", "elasticsearch"],
                outputs=["structured_data", "query_results"],
                cost_level=2,
                complexity_level=2,
            ),
            Technology(
                id="visualization_engine",
                name="Visualization Engine",
                category="visualization",
                description="Veri gorsellestirme ve raporlama",
                capabilities=["data_visualization", "dashboard_creation", "chart_generation"],
                requirements=["plotly", "d3.js", "tableau"],
                outputs=["charts", "dashboards", "reports"],
                cost_level=3,
                complexity_level=3,
            ),
            Technology(
                id="alert_system",
                name="Alert System",
                category="automation",
                description="Otomatik uyari ve bildirim sistemi",
                capabilities=["alert_generation", "notification", "escalation"],
                requirements=["smtp", "slack_api", "twilio"],
                outputs=["alerts", "notifications"],
                cost_level=2,
                complexity_level=2,
            ),
        ]

    def generate_combinations(
        self,
        task_description: str,
        max_combinations: int = 5,
        min_technologies: int = 2,
        max_technologies: int = 5,
    ) -> List[TechnologyCombination]:
        """
        Verilen gorev icin en uygun teknoloji kombinasyonlarini otomatik olarak
        uretir. Kullanici tarafindan herhangi bir secim yapmaya gerek yoktur,
        EMOG otomatik olarak en uygun kombinasyonu secer ve harekete gecer.

        Args:
            task_description: Kullanicinin istegi
            max_combinations: Maksimum kombinasyon sayisi
            min_technologies: En az kac teknoloji icerecek
            max_technologies: En fazla kac teknoloji icerecek

        Returns:
            Sirali kombinasyon listesi
        """
        task_lower = task_description.lower()
        scored_combinations: List[TechnologyCombination] = []

        # Tum olasi kombinasyonlari dene (2'den max_technologies'ye kadar)
        for r in range(min_technologies, min(max_technologies + 1, len(self.technologies) + 1)):
            for combo in itertools.combinations(self.technologies, r):
                score = self._calculate_combination_score(combo, task_description)
                if score > 0.1:
                    combination = self._create_combination(combo, task_description, score)
                    scored_combinations.append(combination)

        # Skora gore sirala (yuksekten dusuge)
        scored_combinations.sort(key=lambda x: x.synergy_score, reverse=True)

        return scored_combinations[:max_combinations]

    def _calculate_combination_score(
        self,
        technologies: tuple,
        task_description: str,
    ) -> float:
        """Bir kombinasyonun gorev ile olan uygunluk skorunu hesaplar."""
        task_lower = task_description.lower()
        score = 0.0

        # Her teknolojinin task ile uygunlugu
        for tech in technologies:
            tech_name = tech.name.lower()
            tech_desc = tech.description.lower()
            tech_caps = " ".join(tech.capabilities).lower()

            for word in task_lower.split():
                if word in tech_name or word in tech_desc or word in tech_caps:
                    score += 0.1

        # Kombinasyon denge bonusu (farkli kategorilerden tekniler daha iyi)
        categories = set(t.category for t in technologies)
        category_bonus = len(categories) * 0.1
        score += min(category_bonus, 0.3)

        # Teknoloji sayisi bonusu (daha fazla teknik = daha kapsamli cozum)
        tech_count_bonus = min(len(technologies) * 0.05, 0.2)
        score += tech_count_bonus

        return min(score, 1.0)

    def _create_combination(
        self,
        technologies: tuple,
        task_description: str,
        score: float,
    ) -> TechnologyCombination:
        """Teknoloji kombinasyonu olusturur (otomatik, kullanici müdahalesi yok)."""

        # Tum yetkileri birlestir
        all_capabilities = []
        for tech in technologies:
            all_capabilities.extend(tech.capabilities)
        combined_capabilities = list(set(all_capabilities))

        # Kullanim senaryolari olustur
        use_cases = self._generate_use_cases(technologies, task_description)

        return TechnologyCombination(
            id=str(uuid.uuid4()),
            name=self._generate_combination_name(technologies),
            description=self._generate_combination_description(technologies),
            technologies=list(technologies),
            combined_capabilities=combined_capabilities,
            synergy_score=score,
            implementation_complexity=max(t.complexity_level for t in technologies),
            estimated_cost=max(t.cost_level for t in technologies),
            use_cases=use_cases,
            auto_executable=True,  # Her zaman otomatik calistirilir
        )

    def _generate_combination_name(self, technologies: tuple) -> str:
        """Kombinasyon icin isim olusturur."""
        names = [t.name for t in technologies]
        if len(names) == 2:
            return f"{names[0]} + {names[1]} Integration"
        elif len(names) == 3:
            return f"{names[0]} + {names[1]} + {names[2]} Ecosystem"
        else:
            return f"{len(names)} Component Complex System"

    def _generate_combination_description(self, technologies: tuple) -> str:
        """Kombinasyon icin aciklama olusturur."""
        names = [t.name for t in technologies]
        categories = set(t.category for t in technologies)
        
        parts = []
        if len(technologies) == 2:
            parts.append(f"{names[0]} ve {names[1]}'nin birleşimiyle")
        elif len(technologies) == 3:
            parts.append(f"{names[0]}, {names[1]} ve {names[2]}'nin birleşimiyle")
        else:
            parts.append(f"{', '.join(names[:-1])} ve {names[-1]}'nin birleşimiyle")

        if "data_collection" in categories and "data_processing" in categories:
            parts.append("veri toplayıp işleyerek")
        if "data_collection" in categories and "automation" in categories:
            parts.append("otomatik veri toplayarak")
        if "data_storage" in categories and "data_processing" in categories:
            use_cases = "saklayarak ve işleyerek"
            parts.append(use_cases)
        if "visualization" in categories:
            parts.append("görselleştirerek")
        if "automation" in categories:
            parts.append("otomatikleştirerek")

        return " ".join(parts) + "."

    def _generate_use_cases(
        self,
        technologies: tuple,
        task_description: str,
    ) -> List[str]:
        """Kombinasyon icin kullanım senaryolari olusturur."""
        tech_names = [t.name for t in technologies]
        categories = set(t.category for t in technologies)
        task_lower = task_description.lower()

        use_cases: List[str] = []

        # Kategori tabanli senaryolar
        if "data_collection" in categories and "automation" in categories:
            use_cases.append("Otomatik veri toplayici ve isleme hatti")
        if "data_collection" in categories and "data_storage" in categories:
            use_cases.append("Veri depositsi ve arsivleme sistemi")
        if "data_processing" in categories and "visualization" in categories:
            use_cases.append("Veri analitiği ve raporlama paneli")
        if "automation" in categories and "data_storage" in categories:
            use_cases.append("Otomatik veri isleme ve saklama hatti")
        if "data_collection" in categories and "automation" in categories and "visualization" in categories:
            use_cases.append("Tamamlayici veri analitik platformu")

        # Task tabanli senaryolar
        if "musteri" in task_lower or "customer" in task_lower.lower():
            use_cases.append("Musteri keşfi ve segmentasyon sistemi")
        if "rapor" in task_lower or "report" in task_lower.lower():
            use_cases.append("Otomatik raporlama ve dashboard sistemi")
        if "analiz" in task_lower or "analysis" in task_lower.lower():
            use_cases.append("Veri analitiği ve içgörü platformu")

        # Genel fallback
        if not use_cases:
            use_cases.append(f"{', '.join(t.name for t in technologies)} tabanli is akisi sistemi")

        return use_cases[:5]  # Maksimum 5 senaryo

    def get_all_technologies(self) -> List[dict]:
        """Tum mevcut teknolojileri dondurur."""
        return [
            {
                "id": t.id,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "capabilities": t.capabilities,
            }
            for t in self.technologies
        ]

    def get_top_combination(
        self,
        task_description: str,
    ) -> Optional[TechnologyCombination]:
        """
        Tek bir en iyi kombinasyonu dondurur (otomatik secim).
        """
        combinations = self.generate_combinations(task_description, max_combinations=1)
        return combinations[0] if combinations else None


# Global instance
discovery_engine_combination = TechnologyCombinationEngine()


if __name__ == "__main__":
    engine = TechnologyCombinationEngine()
    combinations = engine.generate_combinations(
        task_description="Istanbul'daki potansiyel musteri firmalari bul",
        max_combinations=5,
    )
    print("Gorev: Istanbul'daki potansiyel musteri firmalari bul")
    print(f"Toplam kombinasyon: {len(combinations)}")
    print()
    for combo in combinations:
        print(f"- {combo.name}")
        print(f"  Teknolojiler: {', '.join(t.name for t in combo.technologies)}")
        print(f"  Skor: {combo.synergy_score:.2f}")
        print(f"  Yetenekler: {', '.join(combo.combined_capabilities)}")
        print(f"  Kullanim: {', '.join(combo.use_cases[:3])}")
        print()
