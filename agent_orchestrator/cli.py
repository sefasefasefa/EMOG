"""
EMOG CLI Module
"""
import asyncio
import json
import os
import subprocess
import sys
import platform
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.markdown import Markdown

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_orchestrator.core.orchestrator import AgentOrchestrator, Task, TaskPriority
from agent_orchestrator.core.permissions import get_permission_manager, PermissionCategory, PermissionLevel
from agent_orchestrator.agents.research_agent import ResearchAgent
from agent_orchestrator.agents.computer_control_agent import ComputerControlAgent
from agent_orchestrator.core.agent import AgentConfig, AgentCapability

console = Console()


def ensure_emog_dependencies():
    """EMOG CLI başlamadan önce gerekli görüntü/video araçlarını kontrol eder,
    eksikse otomatik olarak kurar (ffmpeg, ffprobe, tesseract + Python paketleri)."""
    setup_script = Path(__file__).resolve().parent.parent / "setup_dependencies.py"

    # Hızlı yol: kritik sistem araçları zaten varsa kurulumu atla
    critical_tools = ("ffmpeg", "ffprobe", "tesseract")
    if all(shutil.which(tool) for tool in critical_tools):
        return

    if not setup_script.exists():
        return

    print("=" * 60)
    print("EMOG başlamadan önce bağımlılıklar kontrol ediliyor/kuruluyor...")
    print("=" * 60)
    try:
        subprocess.run([sys.executable, str(setup_script)], check=False)
    except Exception:
        pass
    print("=" * 60)


def safe_print(text: str) -> str:
    """Sanitize text for Windows console (CP1252)"""
    return text.encode('cp1252', errors='replace').decode('cp1252')


def load_config() -> Dict[str, Any]:
    config_path = Path.home() / ".emog" / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_config(config: Dict[str, Any]) -> None:
    config_path = Path.home() / ".emog" / "config.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)


def create_orchestrator(config: Dict[str, Any]) -> AgentOrchestrator:
    orchestrator = AgentOrchestrator()

    # Create research agent
    research_config = AgentConfig(
        name="ResearchAgent",
        description="Web search, academic papers, GitHub, API integration",
        capabilities=[
            AgentCapability.WEB_SEARCH,
            AgentCapability.ACADEMIC_SEARCH,
            AgentCapability.GITHUB_OPS,
            AgentCapability.API_CALLS,
        ],
        config=config.get("research", {}),
    )
    research_agent = ResearchAgent(research_config)
    orchestrator.register_agent(research_agent)

    # Create computer control agent if permitted
    permissions = config.get("permissions", {})
    if permissions.get("computer_control", False):
        cc_config = AgentConfig(
            name="ComputerControlAgent",
            description="Mouse, keyboard, browser automation, file ops",
            capabilities=[
                AgentCapability.MOUSE_CONTROL,
                AgentCapability.KEYBOARD_CONTROL,
                AgentCapability.SCREEN_CAPTURE,
                AgentCapability.BROWSER_AUTOMATION,
                AgentCapability.FILE_OPERATIONS,
                AgentCapability.CODE_EXECUTION,
            ],
            config=config.get("browser", {}),
            permissions=config.get("permissions", {}),
        )
        cc_agent = ComputerControlAgent(cc_config)
        orchestrator.register_agent(cc_agent)

    return orchestrator


@click.group()
@click.version_option(version="0.1.0")
@click.option('--config', '-c', type=click.Path(exists=True), help='Config file path')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.pass_context
def cli(ctx: click.Context, config: Optional[str], verbose: bool):
    """EMOG - AI Research & Automation Assistant"""
    # EMOG başlatılmadan önce gerekli araçları hazırla
    ensure_emog_dependencies()
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['config_path'] = config


@cli.command()
@click.option('--headless', is_flag=True, help='Run in headless mode')
@click.pass_context
def start(ctx: click.Context, headless: bool):
    """Start the EMOG agent server"""
    config = load_config()
    if headless:
        config.setdefault('browser', {})['headless'] = True

    orchestrator = create_orchestrator(config)

    async def run():
        await orchestrator.start_worker()
        console.print("[green]EMOG Agent started[/green]")
        console.print("Press Ctrl+C to stop")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping...[/yellow]")
            await orchestrator.stop_worker()

    asyncio.run(run())


