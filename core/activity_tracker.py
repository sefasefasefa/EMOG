"""
11. User Activity Tracking & Mimicry
Observes user actions at OS level, collects data, autonomously replicates similar tasks.
"""
import asyncio
import time
import hashlib
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json
import threading
from collections import defaultdict


class ActionType(Enum):
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    WINDOW = "window"
    FILE = "file"
    COMMAND = "command"
    BROWSER = "browser"
    APPLICATION = "application"


@dataclass
class UserAction:
    id: str
    type: ActionType
    timestamp: float
    details: Dict
    context: Dict = field(default_factory=dict)
    duration: float = 0.0


@dataclass
class ActionPattern:
    id: str
    name: str
    sequence: List[Dict]
    frequency: int
    last_seen: float
    context: Dict = field(default_factory=dict)
    automation_script: Optional[str] = None


@dataclass
class MimicryTask:
    id: str
    pattern_id: str
    trigger: Dict
    actions: List[Dict]
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


class ActivityTracker:
    """
    Tracks user activity at OS level.
    Collects keystrokes, mouse movements, window switches, file operations.
    """
    
    def __init__(self, storage_path: str = "./activity_logs"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)
        
        self.actions: List[UserAction] = []
        self.patterns: Dict[str, ActionPattern] = {}
        self.is_tracking = False
        self._listeners: List[Callable] = []
        
        # OS-specific trackers
        self._keyboard_listener = None
        self._mouse_listener = None
        self._window_tracker = None
    
    def start_tracking(self) -> None:
        """Start activity tracking."""
        self.is_tracking = True
        
        # Try to initialize OS-specific trackers
        try:
            import pynput
            self._init_pynput_trackers()
        except ImportError:
            pass
        
        # Start background analysis
        self._analysis_thread = threading.Thread(target=self._analyze_patterns, daemon=True)
        self._analysis_thread.start()
    
    def _init_pynput_trackers(self) -> None:
        """Initialize pynput keyboard/mouse listeners."""
        from pynput import keyboard, mouse
        
        def on_key_press(key):
            self._record_action(ActionType.KEYBOARD, {
                "key": str(key),
                "event": "press"
            })
        
        def on_key_release(key):
            self._record_action(ActionType.KEYBOARD, {
                "key": str(key),
                "event": "release"
            })
        
        def on_mouse_move(x, y):
            self._record_action(ActionType.MOUSE, {
                "x": x, "y": y, "event": "move"
            })
        
        def on_mouse_click(x, y, button, pressed):
            self._record_action(ActionType.MOUSE, {
                "x": x, "y": y, "button": str(button), "event": "press" if pressed else "release"
            })
        
        def on_mouse_scroll(x, y, dx, dy):
            self._record_action(ActionType.MOUSE, {
                "x": x, "y": y, "dx": dx, "dy": dy, "event": "scroll"
            })
        
        self._keyboard_listener = keyboard.Listener(
            on_press=on_key_press,
            on_release=on_key_release
        )
        self._mouse_listener = mouse.Listener(
            on_move=on_mouse_move,
            on_click=on_mouse_click,
            on_scroll=on_mouse_scroll
        )
        
        self._keyboard_listener.start()
        self._mouse_listener.start()
    
    def _record_action(self, action_type: ActionType, details: Dict) -> None:
        """Record a user action."""
        action = UserAction(
            id=f"act_{hashlib.md5(f'{time.time()}:{details}'.encode()).hexdigest()[:8]}",
            type=action_type,
            timestamp=time.time(),
            details=details,
            context=self._get_current_context()
        )
        
        self.actions.append(action)
        
        # Notify listeners
        for listener in self._listeners:
            try:
                listener(action)
            except Exception:
                pass
    
    def _get_current_context(self) -> Dict:
        """Get current context (active window, app, etc.)."""
        context = {"timestamp": time.time()}
        
        try:
            import psutil
            # Get active process
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    if proc.info['name'] and 'window' in proc.info['name'].lower():
                        context['active_app'] = proc.info['name']
                        break
                except Exception:
                    pass
        except ImportError:
            pass
        
        return context
    
    def stop_tracking(self) -> None:
        """Stop activity tracking."""
        self.is_tracking = False
        
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()
    
    def add_listener(self, listener: Callable) -> None:
        """Add action listener."""
        self._listeners.append(listener)
    
    def _analyze_patterns(self) -> None:
        """Background pattern analysis."""
        while self.is_tracking:
            time.sleep(60)  # Analyze every minute
            self._extract_patterns()
    
    def _extract_patterns(self) -> None:
        """Extract recurring action patterns."""
        if len(self.actions) < 10:
            return
        
        # Look for repeating sequences
        recent_actions = self.actions[-100:]
        
        # Simple n-gram analysis
        sequences = []
        for i in range(len(recent_actions) - 3):
            seq = recent_actions[i:i+4]
            seq_key = tuple((a.type.value, str(a.details)) for a in seq)
            sequences.append(seq_key)
        
        # Count frequencies
        seq_counts = defaultdict(int)
        for seq in sequences:
            seq_counts[seq] += 1
        
        # Create patterns for frequent sequences
        for seq, count in seq_counts.items():
            if count >= 3:
                pattern_id = hashlib.md5(str(seq).encode()).hexdigest()[:8]
                
                if pattern_id not in self.patterns:
                    self.patterns[pattern_id] = ActionPattern(
                        id=pattern_id,
                        name=f"Pattern_{len(self.patterns)}",
                        sequence=[{"type": t, "details": d} for t, d in seq],
                        frequency=count,
                        last_seen=time.time()
                    )
                else:
                    self.patterns[pattern_id].frequency = count
                    self.patterns[pattern_id].last_seen = time.time()
    
    def get_recent_actions(self, limit: int = 50) -> List[UserAction]:
        """Get recent actions."""
        return self.actions[-limit:]
    
    def get_patterns(self) -> List[ActionPattern]:
        """Get detected patterns."""
        return list(self.patterns.values())
    
    def save_logs(self) -> None:
        """Save action logs to disk."""
        log_file = self.storage_path / f"activity_{time.strftime('%Y%m%d')}.jsonl"
        
        with open(log_file, 'a') as f:
            for action in self.actions:
                f.write(json.dumps({
                    "id": action.id,
                    "type": action.type.value,
                    "timestamp": action.timestamp,
                    "details": action.details,
                    "context": action.context,
                }) + '\n')
        
        # Clear old actions from memory
        self.actions = self.actions[-1000:]


