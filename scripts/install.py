#!/usr/bin/env python3
"""
EMOG CLI Installer
Installs EMOG agent with permission prompts
"""
import os
import sys
import json
import subprocess
import platform
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
import argparse
import getpass


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def print_header(text: str) -> None:
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text:^60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}\n")


def print_success(text: str) -> None:
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_error(text: str) -> None:
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_warning(text: str) -> None:
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def print_info(text: str) -> None:
    print(f"{Colors.CYAN}ℹ {text}{Colors.END}")


def prompt_yes_no(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        response = input(f"{question}{suffix}: ").strip().lower()
        if not response:
            return default
        if response in ('y', 'yes'):
            return True
        if response in ('n', 'no'):
            return False
        print("Please answer yes or no.")


def prompt_choice(question: str, choices: List[str], default: int = 0) -> int:
    print(f"\n{question}")
    for i, choice in enumerate(choices):
        marker = "→" if i == 0 else " "
        print(f"  {marker} {i+1}. {choice}")

    while True:
        response = input(f"\nSelect [1-{len(choices)}] (default: {default+1}): ").strip()
        if not response:
            return default
        try:
            idx = int(response) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        print(f"Please enter a number between 1 and {len(choices)}.")


class CLIInstaller:
    def __init__(self):
        self.system = platform.system().lower()
        self.home = Path.home()
        self.install_dir = self.home / ".emog"
        self.bin_dir = self.home / ".local" / "bin"
        self.config_dir = self.home / ".emog"
        self.permissions = {}

    def run(self, args: argparse.Namespace) -> int:
        if args.uninstall:
            return self.uninstall()

        print_header("EMOG Agent Installer")
        print_info(f"Platform: {platform.system()} {platform.release()}")
        print_info(f"Install directory: {self.install_dir}")

        if not args.yes:
            if not prompt_yes_no("Continue with installation?"):
                print_warning("Installation cancelled.")
                return 1

        return self.install(args)

    def install(self, args: argparse.Namespace) -> int:
        try:
            self.create_directories()
            self.install_python_dependencies()
            self.install_system_dependencies()
            self.setup_permissions(args)
            self.create_config()
            self.install_cli()
            self.create_desktop_entry()
            self.run_post_install()

            print_header("Installation Complete!")
            print_success("EMOG Agent installed successfully!")
            print_info("\nNext steps:")
            print("  1. Run 'emog' to start the agent")
            print("  2. Run 'emog --help' for options")
            print("  3. Configure API keys in ~/.emog/config.json")
            print("  4. Run 'emog permissions' to manage permissions")
            return 0
        except Exception as e:
            print_error(f"Installation failed: {e}")
            return 1

    def create_directories(self) -> None:
        dirs = [
            self.install_dir,
            self.install_dir / "agents",
            self.install_dir / "data",
            self.install_dir / "logs",
            self.install_dir / "workspace",
            self.install_dir / "downloads",
            self.config_dir,
            self.config_dir / "permissions",
            self.bin_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
        print_success("Created directories")

    def install_python_dependencies(self) -> None:
        print_info("Installing Python dependencies...")

        # Core dependencies
        deps = [
            "playwright>=1.40",
            "pyautogui>=0.9.54",
            "pillow>=10.0",
            "aiohttp>=3.9",
            "beautifulsoup4>=4.12",
            "lxml>=4.9",
            "pyyaml>=6.0",
            "python-dotenv>=1.0",
            "click>=8.1",
            "rich>=13.0",
            "requests>=2.31",
            "semantic-scholar-api>=0.1",
            "arxiv>=2.1",
            "pymed>=1.1",
            "crossrefapi>=1.1",
            "github3.py>=1.3",
            "pyjwt>=2.8",
            "cryptography>=41.0",
        ]

        # System-specific
        if self.system == "windows":
            deps.extend(["pywin32>=306", "wmi>=1.5"])
        elif self.system == "darwin":
            deps.extend(["pyobjc-framework-Quartz>=10.0", "pyobjc-framework-Cocoa>=10.0"])
        else:
            deps.extend(["python3-xlib>=0.33", "xdotool"])

        # Install in batches to avoid memory issues
        batch_size = 5
        for i in range(0, len(deps), batch_size):
            batch = deps[i:i+batch_size]
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", *batch],
                             check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                print_warning(f"Some packages failed to install: {e}")

        # Install Playwright browsers
        try:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                         check=True, capture_output=True)
        except subprocess.CalledProcessError:
            print_warning("Playwright browser install failed - run manually later")

        print_success("Python dependencies installed")

    def install_system_dependencies(self) -> None:
        print_info("Checking system dependencies...")

        if self.system == "linux":
            self._install_linux_deps()
        elif self.system == "darwin":
            self._install_macos_deps()
        elif self.system == "windows":
            self._install_windows_deps()

        print_success("System dependencies checked")

    def _install_linux_deps(self) -> None:
        # Try to detect package manager
        pkg_managers = [
            ("apt", ["apt-get", "update", "&&", "apt-get", "install", "-y",
                    "libnss3", "libatk-bridge2.0-0", "libdrm2", "libxkbcommon0",
                    "libxcomposite1", "libxdamage1", "libxrandr2", "libgbm1",
                    "libasound2", "xdotool", "scrot", "wmctrl"]),
            ("dnf", ["dnf", "install", "-y", "nss", "at-spi2-atk", "libdrm",
                    "libxkbcommon", "libxcomposite", "libxdamage", "libxrandr",
                    "mesa-libgbm", "alsa-lib", "xdotool", "scrot", "wmctrl"]),
            ("pacman", ["pacman", "-S", "--noconfirm", "nss", "at-spi2-atk",
                       "libdrm", "libxkbcommon", "libxcomposite", "libxdamage",
                       "libxrandr", "mesa", "alsa-lib", "xdotool", "scrot", "wmctrl"]),
        ]

        for name, cmd in pkg_managers:
            if shutil.which(name):
                try:
                    subprocess.run(cmd, shell=True, check=True)
                    return
                except subprocess.CalledProcessError:
                    pass
        print_warning("Could not auto-install system dependencies")

    def _install_macos_deps(self) -> None:
        if shutil.which("brew"):
            try:
                subprocess.run(["brew", "install", "xdotool", "scrot"], check=True)
            except subprocess.CalledProcessError:
                print_warning("Could not install macOS dependencies via Homebrew")

    def _install_windows_deps(self) -> None:
        # Install Tesseract OCR via PowerShell script for image text recognition
        ps_script = Path(__file__).resolve().parent / "install_tesseract.ps1"
        if ps_script.exists():
            try:
                subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)],
                    check=False,
                )
            except Exception as exc:
                print_warning(f"Tesseract OCR installation script failed: {exc}")
        else:
            print_warning("Tesseract OCR installer script not found")

    def setup_permissions(self, args: argparse.Namespace) -> None:
        print_header("Permission Configuration")

        print("""
EMOG Agent needs permissions to perform various tasks. Choose a profile:

1. Research Only - Web search, academic papers, APIs only
   ✓ Web search, academic papers, APIs
   ✗ No local machine access

2. Automation Assistant (Recommended) - Browser, files, screen, mouse/keyboard
   ✓ Browser automation, file operations, screen capture
   ✓ Mouse/keyboard control, clipboard
   ✗ No shell commands

3. Full Assistant - Complete computer control
   ✓ Everything including shell commands
   ⚠ Full system access
        """)

        if args.profile:
            profile = args.profile
        else:
            choice = prompt_choice("Select permission profile:", 
                                 ["Research Only", "Automation Assistant", "Full Assistant"],
                                 default=1)
            profiles = ["research_only", "automation_assistant", "full_assistant"]
            profile = profiles[choice]

        self.permissions["profile"] = profile
        print_success(f"Selected profile: {profile}")

        # Additional permissions
        if profile != "research_only":
            if prompt_yes_no("Allow screen recording for demos/debugging?", default=False):
                self.permissions["screen_recording"] = True

            if prompt_yes_no("Allow access to specific directories?", default=True):
                paths = []
                while True:
                    path = input("  Path to allow (empty to finish): ").strip()
                    if not path:
                        break
                    paths.append(path)
                if paths:
                    self.permissions["allowed_paths"] = paths

    def create_config(self) -> None:
        config = {
            "version": "0.1.0",
            "install_dir": str(self.install_dir),
            "config_dir": str(self.config_dir),
            "profile": self.permissions.get("profile", "automation_assistant"),
            "permissions": {
                "computer_control": self.permissions.get("profile") != "research_only",
                "screen_recording": self.permissions.get("screen_recording", False),
                "allowed_paths": self.permissions.get("allowed_paths", [
                    str(self.install_dir / "data"),
                    str(self.install_dir / "downloads"),
                    str(self.install_dir / "workspace"),
                ]),
            },
            "browser": {
                "headless": False,
                "default_timeout": 30000,
            },
            "research": {
                "default_max_results": 10,
                "sources": ["web", "semantic_scholar", "arxiv"],
            },
            "api_keys": {
                "openai": "",
                "anthropic": "",
                "google": "",
                "github": "",
                "semantic_scholar": "",
            },
            "logging": {
                "level": "INFO",
                "file": str(self.install_dir / "logs" / "emog.log"),
            }
        }

        config_file = self.config_dir / "config.json"
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)

        print_success(f"Config saved to {config_file}")

    def install_cli(self) -> None:
        cli_script = self.bin_dir / "emog"
        cli_content = f'''#!/usr/bin/env python3
"""
EMOG CLI Entry Point
"""
import sys
import os

# Add install directory to path
sys.path.insert(0, r"{self.install_dir}")

from agent_orchestrator.cli import main

if __name__ == "__main__":
    main()
'''

        with open(cli_script, 'w') as f:
            f.write(cli_content)

        os.chmod(cli_script, 0o755)

        # Create wrapper script for Windows
        if self.system == "windows":
            wrapper = self.bin_dir / "emog.bat"
            wrapper.write_text(f'@echo off\npython "{cli_script}" %*\n')

        # Add to PATH if needed
        self._ensure_path()

        print_success(f"CLI installed to {self.bin_dir}/emog")

    def _ensure_path(self) -> None:
        path_str = str(self.bin_dir)
        current_path = os.environ.get("PATH", "")

        if path_str not in current_path:
            print_warning(f"Add {path_str} to your PATH to use 'emog' command")
            print_info("  Bash/Zsh: export PATH=\"$HOME/.local/bin:$PATH\"")
            print_info("  Fish: set -U fish_user_paths $HOME/.local/bin $fish_user_paths")
            print_info("  Windows: Add to System Environment Variables")

    def create_desktop_entry(self) -> None:
        if self.system == "linux":
            desktop_dir = self.home / ".local" / "share" / "applications"
            desktop_dir.mkdir(parents=True, exist_ok=True)

            desktop_file = desktop_dir / "emog.desktop"
            desktop_file.write_text(f"""[Desktop Entry]
Name=EMOG Agent
Comment=AI Research & Automation Assistant
Exec={self.bin_dir}/emog
Icon={self.install_dir}/icon.png
Terminal=false
Type=Application
Categories=Development;Utility;
""")
            print_success("Desktop entry created")

    def run_post_install(self) -> None:
        print_info("Running post-install setup...")

        # Create example config
        example_config = self.config_dir / "config.example.json"
        if not example_config.exists():
            shutil.copy(self.config_dir / "config.json", example_config)

        # Create README
        readme = self.install_dir / "README.md"
        readme.write_text("""# EMOG Agent

AI Research & Automation Assistant

## Quick Start

```bash
emog                    # Start the agent
emog chat              # Interactive chat mode
emog research "topic"  # Deep research on topic
emog automate "task"   # Automate a task
emog permissions       # Manage permissions
```

## Configuration

Edit `~/.emog/config.json` to configure:
- API keys (OpenAI, Anthropic, GitHub, etc.)
- Browser settings
- Research sources
- Logging

## Permissions

Run `emog permissions` to manage what the agent can access.

## Project Structure

```
~/.emog/
├── config.json          # Main configuration
├── config.example.json  # Example configuration
├── data/                # Research data
├── downloads/           # Downloaded files
├── workspace/           # Working directory
├── logs/                # Log files
├── agents/              # Agent modules
└── permissions/         # Permission configs
```

## Documentation

See https://github.com/yourusername/emog for full documentation.
""")

        print_success("Post-install setup complete")

    def uninstall(self) -> int:
        print_header("EMOG Uninstaller")

        if not prompt_yes_no("This will remove EMOG completely. Continue?", default=False):
            return 0

        # Remove CLI
        cli = self.bin_dir / "emog"
        if cli.exists():
            cli.unlink()
        if self.system == "windows":
            (self.bin_dir / "emog.bat").unlink(missing_ok=True)

        # Remove desktop entry
        if self.system == "linux":
            (self.home / ".local" / "share" / "applications" / "emog.desktop").unlink(missing_ok=True)

        # Ask about data
        if prompt_yes_no("Remove all data and config?", default=False):
            shutil.rmtree(self.install_dir, ignore_errors=True)
            shutil.rmtree(self.config_dir, ignore_errors=True)
            print_success("All data removed")
        else:
            print_info(f"Data preserved in {self.install_dir} and {self.config_dir}")

        print_success("EMOG uninstalled")
        return 0


def main():
    parser = argparse.ArgumentParser(description="EMOG Agent Installer")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall EMOG")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmations")
    parser.add_argument("--profile", choices=["research_only", "automation_assistant", "full_assistant"],
                       help="Permission profile")
    parser.add_argument("--install-dir", help="Custom install directory")

    args = parser.parse_args()

    installer = CLIInstaller()
    if args.install_dir:
        installer.install_dir = Path(args.install_dir)
        installer.config_dir = installer.install_dir
        installer.bin_dir = Path.home() / ".local" / "bin"

    return installer.run(args)


if __name__ == "__main__":
    sys.exit(main())