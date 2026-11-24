"""
16. Voice/Real-time Communication
Voice commands to agent (e.g. "Add login form to this page").
"""
import asyncio
import time
import hashlib
import json
import tempfile
import base64
from typing import Dict, List, Any, Optional, Callable, AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from abc import ABC, abstractmethod
import asyncio.subprocess


class VoiceCommandType(Enum):
    CODE_GENERATION = "code_generation"
    CODE_MODIFICATION = "code_modification"
    DEBUGGING = "debugging"
    EXPLANATION = "explanation"
    NAVIGATION = "navigation"
    TESTING = "testing"
    DEPLOYMENT = "deployment"
    QUERY = "query"
    SYSTEM = "system"


class VoiceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


@dataclass
class VoiceCommand:
    id: str
    text: str
    command_type: VoiceCommandType
    confidence: float
    language: str = "en"
    timestamp: float = field(default_factory=time.time)
    parameters: Dict = field(default_factory=dict)
    context: Dict = field(default_factory=dict)
    audio_duration: float = 0.0


@dataclass
class VoiceResponse:
    command_id: str
    text: str
    audio_data: Optional[bytes] = None
    audio_format: str = "wav"
    actions: List[Dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class STTEngine(ABC):
    """Speech-to-Text engine interface."""
    
    @abstractmethod
    async def transcribe(self, audio_data: bytes, format: str = "wav", language: str = "en") -> Dict:
        """Transcribe audio to text."""
        pass
    
    @abstractmethod
    async def stream_transcribe(self, audio_stream: AsyncGenerator[bytes, None]) -> AsyncGenerator[Dict, None]:
        """Stream transcription."""
        pass


class TTSEngine(ABC):
    """Text-to-Speech engine interface."""
    
    @abstractmethod
    async def synthesize(self, text: str, voice: str = "default", language: str = "en") -> bytes:
        """Synthesize speech from text."""
        pass
    
    @abstractmethod
    async def stream_synthesize(self, text: str, voice: str = "default") -> AsyncGenerator[bytes, None]:
        """Stream synthesis."""
        pass


class WhisperSTT(STTEngine):
    """OpenAI Whisper STT engine."""
    
    def __init__(self, model: str = "base", device: str = "cpu"):
        self.model_name = model
        self.device = device
        self._model = None
    
    async def _load_model(self):
        if self._model is None:
            try:
                import whisper
                self._model = whisper.load_model(self.model_name, device=self.device)
            except ImportError:
                raise RuntimeError("whisper not installed: pip install openai-whisper")
    
    async def transcribe(self, audio_data: bytes, format: str = "wav", language: str = "en") -> Dict:
        await self._load_model()
        
        # Save audio to temp file
        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            result = self._model.transcribe(temp_path, language=language if language != "auto" else None)
            return {
                "text": result["text"].strip(),
                "language": result.get("language", language),
                "segments": result.get("segments", []),
                "confidence": 0.9  # Whisper doesn't provide confidence directly
            }
        finally:
            Path(temp_path).unlink(missing_ok=True)
    
    async def stream_transcribe(self, audio_stream: AsyncGenerator[bytes, None]) -> AsyncGenerator[Dict, None]:
        """Stream transcription (simulated with chunks)."""
        await self._load_model()
        buffer = bytearray()
        
        async for chunk in audio_stream:
            buffer.extend(chunk)
            # Process every ~2 seconds of audio
            if len(buffer) > 32000 * 2:  # ~2 seconds at 16kHz
                result = await self.transcribe(bytes(buffer))
                yield result
                buffer.clear()


class OpenAISTT(STTEngine):
    """OpenAI Whisper API STT."""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key
    
    async def transcribe(self, audio_data: bytes, format: str = "wav", language: str = "en") -> Dict:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key)
            
            with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
                f.write(audio_data)
                temp_path = f.name
            
            try:
                with open(temp_path, "rb") as audio_file:
                    result = await client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language=language if language != "auto" else None,
                        response_format="verbose_json"
                    )
                
                return {
                    "text": result.text.strip(),
                    "language": result.language,
                    "duration": result.duration,
                    "confidence": 0.95
                }
            finally:
                Path(temp_path).unlink(missing_ok=True)
        except ImportError:
            return {"text": "", "error": "openai package not installed", "confidence": 0}
        except Exception as e:
            return {"text": "", "error": str(e), "confidence": 0}
    
    async def stream_transcribe(self, audio_stream: AsyncGenerator[bytes, None]) -> AsyncGenerator[Dict, None]:
        buffer = bytearray()
        async for chunk in audio_stream:
            buffer.extend(chunk)
            if len(buffer) > 16000 * 2:  # ~2 seconds
                result = await self.transcribe(bytes(buffer))
                yield result
                buffer.clear()


