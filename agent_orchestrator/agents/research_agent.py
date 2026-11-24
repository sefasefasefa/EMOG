"""
Research Agent - Web search, academic papers, GitHub, API integration
"""
import asyncio
import aiohttp
import json
import re
from typing import Dict, List, Any, Optional
from urllib.parse import quote_plus, urljoin
from datetime import datetime
import logging

from ..core.agent import BaseAgent, AgentConfig, Task, AgentCapability, AgentStatus

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.session: Optional[aiohttp.ClientSession] = None
        self.search_engines = {
            "duckduckgo": "https://html.duckduckgo.com/html/",
            "bing": "https://www.bing.com/search",
            "google": "https://www.google.com/search",
        }
        self.academic_apis = {
            "semantic_scholar": "https://api.semanticscholar.org/graph/v1",
            "arxiv": "http://export.arxiv.org/api/query",
            "pubmed": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
            "crossref": "https://api.crossref.org",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "EMOG-ResearchAgent/1.0"}
            )
        return self.session

    async def execute(self, task: Task) -> Dict[str, Any]:
        task_type = task.payload.get("type", "web_search")

        handlers = {
            "web_search": self._web_search,
            "academic_search": self._academic_search,
            "github_search": self._github_search,
            "api_call": self._api_call,
            "deep_research": self._deep_research,
            "summarize": self._summarize_content,
        }

        handler = handlers.get(task_type)
        if not handler:
            raise ValueError(f"Unknown task type: {task_type}")

        return await handler(task)

    async def validate_task(self, task: Task) -> bool:
        required_caps = task.payload.get("required_capabilities", [])
        return all(self.has_capability(cap) for cap in required_caps)

    async def _web_search(self, task: Task) -> Dict[str, Any]:
        query = task.payload.get("query", "")
        max_results = task.payload.get("max_results", 10)
        engine = task.payload.get("engine", "auto")

        engines_to_try = ["duckduckgo", "bing"] if engine == "auto" else [engine]

        for eng in engines_to_try:
            if eng == "duckduckgo":
                result = await self._duckduckgo_search(query, max_results)
            elif eng == "bing":
                result = await self._bing_search(query, max_results)
            elif eng == "google":
                result = await self._google_search(query, max_results)
            else:
                continue

            if result.get("results") and not result.get("error"):
                return result
            logger.warning(f"{eng} search failed: {result.get('error')}")

        return {"results": [], "error": "All search engines failed"}

    async def _duckduckgo_search(self, query: str, max_results: int) -> Dict[str, Any]:
        session = await self._get_session()
        params = {"q": query, "kl": "us-en"}

        try:
            async with self.session.get(self.search_engines["duckduckgo"], params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html = await resp.text()
        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return {"results": [], "error": str(e)}

        results = self._parse_duckduckgo_html(html, max_results)
        return {"query": query, "results": results, "count": len(results)}

    def _parse_duckduckgo_html(self, html: str, max_results: int) -> List[Dict[str, Any]]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for result in soup.select(".result")[:max_results]:
            title_elem = result.select_one(".result__title")
            snippet_elem = result.select_one(".result__snippet")
            url_elem = result.select_one(".result__url")

            if title_elem:
                results.append({
                    "title": title_elem.get_text(strip=True),
                    "snippet": snippet_elem.get_text(strip=True) if snippet_elem else "",
                    "url": url_elem.get_text(strip=True) if url_elem else "",
                })

        return results

    async def _bing_search(self, query: str, max_results: int) -> Dict[str, Any]:
        session = await self._get_session()
        params = {"q": query, "count": max_results, "setlang": "en-us"}

        try:
            async with self.session.get(self.search_engines["bing"], params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html = await resp.text()
        except Exception as e:
            logger.error(f"Bing search failed: {e}")
            return {"results": [], "error": str(e)}

        return self._parse_bing_html(html, max_results, query)

    def _parse_bing_html(self, html: str, max_results: int, query: str = "") -> Dict[str, Any]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for result in soup.select(".b_algo")[:max_results]:
            title_elem = result.select_one("h2 a")
            snippet_elem = result.select_one(".b_caption p")
            url_elem = result.select_one("cite")

            if title_elem:
                results.append({
                    "title": title_elem.get_text(strip=True),
                    "snippet": snippet_elem.get_text(strip=True) if snippet_elem else "",
                    "url": url_elem.get_text(strip=True) if url_elem else "",
                })

        return {"query": query, "results": results, "count": len(results)}

    async def _academic_search(self, task: Task) -> Dict[str, Any]:
        query = task.payload.get("query", "")
        source = task.payload.get("source", "semantic_scholar")
        max_results = task.payload.get("max_results", 20)

        if source == "semantic_scholar":
            return await self._semantic_scholar_search(query, max_results)
        elif source == "arxiv":
            return await self._arxiv_search(query, max_results)
        elif source == "pubmed":
            return await self._pubmed_search(query, max_results)
        elif source == "crossref":
            return await self._crossref_search(query, max_results)

        return {"results": [], "error": f"Unknown source: {source}"}

    async def _semantic_scholar_search(self, query: str, max_results: int) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.academic_apis['semantic_scholar']}/paper/search"
        params = {"query": query, "limit": max_results, "fields": "title,authors,year,venue,abstract,citationCount,url"}

        try:
            async with self.session.get(url, params=params) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"Semantic Scholar search failed: {e}")
            return {"results": [], "error": str(e)}

        results = []
        for paper in data.get("data", []):
            results.append({
                "title": paper.get("title"),
                "authors": [a.get("name") for a in paper.get("authors", [])],
                "year": paper.get("year"),
                "venue": paper.get("venue"),
                "abstract": paper.get("abstract"),
                "citations": paper.get("citationCount"),
                "url": paper.get("url"),
            })

        return {"query": query, "results": results, "count": len(results)}

    async def _arxiv_search(self, query: str, max_results: int) -> Dict[str, Any]:
        session = await self._get_session()
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        try:
            async with self.session.get(self.academic_apis["arxiv"], params=params) as resp:
                xml_text = await resp.text()
        except Exception as e:
            logger.error(f"ArXiv search failed: {e}")
            return {"results": [], "error": str(e)}

        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        results = []
        for entry in root.findall("atom:entry", ns)[:20]:
            title = entry.find("atom:title", ns)
            summary = entry.find("atom:summary", ns)
            published = entry.find("atom:published", ns)
            link = entry.find("atom:id", ns)

            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.find("atom:name", ns)
                if name is not None:
                    authors.append(name.text)

            results.append({
                "title": title.text if title is not None else "",
                "authors": authors,
                "published": published.text if published is not None else "",
                "abstract": summary.text if summary is not None else "",
                "url": link.text if link is not None else "",
            })

        return {"query": query, "results": results, "count": len(results)}

    async def _pubmed_search(self, query: str, max_results: int) -> Dict[str, Any]:
        session = await self._get_session()
        # ESearch
        esearch_url = f"{self.academic_apis['pubmed']}/esearch.fcgi"
        params = {"db": "pubmed", "term": query, "retmax": max_results, "retmode": "json"}

        try:
            async with self.session.get(esearch_url, params=params) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"PubMed search failed: {e}")
            return {"results": [], "error": str(e)}

        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return {"query": query, "results": [], "count": 0}

        # EFetch
        efetch_url = f"{self.academic_apis['pubmed']}/efetch.fcgi"
        params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}

        try:
            async with self.session.get(efetch_url, params=params) as resp:
                xml_text = await resp.text()
        except Exception as e:
            logger.error(f"PubMed fetch failed: {e}")
            return {"results": [], "error": str(e)}

        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)

        results = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.find(".//PMID")
            title = article.find(".//ArticleTitle")
            abstract = article.find(".//Abstract/AbstractText")
            journal = article.find(".//Journal/Title")
            pub_date = article.find(".//PubDate/Year")

            results.append({
                "pmid": pmid.text if pmid is not None else "",
                "title": title.text if title is not None else "",
                "abstract": abstract.text if abstract is not None else "",
                "journal": journal.text if journal is not None else "",
                "year": pub_date.text if pub_date is not None else "",
            })

        return {"query": query, "results": results, "count": len(results)}

    async def _crossref_search(self, query: str, max_results: int) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.academic_apis['crossref']}/works"
        params = {"query": query, "rows": max_results, "sort": "relevance"}

        try:
            async with self.session.get(url, params=params) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"Crossref search failed: {e}")
            return {"results": [], "error": str(e)}

        results = []
        for item in data.get("message", {}).get("items", []):
            results.append({
                "title": item.get("title", [""])[0],
                "authors": [f"{a.get('given', '')} {a.get('family', '')}" for a in item.get("author", [])],
                "year": item.get("published-print", {}).get("date-parts", [[None]])[0][0],
                "journal": item.get("container-title", [""])[0],
                "doi": item.get("DOI"),
                "url": f"https://doi.org/{item.get('DOI')}" if item.get("DOI") else "",
            })

        return {"query": query, "results": results, "count": len(results)}

    async def _github_search(self, task: Task) -> Dict[str, Any]:
        query = task.payload.get("query", "")
        type_ = task.payload.get("type", "repositories")
        max_results = task.payload.get("max_results", 30)

        session = await self._get_session()
        url = f"https://api.github.com/search/{type_}"
        params = {"q": query, "per_page": max_results, "sort": "stars", "order": "desc"}

        headers = {"Accept": "application/vnd.github.v3+json"}
        token = self.config.config.get("github_token")
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            async with self.session.get(url, params=params, headers=headers) as resp:
                data = await resp.json()
        except Exception as e:
            logger.error(f"GitHub search failed: {e}")
            return {"results": [], "error": str(e)}

        results = []
        for item in data.get("items", []):
            if type_ == "repositories":
                results.append({
                    "name": item.get("full_name"),
                    "description": item.get("description"),
                    "stars": item.get("stargazers_count"),
                    "forks": item.get("forks_count"),
                    "language": item.get("language"),
                    "url": item.get("html_url"),
                    "owner": item.get("owner", {}).get("login"),
                })
            elif type_ == "code":
                results.append({
                    "repo": item.get("repository", {}).get("full_name"),
                    "path": item.get("path"),
                    "url": item.get("html_url"),
                })

        return {"query": query, "results": results, "count": len(results)}

    async def _api_call(self, task: Task) -> Dict[str, Any]:
        url = task.payload.get("url")
        method = task.payload.get("method", "GET")
        headers = task.payload.get("headers", {})
        data = task.payload.get("data")
        params = task.payload.get("params")

        if not url:
            raise ValueError("URL required for api_call")

        session = await self._get_session()
        try:
            async with self.session.request(method, url, headers=headers, json=data, params=params) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    result = await resp.json()
                else:
                    result = await resp.text()
                return {"status": resp.status, "data": result}
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {"error": str(e)}

    async def _deep_research(self, task: Task) -> Dict[str, Any]:
        topic = task.payload.get("topic", "")
        depth = task.payload.get("depth", 3)
        sources = task.payload.get("sources", ["web", "academic"])

        # Step 1: Initial broad search
        web_results = await self._web_search(Task(payload={"query": topic, "max_results": 10}))

        # Step 2: Academic search
        academic_results = await self._academic_search(Task(payload={"query": topic, "source": "semantic_scholar", "max_results": 10}))

        # Step 3: Extract key concepts and search deeper
        all_findings = {
            "topic": topic,
            "web_results": web_results.get("results", []),
            "academic_papers": academic_results.get("results", []),
            "key_concepts": [],
            "related_topics": [],
            "depth": depth,
        }

        # Extract key concepts from findings
        for paper in academic_results.get("results", [])[:5]:
            if paper.get("abstract"):
                all_findings["key_concepts"].extend(self._extract_concepts(paper["abstract"]))

        # Remove duplicates
        all_findings["key_concepts"] = list(set(all_findings["key_concepts"]))

        return all_findings

    def _extract_concepts(self, text: str) -> List[str]:
        # Simple keyword extraction - could use NLP
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        return [w for w in words if len(w) > 3][:20]

    async def _summarize_content(self, task: Task) -> Dict[str, Any]:
        content = task.payload.get("content", "")
        max_length = task.payload.get("max_length", 500)

        if not content:
            return {"summary": "", "error": "No content provided"}

        # Simple extractive summarization - take first sentences
        sentences = re.split(r'[.!?]+', content)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

        summary = ""
        for sent in sentences:
            if len(summary) + len(sent) < max_length:
                summary += sent + ". "
            else:
                break

        return {
            "original_length": len(content),
            "summary": summary.strip(),
            "sentences_used": len(summary.split(". ")),
        }

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()