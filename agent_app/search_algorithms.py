"""
EMOG Discovery Engine - Arama Algoritmaları Modülü

Bu modül, Discovery Engine ile kullanılan arama algoritmalarını içerir:
- MCTS (Monte Carlo Tree Search)
- Genetic Algorithm
- Beam Search
- Bayesian Optimization
- Multi-Armed Bandit
- Constraint Optimization

Her algoritma farklı senaryolar için kullanılabilir.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import random
import math
import heapq
import numpy as np


# ---------------------------------------------------------------------------
# 1. Monte Carlo Tree Search (MCTS)
# ---------------------------------------------------------------------------

class MCTSNode:
    """MCTS düğümü."""
    def __init__(self, state: Any, parent: Optional['MCTSNode'] = None):
        self.state = state
        self.parent = parent
        self.children: List[MCTSNode] = []
        self.visits = 0
        self.value = 0.0

    def select_child(self) -> 'MCTSNode':
        """UCB1 ile en iyi çocuk seç."""
        best_child = None
        best_value = float('-inf')
        for child in self.children:
            if child.visits == 0:
                return child
            ucb1 = child.value / child.visits + 1.41 * math.sqrt(math.log(self.visits) / child.visits)
            if ucb1 > best_value:
                best_value = ucb1
                best_child = child
        return best_child

    def expand(self) -> 'MCTSNode':
        """Yeni çocuk ekle."""
        child = MCTSNode(state=None, parent=self)
        self.children.append(child)
        return child

    def backpropagate(self, result: float) -> None:
        """Sonucu geri yay."""
        node = self
        while node is not None:
            node.visits += 1
            node.value += result
            node = node.parent


def mcts_search(
    candidates: List[Dict[str, Any]],
    iterations: int = 100,
    timeout: float = 5.0
) -> Tuple[Any, float]:
    """
    MCTS ile en iyi seçeneği seç.

    Args:
        candidates: Seçenek listesi
        iterations: Maksimum iterasyon sayısı
        timeout: Maksimum süre (saniye)

    Returns:
        (seçilen seçenek, skor)
    """
    if not candidates:
        return None, 0.0

    root = MCTSNode(state=None)

    for _ in range(iterations):
        # Selection
        node = root
        while node.children:
            node = node.select_child()

        # Expansion
        if not node.children:
            for candidate in candidates:
                node.children.append(MCTSNode(state=candidate, parent=node))

        # Selection from new children
        if node.children:
            node = node.select_child()

        # Simulation (basit skora göre)
        result = node.state.get('score', 0.5) if node.state else 0.5

        # Backpropagation
        node.backpropagate(result)

    # En iyi çocuğu seç
    best_child = max(root.children, key=lambda c: c.visits) if root.children else None
    if best_child:
        return best_child.state, best_child.value / max(best_child.visits, 1)
    return None, 0.0


# ---------------------------------------------------------------------------
# 2. Genetic Algorithm
# ---------------------------------------------------------------------------

class GeneticAlgorithm:
    """
    Genetik Algoritma - kombinasyon seçimi için.
    """

    def __init__(
        self,
        population_size: int = 50,
        mutation_rate: float = 0.1,
        crossover_rate: float = 0.7,
        generations: int = 20,
    ):
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.generations = generations

    def evolve(
        self,
        candidates: List[Dict[str, Any]],
        fitness_func: callable,
    ) -> Tuple[Any, float]:
        """
        Genetik algoritma ile en iyi çözümü bulur.

        Args:
            candidates: Seçenek listesi
            fitness_func: Skorlama fonksiyonu

        Returns:
            (en iyi seçenek, skor)
        """
        if not candidates:
            return None, 0.0

        # Popülasyon başlat
        population = list(candidates)
        while len(population) < self.population_size:
            population.append(random.choice(candidates))

        best = None
        best_score = -1.0

        for generation in range(self.generations):
            # Skorları hesapla
            scores = [fitness_func(c) for c in population]

            # En iyi
            best_idx = max(range(len(population)), key=lambda i: scores[i])
            if scores[best_idx] > best_score:
                best_score = scores[best_idx]
                best = population[best_idx]

            # Seçim (turnuva)
            parents = []
            for _ in range(self.population_size):
                participants = random.sample(range(len(population)), min(3, len(population)))
                best_participant = max(participants, key=lambda i: scores[i])
                parents.append(population[best_participant])

            # Çaprazlama ve mutasyon
            new_population = []
            while len(new_population) < self.population_size:
                parent1 = random.choice(parents)
                parent2 = random.choice(parents)

                if random.random() < self.crossover_rate:
                    child = self._crossover(parent1, parent2)
                else:
                    child = parent1.copy() if isinstance(parent1, dict) else dict(parent1)

                if random.random() < self.mutation_rate:
                    child = self._mutate(child)

                new_population.append(child)

            population = new_population

        return best, best_score

    def _crossover(self, parent1: Dict, parent2: Dict) -> Dict:
        """İki ebeveynden çocuk oluşturur."""
        child = {}
        for key in set(list(parent1.keys()) + list(parent2.keys())):
            if random.random() < 0.5:
                child[key] = parent1.get(key)
            else:
                child[key] = parent2.get(key)
        return child

    def _mutate(self, individual: Dict) -> Dict:
        """Bireyi mutate eder."""
        individual = individual.copy()
        if 'score' in individual and random.random() < 0.5:
            individual['score'] = max(0, individual['score'] + random.uniform(-0.1, 0.1))
        return individual


# ---------------------------------------------------------------------------
# 3. Beam Search
# ---------------------------------------------------------------------------

class BeamSearch:
    """
    Beam Search - sıralı arama.
    """

    def __init__(self, beam_width: int = 5):
        self.beam_width = beam_width

    def search(
        self,
        candidates: List[Dict[str, Any]],
        score_func: callable,
    ) -> Tuple[Any, float]:
        """
        Beam search ile en iyi seçeneği bulur.

        Args:
            candidates: Seçenek listesi
            score_func: Skorlama fonksiyonu

        Returns:
            (en iyi seçenek, skor)
        """
        if not candidates:
            return None, 0.0

        current_beam = candidates[:self.beam_width]

        # Tüm adımları değerlendir
        scored = [(c, score_func(c)) for c in current_beam]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[0][0], scored[0][1]


# ---------------------------------------------------------------------------
# 4. Bayesian Optimization
# ---------------------------------------------------------------------------

class BayesianOptimization:
    """
    Basit Bayesian Optimizasyon - hiperparametre optimizasyonu için.
    """

    def __init__(self, n_iterations: int = 20):
        self.n_iterations = n_iterations

    def optimize(
        self,
        candidates: List[Dict[str, Any]],
        score_func: callable,
    ) -> Tuple[Any, float]:
        """
        Bayesian optimizasyon ile en iyi seçeneği bulur.

        Args:
            candidates: Seçenek listesi
            score_func: Skorlama fonksiyonu

        Returns:
            (en iyi seçenek, skor)
        """
        if not candidates:
            return None, 0.0

        # İlk değerlendirme
        results = []
        for c in candidates[: min(len(candidates), 5)]:
            score = score_func(c)
            results.append((c, score))

        best = max(results, key=lambda x: x[1])

        # Basit Gaussian Process gibi yaklaşım: en iyi yakınındaki seçenekleri dene
        for _ in range(self.n_iterations):
            # En iyi seçeneğin yakınında yeni adaylar oluştur
            candidates_to_try = []
            for c, s in sorted(results, key=lambda x: x[1], reverse=True)[:3]:
                for _ in range(2):
                    new_c = c.copy()
                    # Küçük değişim
                    if 'score' in new_c:
                        new_c['score'] = max(0, new_c['score'] + random.uniform(-0.05, 0.05))
                    candidates_to_try.append(new_c)

            for c in candidates_to_try:
                score = score_func(c)
                results.append((c, score))
                if score > best[1]:
                    best = (c, score)

        return best[0], best[1]


# ---------------------------------------------------------------------------
# 5. Multi-Armed Bandit (MAB)
# ---------------------------------------------------------------------------

class MultiArmedBandit:
    """
    Çok kolalı bandit problemi - keşif ve istismar dengelemesi.
    """

    def __init__(self, exploration_rate: float = 0.2):
        self.exploration_rate = exploration_rate
        self.arm_stats: Dict[str, Dict[str, float]] = {}

    def select(
        self,
        candidates: List[Dict[str, Any]],
        score_func: callable,
    ) -> Tuple[Any, float]:
        """
        MAB ile en iyi seçeneği seçer.

        Args:
            candidates: Seçenek listesi
            score_func: Skorlama fonksiyonu

        Returns:
            (en iyi seçenek, skor)
        """
        if not candidates:
            return None, 0.0

        best_candidate = None
        best_score = -1.0

        for c in candidates:
            arm_id = c.get('id', str(hash(str(c))))
            stats = self.arm_stats.get(arm_id, {'count': 0, 'total_reward': 0.0})

            if stats['count'] == 0:
                # İlk kez görülüyorsa, keşfet
                score = 1.0  # Maksimum skor
            else:
                # UCB1 formülü
                avg_reward = stats['total_reward'] / stats['count']
                exploration_bonus = self.exploration_rate * math.sqrt(
                    math.log(sum(s['count'] for s in self.arm_stats.values())) / stats['count']
                )
                score = avg_reward + exploration_bonus

            if score > best_score:
                best_score = score
                best_candidate = c

        # Gerçek skoru hesapla
        actual_score = score_func(best_candidate)

        # İstatistikleri güncelle
        arm_id = best_candidate.get('id', str(hash(str(best_candidate))))
        if arm_id not in self.arm_stats:
            self.arm_stats[arm_id] = {'count': 0, 'total_reward': 0.0}
        self.arm_stats[arm_id]['count'] += 1
        self.arm_stats[arm_id]['total_reward'] += actual_score

        return best_candidate, actual_score


# ---------------------------------------------------------------------------
# 6. Constraint Optimization
# ---------------------------------------------------------------------------

class ConstraintOptimizer:
    """
    Kısıtlamalı optimizasyon - kısıtlamaları sağlayan en iyi çözümü bulur.
    """

    def __init__(self, constraints: Optional[List[callable]] = None):
        self.constraints = constraints or []

    def optimize(
        self,
        candidates: List[Dict[str, Any]],
        score_func: callable,
    ) -> Tuple[Any, float]:
        """
        Kısıtlamalı optimizasyon.

        Args:
            candidates: Seçenek listesi
            score_func: Skorlama fonksiyonu

        Returns:
            (en iyi seçenek, skor)
        """
        valid_candidates = []

        for c in candidates:
            valid = True
            for constraint in self.constraints:
                if not constraint(c):
                    valid = False
                    break
            if valid:
                valid_candidates.append(c)

        if not valid_candidates:
            return None, 0.0

        best = max(valid_candidates, key=score_func)
        return best, score_func(best)


# ---------------------------------------------------------------------------
# Discovery Engine entegrasyonu
# ---------------------------------------------------------------------------

class DiscoveryEngineWithSearch:
    """
    Discovery Engine'e entegre edilmiş arama algoritmaları.
    """

    def __init__(self) -> None:
        self.mcts = MCTSNode
        self.genetic = GeneticAlgorithm()
        self.beam_search = BeamSearch()
        self.bayesian = BayesianOptimization()
        self.mab = MultiArmedBandit()
        self.constraint_optimizer = ConstraintOptimizer()

    def discover_with_algorithm(
        self,
        task_description: str,
        context: Dict[str, Any],
        algorithm: str = "genetic",
        max_hypotheses: int = 5,
    ) -> Dict[str, Any]:
        """
        Belirli bir algoritma ile keşif yapar.

        Args:
            task_description: Görev açıklaması
            context: Bağlam bilgileri
            max_hypotheses: Maksimum hipótesis sayısı
            algorithm: Kullanılacak algoritma ('mcts', 'genetic', 'beam_search',
                        'bayesian', 'mab', 'constraint')

        Returns:
            DiscoveryResult nesnesi
        """
        # Üst sınıfın discover metodunu çağır
        from agent_app.discovery_engine import discovery_engine
        result = discovery_engine.discover(task_description, context, max_hypotheses)

        # Seçilen algoritma ile yeniden değerlendir
        if result.best_hypothesis:
            # Seçenekleri hazırla
            candidates = [
                {
                    'id': h.id,
                    'name': h.name,
                    'description': h.description,
                    'data_source': h.data_source,
                    'score': h.applicability_score,
                }
                for h in hypotheses
            ]

            # Algoritma seç
            if algorithm == 'genetic':
                best, score = self.genetic.evolve(candidates, lambda c: c.get('score', 0.5))
            elif algorithm == 'beam_search':
                best, score = self.beam_search.search(candidates, lambda c: c.get('score', 0.5))
            elif algorithm == 'bayesian':
                best, score = self.bayesian.optimize(candidates, lambda c: c.get('score', 0.5))
            elif algorithm == 'mab':
                best, score = self.mab.select(candidates, lambda c: c.get('score', 0.5))
            else:
                best, score = None, 0.0

            if best:
                result.best_hypothesis.name = best.get('name', best.get('id', 'unknown'))
                result.best_hypothesis.applicability_score = score
                result.reasoning = f"'{task_description}' için {algorithm} algoritması ile seçilen en uygun yöntem: {best.name}"

        return result


# Global instance
discovery_engine_with_search = DiscoveryEngineWithSearch()