class EdgeTTS(TTSEngine):
    """Microsoft Edge TTS (free, high quality)."""
    
    VOICES = {
        "en": ["en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural"],
        "tr": ["tr-TR-EmelNeural", "tr-TR-AhmetNeural"],
        "de": ["de-DE-KatjaNeural", "de-DE-ConradNeural"],
        "fr": ["fr-FR-DeniseNeural", "fr-FR-HenriNeural"],
        "es": ["es-ES-ElviraNeural", "es-ES-AlvaroNeural"],
        "ja": ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"],
        "zh": ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"],
    }
    
    def __init__(self):
        self._communicate = None
    
    async def synthesize(self, text: str, voice: str = "default", language: str = "en") -> bytes:
        try:
            import edge_tts
        except ImportError:
            raise RuntimeError("edge-tts not installed: pip install edge-tts")
        
        # Select voice
        if voice == "default":
            voice = self.VOICES.get(language, ["en-US-AriaNeural"])[0]
        
        communicate = edge_tts.Communicate(text, voice)
        
        audio_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.extend(chunk["data"])
        
        return bytes(audio_data)
    
    async def stream_synthesize(self, text: str, voice: str = "default") -> AsyncGenerator[bytes, None]:
        import edge_tts
        
        if voice == "default":
            voice = self.VOICES.get("en", ["en-US-AriaNeural"])[0]
        
        communicate = edge_tts.Communicate(text, voice)
        
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]


class OpenAITTS(TTSEngine):
    """OpenAI TTS API."""
    
    VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key
    
    async def synthesize(self, text: str, voice: str = "nova", language: str = "en") -> bytes:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=self.api_key)
            
            if voice not in self.VOICES:
                voice = "nova"
            
            response = await client.audio.speech.create(
                model="tts-1-hd",
                voice=voice,
                input=text,
                response_format="mp3"
            )
            
            return response.content
        except ImportError:
            raise RuntimeError("openai package not installed")
        except Exception as e:
            raise RuntimeError(f"TTS synthesis failed: {e}")
    
    async def stream_synthesize(self, text: str, voice: str = "nova") -> AsyncGenerator[bytes, None]:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.api_key)
        
        if voice not in self.VOICES:
            voice = "nova"
        
        async with client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3"
        ) as response:
            async for chunk in response.iter_bytes(1024):
                yield chunk


