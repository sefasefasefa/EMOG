# EMOG Agent Orchestrator

Multi-agent system for research, automation, and computer control.

## Architecture

```
agent_orchestrator/
├── core/
│   ├── agent.py          # Base agent classes
│   ├── orchestrator.py   # Agent orchestration & task queue
│   └── permissions.py    # Permission system
├── agents/
│   ├── research_agent.py      # Web search, academic papers, APIs
│   └── computer_control_agent.py  # Mouse, keyboard, browser, files
├── cli.py                  # Command-line interface
└── requirements.txt
```

## Agents

### ResearchAgent
Capabilities:
- Web search (DuckDuckGo, Google)
- Academic papers (Semantic Scholar, ArXiv, PubMed, Crossref)
- GitHub search (repos, code, issues)
- API calls (REST, GraphQL)
- Deep research (multi-source synthesis)
- Content summarization

### ComputerControlAgent
Capabilities:
- Mouse control (move, click, drag, scroll)
- Keyboard control (type, press, hotkeys)
- Screen capture (full, region, base64)
- Browser automation (Playwright)
- File operations (read, write, list)
- Shell commands

## Quick Start

```python
from agent_orchestrator.core.orchestrator import AgentOrchestrator
from agent_orchestrator.core.agent import AgentConfig, AgentCapability
from agent_orchestrator.agents.research_agent import ResearchAgent
from agent_orchestrator.agents.computer_control_agent import ComputerControlAgent

# Create orchestrator
orchestrator = AgentOrchestrator()

# Create agents
research_config = AgentConfig(
    name="ResearchAgent",
    capabilities=[
        AgentCapability.WEB_SEARCH,
        AgentCapability.ACADEMIC_SEARCH,
        AgentCapability.GITHUB_OPS,
        AgentCapability.API_CALLS,
    ]
)
research_agent = ResearchAgent(research_config)
orchestrator.register_agent(research_agent)

# Start worker
await orchestrator.start_worker()

# Submit task
task = Task(
    name="research",
    description="Research AI safety",
    payload={
        "type": "deep_research",
        "topic": "AI safety alignment",
        "depth": 3,
        "sources": ["web", "academic"]
    }
)
task_id = await orchestrator.submit_task(task, TaskPriority.HIGH)

# Wait for result
while True:
    status = orchestrator.get_task_status(task_id)
    if status and status['status'] in ('completed', 'failed'):
        break
    await asyncio.sleep(1)

# Stop
await orchestrator.stop_worker()
```

## CLI Usage

```bash
# Install
python scripts/install.py

# Start agent server
emog start

# Interactive chat
emog chat

# Research
emog research "quantum computing" --depth 4 --sources web academic

# Automate task
emog automate "open browser and search for python tutorials"

# Permissions
emog permissions list
emog permissions grant my-agent automation_assistant

# Configuration
emog config
emog doctor
```

## Permissions System

Profiles:
- `research_only` - Web search, APIs only
- `automation_assistant` - Browser, files, screen, mouse/keyboard
- `full_assistant` - Complete computer control

```bash
emog permissions grant my-agent automation_assistant
emog permissions show-agent my-agent
```

## Configuration

```json
{
  "profile": "automation_assistant",
  "permissions": {
    "computer_control": true,
    "allowed_paths": ["~/.emog/data", "~/workspace"]
  },
  "browser": {
    "headless": false,
    "default_timeout": 30000
  },
  "research": {
    "default_max_results": 10,
    "sources": ["web", "semantic_scholar", "arxiv"]
  },
  "api_keys": {
    "openai": "",
    "anthropic": "",
    "github": ""
  }
}
```

## Environment Variables

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-..."
export GITHUB_TOKEN="ghp_..."
export SEMANTIC_SCHOLAR_API_KEY="..."
export EMOG_JWT_SECRET="your-secret-key"
```

## Requirements

- Python 3.10+
- Playwright (Chromium)
- System dependencies:
  - Linux: xdotool, scrot, libnss3, libatk-bridge2.0-0
  - macOS: screencapture (built-in)
  - Windows: Built-in support

Install with: `python scripts/install.py`