@cli.command()
@click.argument('message', required=False)
@click.option('--agent', '-a', help='Specific agent to use')
@click.option('--stream', '-s', is_flag=True, help='Stream response')
@click.pass_context
def chat(ctx: click.Context, message: Optional[str], agent: Optional[str], stream: bool):
    """Interactive chat with EMOG"""
    config = load_config()
    orchestrator = create_orchestrator(config)

    console.print(Panel.fit("[bold cyan]EMOG Chat[/bold cyan]", border_style="cyan"))

    if message:
        # Single message mode
        asyncio.run(send_message(orchestrator, message, agent))
    else:
        # Interactive mode
        console.print("[dim]Type 'exit' or 'quit' to leave[/dim]\n")
        asyncio.run(interactive_chat(orchestrator))


async def send_message(orchestrator: AgentOrchestrator, message: str, agent_name: Optional[str]):
    task = Task(
        name="chat",
        description=message,
        payload={"type": "chat", "message": message},
    )

    if agent_name:
        agent = orchestrator.get_agent(agent_name)
        if not agent:
            console.print(f"[red]Agent not found: {agent_name}[/red]")
            return
        result = await orchestrator.submit_task_to_agent(task, agent.config.agent_id)
    else:
        task_id = await orchestrator.submit_task(task, TaskPriority.HIGH)
        # Wait for completion
        while True:
            await asyncio.sleep(0.5)
            status = orchestrator.get_task_status(task.task_id)
            if status and status['status'] in ('completed', 'failed'):
                break

        if task.status == 'completed':
            console.print(Markdown(task.result.get('response', 'No response')))
        else:
            console.print(f"[red]Error: {task.error}[/red]")


async def interactive_chat(orchestrator: AgentOrchestrator):
    await orchestrator.start_worker()

    while True:
        try:
            message = Prompt.ask("\n[bold cyan]You[/bold cyan]")
            if message.lower() in ('exit', 'quit', 'q'):
                break

            if not message.strip():
                continue

            task = Task(
                name="chat",
                description=message,
                payload={"type": "chat", "message": message},
            )

            task_id = await orchestrator.submit_task(task, TaskPriority.HIGH)

            with console.status("[bold green]Thinking...[/bold green]"):
                while True:
                    await asyncio.sleep(0.5)
                    status = orchestrator.get_task_status(task_id)
                    if status and status['status'] in ('completed', 'failed'):
                        break

            if status['status'] == 'completed':
                console.print(f"[green]EMOG:[/green] {status['result'].get('response', 'No response')}")
            else:
                console.print(f"[red]Error: {status['error']}[/red]")

        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    await orchestrator.stop_worker()
    console.print("\n[yellow]Goodbye![/yellow]")


@cli.command()
@click.argument('topic')
@click.option('--depth', '-d', default=3, help='Research depth (1-5)')
@click.option('--sources', '-s', multiple=True, help='Sources to search')
@click.option('--output', '-o', type=click.Path(), help='Output file')
@click.pass_context
def research(ctx: click.Context, topic: str, depth: int, sources: tuple, output: Optional[str]):
    """Deep research on a topic"""
    config = load_config()
    orchestrator = create_orchestrator(config)

    console.print(f"[bold cyan]Researching:[/bold cyan] {topic}")
    console.print(f"[dim]Depth: {depth} | Sources: {', '.join(sources) or 'default'}[/dim]\n")

    task = Task(
        name=f"research: {topic}",
        description=f"Deep research on: {topic}",
        payload={
            "type": "deep_research",
            "topic": topic,
            "depth": depth,
            "sources": list(sources) if sources else ["web", "academic"],
        },
    )

    async def run():
        await orchestrator.start_worker()
        task_id = await orchestrator.submit_task(task, TaskPriority.HIGH)

        # Use simple text output for Windows compatibility
        console.print("[cyan]Researching...[/cyan]")

        while True:
            await asyncio.sleep(1)
            status = orchestrator.get_task_status(task.task_id)
            if status and status['status'] in ('completed', 'failed'):
                break

        if status['status'] == 'completed':
            result = status['result']
            console.print(f"\n[green]Research complete![/green]")

            # Display results
            if result.get('web_results'):
                console.print(f"\n[bold]Web Results ({len(result['web_results'])}):[/bold]")
                for i, r in enumerate(result['web_results'][:5], 1):
                    console.print(f"  {i}. {safe_print(r['title'])}")
                    console.print(f"     [dim]{safe_print(r['snippet'][:100])}...[/dim]")

            if result.get('academic_papers'):
                console.print(f"\n[bold]Academic Papers ({len(result['academic_papers'])}):[/bold]")
                for i, p in enumerate(result['academic_papers'][:5], 1):
                    console.print(f"  {i}. {safe_print(p['title'])}")
                    if p.get('authors'):
                        console.print(f"     [dim]Authors: {', '.join(p['authors'][:3])}[/dim]")

            if result.get('key_concepts'):
                console.print(f"\n[bold]Key Concepts:[/bold] {', '.join(result['key_concepts'][:10])}")

                if output:
                    with open(output, 'w') as f:
                        json.dump(result, f, indent=2)
                    console.print(f"\n[green]Results saved to {output}[/green]")

        await orchestrator.stop_worker()

    asyncio.run(run())


