"""
Computer Control Agent - Mouse, keyboard, browser automation, screen capture
"""
import asyncio
import base64
import json
import os
import tempfile
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging

from ..core.agent import BaseAgent, AgentConfig, Task, AgentCapability, AgentStatus

logger = logging.getLogger(__name__)


class ComputerControlAgent(BaseAgent):
    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.browser = None
        self.page = None
        self.playwright = None
        self._screen_size: Optional[Tuple[int, int]] = None
        self._permissions = config.permissions

    async def execute(self, task: Task) -> Dict[str, Any]:
        task_type = task.payload.get("type", "")

        handlers = {
            "mouse_move": self._mouse_move,
            "mouse_click": self._mouse_click,
            "mouse_drag": self._mouse_drag,
            "mouse_scroll": self._mouse_scroll,
            "keyboard_type": self._keyboard_type,
            "keyboard_press": self._keyboard_press,
            "keyboard_hotkey": self._keyboard_hotkey,
            "screenshot": self._screenshot,
            "screen_region": self._screen_region,
            "browser_open": self._browser_open,
            "browser_navigate": self._browser_navigate,
            "browser_click": self._browser_click,
            "browser_type": self._browser_type,
            "browser_extract": self._browser_extract,
            "browser_execute_js": self._browser_execute_js,
            "browser_wait": self._browser_wait,
            "browser_screenshot": self._browser_screenshot,
            "file_read": self._file_read,
            "file_write": self._file_write,
            "file_list": self._file_list,
            "shell_command": self._shell_command,
        }

        handler = handlers.get(task.payload.get("type"))
        if not handler:
            raise ValueError(f"Unknown task type: {task.payload.get('type')}")

        return await handler(task)

    async def validate_task(self, task: Task) -> bool:
        if not self._permissions.get("computer_control", False):
            raise PermissionError("Computer control permission not granted")
        return True

    def _check_permission(self, permission: str) -> bool:
        return self._permissions.get(permission, False)

    # Mouse operations
    async def _mouse_move(self, task: Task) -> Dict[str, Any]:
        x = task.payload.get("x")
        y = task.payload.get("y")
        duration = task.payload.get("duration", 0.5)

        if x is None or y is None:
            raise ValueError("x and y coordinates required")

        try:
            import pyautogui
            pyautogui.moveTo(x, y, duration=duration)
            return {"success": True, "position": {"x": x, "y": y}}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _mouse_click(self, task: Task) -> Dict[str, Any]:
        x = task.payload.get("x")
        y = task.payload.get("y")
        button = task.payload.get("button", "left")
        clicks = task.payload.get("clicks", 1)
        interval = task.payload.get("interval", 0.1)

        if x is not None and y is not None:
            import pyautogui
            pyautogui.moveTo(x, y)

        import pyautogui
        pyautogui.click(button=button, clicks=clicks, interval=interval)
        return {"success": True, "button": button, "clicks": clicks}

    async def _mouse_drag(self, task: Task) -> Dict[str, Any]:
        start_x = task.payload.get("start_x")
        start_y = task.payload.get("start_y")
        end_x = task.payload.get("end_x")
        end_y = task.payload.get("end_y")
        duration = task.payload.get("duration", 0.5)

        if None in [start_x, start_y, end_x, end_y]:
            raise ValueError("start_x, start_y, end_x, end_y required")

        import pyautogui
        pyautogui.moveTo(start_x, start_y)
        pyautogui.dragTo(end_x, end_y, duration=duration, button="left")
        return {"success": True}

    async def _mouse_scroll(self, task: Task) -> Dict[str, Any]:
        clicks = task.payload.get("clicks", 3)
        x = task.payload.get("x")
        y = task.payload.get("y")

        if x is not None and y is not None:
            import pyautogui
            pyautogui.moveTo(x, y)

        import pyautogui
        pyautogui.scroll(clicks)
        return {"success": True, "clicks": clicks}

    # Keyboard operations
    async def _keyboard_type(self, task: Task) -> Dict[str, Any]:
        text = task.payload.get("text", "")
        interval = task.payload.get("interval", 0.05)

        if not text:
            raise ValueError("text required")

        import pyautogui
        pyautogui.write(text, interval=interval)
        return {"success": True, "length": len(text)}

    async def _keyboard_press(self, task: Task) -> Dict[str, Any]:
        key = task.payload.get("key")
        presses = task.payload.get("presses", 1)
        interval = task.payload.get("interval", 0.1)

        if not key:
            raise ValueError("key required")

        import pyautogui
        for _ in range(presses):
            pyautogui.press(key)
            await asyncio.sleep(0.1)

        return {"success": True, "key": key, "presses": presses}

    async def _keyboard_hotkey(self, task: Task) -> Dict[str, Any]:
        keys = task.payload.get("keys", [])

        if not keys:
            raise ValueError("keys array required")

        import pyautogui
        pyautogui.hotkey(*keys)
        return {"success": True, "keys": keys}

    # Screen capture
    async def _screenshot(self, task: Task) -> Dict[str, Any]:
        format_ = task.payload.get("format", "png")
        quality = task.payload.get("quality", 85)
        save_path = task.payload.get("save_path")

        import pyautogui
        import io

        screenshot = pyautogui.screenshot()

        if save_path:
            screenshot.save(save_path)
            return {"success": True, "path": save_path}
        else:
            buffer = io.BytesIO()
            screenshot.save(buffer, format=format_.upper(), quality=quality)
            buffer.seek(0)
            encoded = base64.b64encode(buffer.read()).decode()
            return {"success": True, "screenshot_base64": encoded, "format": format_}

    async def _screen_region(self, task: Task) -> Dict[str, Any]:
        x = task.payload.get("x", 0)
        y = task.payload.get("y", 0)
        width = task.payload.get("width")
        height = task.payload.get("height")
        save_path = task.payload.get("save_path")

        if width is None or height is None:
            raise ValueError("width and height required")

        import pyautogui
        import io

        region = (x, y, width, height)
        screenshot = pyautogui.screenshot(region=region)

        if save_path:
            screenshot.save(save_path)
            return {"success": True, "path": save_path}
        else:
            buffer = io.BytesIO()
            screenshot.save(buffer, format="PNG")
            buffer.seek(0)
            encoded = base64.b64encode(buffer.read()).decode()
            return {"success": True, "screenshot_base64": encoded, "region": {"x": x, "y": y, "width": width, "height": height}}

    # Browser automation (Playwright)
    async def _ensure_browser(self) -> None:
        if self.browser is None:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=self.config.config.get("headless", False))
            self.page = await self.browser.new_page()

    async def _browser_open(self, task: Task) -> Dict[str, Any]:
        url = task.payload.get("url", "about:blank")
        headless = task.payload.get("headless", self.config.config.get("headless", False))

        from playwright.async_api import async_playwright
        if self.browser:
            await self.browser.close()

        self.playwright = await async_playwright().start()
        self.browser = await async_playwright().chromium.launch(headless=headless)
        self.page = await self.browser.new_page()

        await self.page.goto(url)
        return {"success": True, "url": url}

    async def _browser_navigate(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        url = task.payload.get("url")
        wait_until = task.payload.get("wait_until", "networkidle")

        if not url:
            raise ValueError("url required")

        await self.page.goto(url, wait_until=wait_until)
        return {"success": True, "url": self.page.url}

    async def _browser_click(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        selector = task.payload.get("selector")
        x = task.payload.get("x")
        y = task.payload.get("y")
        button = task.payload.get("button", "left")

        if selector:
            await self.page.click(selector)
        elif x is not None and y is not None:
            await self.page.mouse.click(x, y, button=button)
        else:
            raise ValueError("selector or x,y coordinates required")

        return {"success": True}

    async def _browser_type(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        selector = task.payload.get("selector")
        text = task.payload.get("text", "")
        delay = task.payload.get("delay", 50)

        if not selector or not text:
            raise ValueError("selector and text required")

        await self.page.fill(selector, "")
        await self.page.type(selector, text, delay=delay)
        return {"success": True}

    async def _browser_extract(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        selector = task.payload.get("selector")
        attribute = task.payload.get("attribute", "textContent")
        multiple = task.payload.get("multiple", False)

        if not selector:
            raise ValueError("selector required")

        if multiple:
            elements = await self.page.query_selector_all(selector)
            values = []
            for el in elements:
                val = await el.get_attribute(attribute) if attribute != "textContent" else await el.text_content()
                values.append(val)
            return {"success": True, "values": values}
        else:
            element = await self.page.query_selector(selector)
            if not element:
                return {"success": False, "error": "Element not found"}
            val = await element.get_attribute(attribute) if attribute != "textContent" else await element.text_content()
            return {"success": True, "value": val}

    async def _browser_execute_js(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        script = task.payload.get("script")
        args = task.payload.get("args", [])

        if not script:
            raise ValueError("script required")

        result = await self.page.evaluate(script, *args)
        return {"success": True, "result": result}

    async def _browser_wait(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        selector = task.payload.get("selector")
        timeout = task.payload.get("timeout", 30000)
        state = task.payload.get("state", "visible")

        if not selector:
            raise ValueError("selector required")

        await self.page.wait_for_selector(selector, state=state, timeout=timeout)
        return {"success": True}

    async def _browser_screenshot(self, task: Task) -> Dict[str, Any]:
        if not self.page:
            raise RuntimeError("Browser not open")

        path = task.payload.get("path")
        full_page = task.payload.get("full_page", False)

        screenshot = await self.page.screenshot(path=path, full_page=full_page)
        if path:
            return {"success": True, "path": path}
        else:
            encoded = base64.b64encode(screenshot).decode()
            return {"success": True, "screenshot_base64": encoded}

    # File operations
    async def _file_read(self, task: Task) -> Dict[str, Any]:
        path = task.payload.get("path")
        encoding = task.payload.get("encoding", "utf-8")

        if not path:
            raise ValueError("path required")

        try:
            with open(os.path.expanduser(path), "r", encoding=encoding) as f:
                content = f.read()
            return {"success": True, "content": content, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _file_write(self, task: Task) -> Dict[str, Any]:
        path = task.payload.get("path")
        content = task.payload.get("content", "")
        mode = task.payload.get("mode", "w")
        encoding = task.payload.get("encoding", "utf-8")

        if not path:
            raise ValueError("path required")

        try:
            os.makedirs(os.path.dirname(os.path.expanduser(path)), exist_ok=True)
            with open(os.path.expanduser(path), mode, encoding=encoding) as f:
                f.write(content)
            return {"success": True, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _file_list(self, task: Task) -> Dict[str, Any]:
        path = task.payload.get("path", ".")
        recursive = task.payload.get("recursive", False)

        try:
            path = os.path.expanduser(path)
            if recursive:
                files = []
                for root, dirs, filenames in os.walk(path):
                    for f in filenames:
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, path)
                        stat = os.stat(full)
                        files.append({
                            "name": rel,
                            "path": full,
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        })
            else:
                files = []
                for f in os.listdir(path):
                    full = os.path.join(path, f)
                    stat = os.stat(full)
                    files.append({
                        "name": f,
                        "path": full,
                        "size": stat.st_size,
                        "is_dir": os.path.isdir(full),
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
            return {"success": True, "files": files, "path": path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _shell_command(self, task: Task) -> Dict[str, Any]:
        command = task.payload.get("command")
        cwd = task.payload.get("cwd")
        timeout = task.payload.get("timeout", 60)

        if not command:
            raise ValueError("command required")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=os.path.expanduser(cwd) if cwd else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "success": True,
                "returncode": proc.returncode,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": "Command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}


import re
import base64
from playwright.async_api import async_playwright