class MimicryEngine:
    """
    Replicates user behavior patterns autonomously.
    """
    
    def __init__(self, tracker: ActivityTracker):
        self.tracker = tracker
        self.tasks: List[MimicryTask] = []
        self.is_running = False
    
    def create_task_from_pattern(self, pattern_id: str, trigger: Dict = None) -> Optional[MimicryTask]:
        """Create mimicry task from detected pattern."""
        pattern = self.tracker.patterns.get(pattern_id)
        if not pattern:
            return None
        
        # Generate automation script
        script = self._generate_automation_script(pattern)
        
        task = MimicryTask(
            id=f"mimic_{hashlib.md5(f'{pattern_id}:{time.time()}'.encode()).hexdigest()[:8]}",
            pattern_id=pattern_id,
            trigger=trigger or {},
            actions=pattern.sequence,
            status="ready"
        )
        
        pattern.automation_script = script
        self.tasks.append(task)
        return task
    
    def _generate_automation_script(self, pattern: ActionPattern) -> str:
        """Generate Python automation script from pattern."""
        lines = [
            "# Auto-generated mimicry script",
            "import time",
            "from pynput import keyboard, mouse",
            "",
            "kb = keyboard.Controller()",
            "ms = mouse.Controller()",
            "",
        ]
        
        for action in pattern.sequence:
            action_type = action["type"]
            details = action["details"]
            
            if action_type == "keyboard":
                key = details.get("key", "")
                event = details.get("event", "press")
                if event == "press":
                    lines.append(f"kb.press({key})")
                else:
                    lines.append(f"kb.release({key})")
            elif action_type == "mouse":
                x = details.get("x", 0)
                y = details.get("y", 0)
                event = details.get("event", "move")
                if event == "move":
                    lines.append(f"ms.position = ({x}, {y})")
                elif event == "press":
                    lines.append(f"ms.press(mouse.Button.left)")
                elif event == "release":
                    lines.append(f"ms.release(mouse.Button.left)")
            
            lines.append("time.sleep(0.1)")
        
        return "\n".join(lines)
    
    async def execute_task(self, task_id: str) -> bool:
        """Execute a mimicry task."""
        task = next((t for t in self.tasks if t.id == task_id), None)
        if not task:
            return False
        
        task.status = "running"
        
        try:
            # Execute actions
            for action in task.actions:
                await self._execute_action(action)
                await asyncio.sleep(0.1)
            
            task.status = "completed"
            return True
        except Exception as e:
            task.status = "failed"
            return False
    
    async def _execute_action(self, action: Dict) -> None:
        """Execute single action."""
        # In real implementation, would use pynput or similar
        pass
    
    def get_pending_tasks(self) -> List[MimicryTask]:
        """Get pending mimicry tasks."""
        return [t for t in self.tasks if t.status in ["ready", "pending"]]


class ActivityMimicry:
    """High-level interface for activity tracking and mimicry."""
    
    def __init__(self, storage_path: str = "./activity_logs"):
        self.tracker = ActivityTracker(storage_path)
        self.mimicry = MimicryEngine(self.tracker)
    
    def start(self) -> None:
        """Start tracking and mimicry."""
        self.tracker.start_tracking()
        self.mimicry.is_running = True
    
    def stop(self) -> None:
        """Stop tracking and mimicry."""
        self.tracker.stop_tracking()
        self.tracker.save_logs()
        self.mimicry.is_running = False
    
    def get_automation_candidates(self, min_frequency: int = 3) -> List[ActionPattern]:
        """Get patterns suitable for automation."""
        return [
            p for p in self.tracker.get_patterns()
            if p.frequency >= min_frequency
        ]
    
    def create_automation(self, pattern_id: str) -> Optional[MimicryTask]:
        """Create automation from pattern."""
        return self.mimicry.create_task_from_pattern(pattern_id)
    
    def get_stats(self) -> Dict:
        """Get activity statistics."""
        return {
            "total_actions": len(self.tracker.actions),
            "patterns_detected": len(self.tracker.patterns),
            "automation_tasks": len(self.mimicry.tasks),
            "actions_by_type": self._count_by_type(),
        }
    
    def _count_by_type(self) -> Dict:
        counts = defaultdict(int)
        for action in self.tracker.actions:
            counts[action.type.value] += 1
        return dict(counts)