@cli.command()
@click.argument('task_description')
@click.option('--agent', '-a', help='Specific agent')
@click.option('--dry-run', is_flag=True, help='Show plan without executing')
@click.pass_context
def automate(ctx: click.Context, task_description: str, agent: Optional[str], dry_run: bool):
    """Automate a task on your computer"""
    config = load_config()
    orchestrator = create_orchestrator(config)

    console.print(f"[bold cyan]Automation Task:[/bold cyan] {task_description}")

    task = Task(
        name="automation",
        description=task_description,
        payload={
            "type": "automation",
            "instruction": task_description,
            "dry_run": dry_run,
        },
    )

    async def run():
        await orchestrator.start_worker()

        if dry_run:
            console.print("[yellow]DRY RUN - Showing plan only[/yellow]")

        task_id = await orchestrator.submit_task(task, TaskPriority.HIGH)

        console.print("[cyan]Executing...[/cyan]")

        while True:
            await asyncio.sleep(0.5)
            status = orchestrator.get_task_status(task.task_id)
            if status and status['status'] in ('completed', 'failed'):
                break

        if status['status'] == 'completed':
            result = status['result']
            console.print(f"\n[green]Automation complete![/green]")
            if result.get('steps'):
                for i, step in enumerate(result['steps'], 1):
                    console.print(f"  {i}. {step}")
        else:
            console.print(f"[red]Automation failed: {task.error}[/red]")

        await orchestrator.stop_worker()

    asyncio.run(run())


@cli.group()
def permissions():
    """Manage agent permissions"""
    pass