class VoiceCommandProcessor:
    """Processes voice commands and maps them to actions."""
    
    COMMAND_PATTERNS = {
        VoiceCommandType.CODE_GENERATION: [
            r"(create|write|generate|make|build)\s+(a|an)?\s*(\w+)",
            r"write\s+(code|function|class|api|component)",
            r"implement\s+(\w+)",
        ],
        VoiceCommandType.CODE_MODIFICATION: [
            r"(change|modify|update|fix|refactor)\s+(the|this|that)?\s*(\w+)",
            r"add\s+(\w+)\s+to\s+(\w+)",
            r"remove\s+(\w+)",
        ],
        VoiceCommandType.DEBUGGING: [
            r"(debug|fix|solve)\s+(this|the|that)?\s*(error|bug|issue)",
            r"why\s+(is|does)\s+(\w+)",
            r"explain\s+(the|this)?\s*(error|bug)",
        ],
        VoiceCommandType.EXPLANATION: [
            r"(explain|describe|what\s+is)\s+(\w+)",
            r"how\s+(does|to)\s+(\w+)",
        ],
        VoiceCommandType.TESTING: [
            r"(write|generate|create)\s+(tests?|unit\s+tests?)",
            r"test\s+(the|this)\s+(\w+)",
            r"run\s+tests?",
        ],
        VoiceCommandType.DEPLOYMENT: [
            r"deploy\s+(to|the)?\s*(\w+)",
            r"publish\s+(to|the)?\s*(\w+)",
            r"release\s+(\w+)",
        ],
        VoiceCommandType.NAVIGATION: [
            r"(go|open|show)\s+(to|the)?\s*(\w+)",
            r"switch\s+to\s+(\w+)",
        ],
        VoiceCommandType.SYSTEM: [
            r"(stop|pause|resume|quit|exit)",
            r"(help|what\s+can\s+you\s+do)",
        ],
    }
    
    @classmethod
    def classify(cls, text: str) -> tuple[VoiceCommandType, Dict]:
        """Classify voice command and extract parameters."""
        text_lower = text.lower()
        
        for cmd_type, patterns in cls.COMMAND_PATTERNS.items():
            for pattern in patterns:
                import re
                match = re.search(pattern, text_lower)
                if match:
                    params = {}
                    if match.groups():
                        if len(match.groups()) == 1:
                            params["target"] = match.group(1)
                        elif len(match.groups()) >= 2:
                            params["action"] = match.group(1)
                            params["target"] = match.group(2)
                            if len(match.groups()) > 2:
                                params["extra"] = match.group(3)
                    
                    return cmd_type, params
        
        return VoiceCommandType.QUERY, {"query": text}
    
    @classmethod
    def extract_code_entities(cls, text: str) -> Dict:
        """Extract code-related entities from text."""
        import re
        
        entities = {
            "languages": [],
            "frameworks": [],
            "files": [],
            "functions": [],
            "classes": [],
        }
        
        # Languages
        langs = ["python", "javascript", "typescript", "java", "go", "rust", "c++", "c#", "php", "ruby"]
        for lang in langs:
            if lang in text.lower():
                entities["languages"].append(lang)
        
        # Frameworks
        frameworks = ["react", "vue", "angular", "django", "flask", "fastapi", "express", "spring", "rails"]
        for fw in frameworks:
            if fw in text.lower():
                entities["frameworks"].append(fw)
        
        # File patterns
        file_matches = re.findall(r'(\w+\.(py|js|ts|java|go|rs|cpp|cs|php|rb))', text, re.IGNORECASE)
        entities["files"] = [m[0] for m in file_matches]
        
        # Function/class patterns
        func_matches = re.findall(r'(function|class|def)\s+(\w+)', text, re.IGNORECASE)
        for match in func_matches:
            if match[0].lower() in ["function", "def"]:
                entities["functions"].append(match[1])
            else:
                entities["classes"].append(match[1])
        
        return entities