@permissions.command("list")
def permissions_list():
    """List available permission profiles"""
    pm = get_permission_manager()
    profiles = pm.list_profiles()

    table = Table(title="Permission Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="white")
    table.add_column("Default", style="green")

    for p in profiles:
        table.add_row(p['name'], p['description'], "[OK]" if p['is_default'] else "")

    console.print(table)


@permissions.command("show")
@click.argument('profile')
def permissions_show(profile: str):
    """Show details of a permission profile"""
    pm = get_permission_manager()
    details = pm.get_profile(profile)

    if not details:
        console.print(f"[red]Profile not found: {profile}[/red]")
        return

    console.print(Panel(f"[bold]{details['name']}[/bold]\n{details['description']}", title="Profile"))

    table = Table(title="Permissions")
    table.add_column("Category", style="cyan")
    table.add_column("Level", style="green")
    table.add_column("Paths", style="yellow")

    for cat, perm in details.get('permissions', {}).items():
        paths = ', '.join(perm.get('paths', [])) or 'N/A'
        table.add_row(cat, perm['level'], paths)

    console.print(table)


@permissions.command("grant")
@click.argument('agent_id')
@click.argument('profile')
@click.option('--override', '-o', multiple=True, help='Override specific permissions (cat=level)')
def permissions_grant(agent_id: str, profile: str, override: tuple):
    """Grant permissions to an agent"""
    pm = get_permission_manager()
    overrides = {}
    for o in override:
        cat, level = o.split('=', 1)
        overrides[cat] = {"level": level}

    if pm.grant_permissions(agent_id, profile, overrides):
        console.print(f"[green]Granted {profile} permissions to {agent_id}[/green]")
    else:
        console.print(f"[red]Failed to grant permissions[/red]")


@permissions.command("revoke")
@click.argument('agent_id')
def permissions_revoke(agent_id: str):
    """Revoke all permissions for an agent"""
    pm = get_permission_manager()
    if pm.revoke_permissions(agent_id):
        console.print(f"[green]Revoked permissions for {agent_id}[/green]")
    else:
        console.print(f"[red]No permissions found for {agent_id}[/red]")


@permissions.command("show-agent")
@click.argument('agent_id')
def permissions_show_agent(agent_id: str):
    """Show permissions for an agent"""
    pm = get_permission_manager()
    perms = pm.get_agent_permissions(agent_id)

    if not perms:
        console.print(f"[yellow]No permissions granted to {agent_id}[/yellow]")
        return

    table = Table(title=f"Permissions for {agent_id}")
    table.add_column("Category", style="cyan")
    table.add_column("Level", style="green")
    table.add_column("Paths", style="yellow")

    for cat, perm in perms.items():
        paths = ', '.join(perm.get('paths', [])) or 'N/A'
        table.add_row(cat, perm.get('level', 'N/A'), paths)

    console.print(table)


@cli.command()
@click.pass_context
def config(ctx: click.Context):
    """Manage configuration"""
    config = load_config()

    table = Table(title="Current Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")

    for key, value in config.items():
        if key == 'api_keys':
            table.add_row(key, "[hidden]")
        else:
            table.add_row(key, str(value)[:100])

    console.print(table)

    if Confirm.ask("\nEdit configuration?"):
        # Interactive config editing
        new_config = config.copy()
        new_config['profile'] = Prompt.ask("Permission profile", choices=["research_only", "automation_assistant", "full_assistant"], default=config.get('profile', 'automation_assistant'))

        if Confirm.ask("Update API keys?"):
            for key in ['openai', 'anthropic', 'google', 'github', 'semantic_scholar']:
                new_config.setdefault('api_keys', {})[key] = Prompt.ask(f"  {key}", default=config.get('api_keys', {}).get(key, ''), password=True)

        save_config(new_config)
        console.print("[green]Configuration saved![/green]")


@cli.command()
@click.pass_context
def doctor(ctx: click.Context):
    """Run system diagnostics"""
    console.print(Panel("[bold]EMOG System Diagnostics[/bold]", border_style="blue"))

    checks = [
        ("Python", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        ("Platform", f"{platform.system()} {platform.release()}"),
        ("Install Dir", str(Path.home() / ".emog")),
        ("Config", "[OK]" if (Path.home() / ".emog" / "config.json").exists() else "[MISSING]"),
    ]

    table = Table()
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="green")

    for name, status in checks:
        table.add_row(name, status)

    console.print(table)

    # Check dependencies
    deps = [
        "playwright", "pyautogui", "aiohttp", "bs4", "lxml",
        "pyyaml", "rich", "click", "requests", "semantic_scholar",
        "arxiv", "pymed", "crossrefapi", "github3"
    ]

    console.print("\n[bold]Dependencies:[/bold]")
    for dep in deps:
        try:
            __import__(dep.replace('-', '_'))
            console.print(f"  [green][OK][/green] {dep}")
        except ImportError:
            console.print(f"  [red][MISSING][/red] {dep}")

    # Check Playwright
    try:
        from playwright.async_api import async_playwright
        console.print("  [green][OK][/green] playwright (browser)")
    except ImportError:
        console.print("  [red][MISSING][/red] playwright")

    # Check system tools
    tools = [("xdotool", "Linux"), ("scrot", "Linux"), ("screencapture", "Darwin")]
    for tool, sys_name in tools:
        if platform.system() == sys_name or sys_name == "All":
            if shutil.which(tool):
                console.print(f"  [green][OK][/green] {tool}")
            else:
                console.print(f"  [yellow][OPTIONAL][/yellow] {tool}")


@cli.command()
@click.pass_context
def update(ctx: click.Context):
    """Update EMOG to latest version"""
    console.print("[yellow]Updating EMOG...[/yellow]")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "emog-agent"],
                      check=True)
        console.print("[green]Update complete![/green]")
    except subprocess.CalledProcessError:
        console.print("[red]Update failed[/red]")
        sys.exit(1)


@cli.command()
@click.pass_context
def logs(ctx: click.Context):
    """View agent logs"""
    log_file = Path.home() / ".emog" / "logs" / "emog.log"
    if log_file.exists():
        console.print(log_file.read_text()[-5000:])
    else:
        console.print("[yellow]No log file found[/yellow]")


if __name__ == "__main__":
    cli()