class VoiceInterface:
    """
    Complete voice interface for agent interaction.
    Handles STT, command processing, TTS, and action execution.
    """
    
    def __init__(
        self,
        stt_engine: STTEngine = None,
        tts_engine: TTSEngine = None,
        action_executor: Callable = None
    ):
        self.stt = stt_engine or WhisperSTT()
        self.tts = tts_engine or EdgeTTS()
        self.action_executor = action_executor
        
        self.state = VoiceState.IDLE
        self.command_history: List[VoiceCommand] = []
        self.response_history: List[VoiceResponse] = []
        
        self._processor = VoiceCommandProcessor()
        self._callbacks: Dict[str, List[Callable]] = defaultdict(list)
    
    async def listen(self, audio_data: bytes, format: str = "wav", language: str = "en") -> VoiceCommand:
        """Process voice input and return command."""
        self.state = VoiceState.LISTENING
        
        # Transcribe
        result = await self.stt.transcribe(audio_data, language=language)
        
        if result.get("error"):
            self.state = VoiceState.ERROR
            return VoiceCommand(
                id=f"cmd_{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}",
                text="",
                command_type=VoiceCommandType.QUERY,
                confidence=0,
                error=result.get("error")
            )
        
        self.state = VoiceState.PROCESSING
        
        # Process command
        text = result.get("text", "")
        cmd_type, params = VoiceCommandProcessor.classify(text)
        entities = VoiceCommandProcessor.extract_code_entities(text)
        
        command = VoiceCommand(
            id=f"cmd_{hashlib.md5(f'{text}{time.time()}'.encode()).hexdigest()[:8]}",
            text=text,
            command_type=cmd_type,
            confidence=result.get("confidence", 0.9),
            language=result.get("language", "en"),
            parameters=params,
            context={"entities": entities},
            audio_duration=result.get("duration", 0)
        )
        
        self.command_history.append(command)
        
        # Execute if executor available
        response = await self._execute_command(command)
        
        self.state = VoiceState.IDLE
        return command
    
    async def _execute_command(self, command: VoiceCommand) -> VoiceResponse:
        """Execute voice command."""
        self.state = VoiceState.PROCESSING
        
        response_text = ""
        actions = []
        
        if self.action_executor:
            try:
                result = await self.action_executor(command)
                if isinstance(result, dict):
                    response_text = result.get("text", "Command executed")
                    actions = result.get("actions", [])
                else:
                    response_text = str(result)
            except Exception as e:
                response_text = f"Error executing command: {e}"
        else:
            # Default responses
            response_text = self._generate_response(command)
        
        # Synthesize speech
        audio_data = await self.tts.synthesize(response_text)
        
        response = VoiceResponse(
            command_id=command.id,
            text=response_text,
            audio_data=audio_data,
            actions=actions
        )
        
        self.response_history.append(response)
        self.state = VoiceState.IDLE
        
        return response
    
    def _generate_response(self, command: VoiceCommand) -> str:
        """Generate default response for command type."""
        responses = {
            VoiceCommandType.CODE_GENERATION: "I'll generate that code for you.",
            VoiceCommandType.CODE_MODIFICATION: "I'll make those changes.",
            VoiceCommandType.DEBUGGING: "Let me help debug that issue.",
            VoiceCommandType.EXPLANATION: "Let me explain that for you.",
            VoiceCommandType.TESTING: "I'll create those tests.",
            VoiceCommandType.DEPLOYMENT: "Deploying now.",
            VoiceCommandType.NAVIGATION: "Navigating there.",
            VoiceCommandType.SYSTEM: "Sure thing.",
            VoiceCommandType.QUERY: "Let me think about that...",
        }
        
        return responses.get(command.command_type, "I heard you. Processing...")
    
    async def speak(self, text: str, voice: str = "default") -> bytes:
        """Synthesize speech from text."""
        return await self.tts.synthesize(text, voice=voice)
    
    async def speak_stream(self, text: str, voice: str = "default") -> AsyncGenerator[bytes, None]:
        """Stream TTS output."""
        async for chunk in self.tts.stream_synthesize(text):
            yield chunk
    
    def register_callback(self, event: str, callback: Callable) -> None:
        """Register event callback."""
        self._callbacks[event].append(callback)
    
    def _trigger_callback(self, event: str, *args, **kwargs) -> None:
        for callback in self._callbacks.get(event, []):
            try:
                callback(*args, **kwargs)
            except Exception:
                pass
    
    def get_stats(self) -> Dict:
        """Get voice interface statistics."""
        return {
            "total_commands": len(self.command_history),
            "by_type": self._count_by_type(),
            "avg_confidence": sum(c.confidence for c in self.command_history) / len(self.command_history) if self.command_history else 0,
            "total_audio_duration": sum(c.audio_duration for c in self.command_history),
        }
    
    def _count_by_type(self) -> Dict:
        counts = defaultdict(int)
        for cmd in self.command_history:
            counts[cmd.command_type.value] += 1
        return dict(counts)


class VoiceChat:
    """
    High-level voice chat interface.
    Combines STT, command processing, TTS, and conversation management.
    """
    
    def __init__(
        self,
        stt: STTEngine = None,
        tts: TTSEngine = None,
        llm_executor: Callable = None
    ):
        self.voice = VoiceInterface(stt, tts, self._execute_with_llm)
        self.llm_executor = llm_executor
        self.conversation_history: List[Dict] = []
        self.is_active = False
    
    async def _execute_with_llm(self, command: VoiceCommand) -> Dict:
        """Execute command with LLM."""
        if self.llm_executor:
            return await self.llm_executor(command)
        return {"text": "I processed your command."}
    
    async def process_audio(self, audio_data: bytes) -> VoiceResponse:
        """Process audio input and return response."""
        command = await self.voice.listen(audio_data)
        response = await self.voice._execute_command(command)
        
        # Add to conversation history
        self.conversation_history.append({
            "role": "user",
            "content": command.text,
            "timestamp": command.timestamp
        })
        self.conversation_history.append({
            "role": "assistant",
            "content": response.text,
            "timestamp": response.timestamp
        })
        
        return response
    
    async def speak(self, text: str) -> bytes:
        """Speak text."""
        return await self.voice.speak(text)
    
    def get_conversation(self) -> List[Dict]:
        """Get conversation history."""
        return self.conversation_history
    
    def clear_history(self) -> None:
        """Clear conversation history."""
        self.conversation_history.clear()