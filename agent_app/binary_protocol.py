"""
EMOG Binary Protocol - EMOG İkili Komut Protokolü

Bu modül, EMOG'ın içsel iletişim katmanı için bayt seviyesinde komut
çerçevesini tanımlar. Tüm araçlar (video düzenleme, dosya işlemleri, ağ
işlemleri, sistem komutları) bu protokol üzerinden iletişim kurar.

Komut Kodları (Command IDs):
    0x01 - USER_INPUT      : Kullanıcı girdisi
    0x02 - MODEL_REQUEST   : Model isteği
    0x03 - MODEL_RESPONSE  : Model cevabı
    0x04 - EVALUATE        : Cevap değerlendirme
    0x05 - MEMORY_WRITE    : Hafızaya kayıt
    0x06 - MEMORY_READ     : Hafızadan okuma
    0x07 - LEARN           : Öğrenme işlemi
    0x08 - DECISION        : Karar üretimi
    0x09 - TOOL_EXECUTE    : Araç çalıştırma
    0x0A - VERIFY          : Sonuç doğrulama

Mesaj Yapısı (Big-Endian):
    +--------+--------+--------+--------+--------+--------+--------+--------+
    | VERSION(1) | MSG_TYPE(1) | USER_ID(2) | TASK_ID(2) | MODEL_ID(2) |
    +--------+--------+--------+--------+--------+--------+--------+--------+
    | PAYLOAD_LEN(2) | TIMESTAMP(8) | CHECKSUM(2) | PAYLOAD (var) |
    +--------+--------+--------+--------+--------+--------+--------+--------+

Toplam başlık boyutu: 16 byte + payload boyutu
"""

from __future__ import annotations

import struct
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Komut kodları (Command IDs)
CMD_USER_INPUT = 0x01      # Kullanıcı girdisi
CMD_MODEL_REQUEST = 0x02   # Model isteği
CMD_MODEL_RESPONSE = 0x03  # Model cevabı
CMD_EVALUATE = 0x04        # Cevap değerlendirme
CMD_MEMORY_WRITE = 0x05    # Hafızaya kayıt
CMD_MEMORY_READ = 0x06     # Hafızadan okuma
CMD_LEARN = 0x07           # Öğrenme işlemi
CMD_DECISION = 0x08        # Karar üretimi
CMD_TOOL_EXECUTE = 0x09    # Araç çalıştırma
CMD_VERIFY = 0x0A          # Sonuç doğrulama

# Komut isimleri (debug/logging için)
COMMAND_NAMES: Dict[int, str] = {
    CMD_USER_INPUT: "USER_INPUT",
    CMD_MODEL_REQUEST: "MODEL_REQUEST",
    CMD_MODEL_RESPONSE: "MODEL_RESPONSE",
    CMD_EVALUATE: "EVALUATE",
    CMD_MEMORY_WRITE: "MEMORY_WRITE",
    CMD_MEMORY_READ: "MEMORY_READ",
    CMD_LEARN: "LEARN",
    CMD_DECISION: "DECISION",
    CMD_TOOL_EXECUTE: "TOOL_EXECUTE",
    CMD_VERIFY: "VERIFY",
}

# Protokol sürümü
PROTOCOL_VERSION = 0x01

# Mesaj başlığı boyutu (18 byte)
HEADER_SIZE = 18

# CHECKSUM algoritması basit XOR toplamı


def compute_checksum(data: bytes) -> int:
    """Basit XOR checksum (16-bit)."""
    checksum = 0
    for b in data:
        checksum ^= b
    return checksum & 0xFFFF


@dataclass
class BinaryMessage:
    """
    EMOG İkili Mesaj Yapısı.

    Alanlar:
        version: Protokol sürümü (1 byte)
        msg_type: Komut kodu (1 byte)
        user_id: Kullanıcı kimliği (2 byte)
        task_id: Görev kimliği (2 byte)
        model_id: Model kimliği (2 byte)
        payload: Yük (payload) baytları
        timestamp: Zaman damgası (saniye cinsinden float, 8 byte)
        checksum: XOR toplamı (2 byte)
    """

    msg_type: int
    payload: bytes
    user_id: int = 0
    task_id: int = 0
    model_id: int = 0
    version: int = PROTOCOL_VERSION
    timestamp: Optional[float] = None
    checksum: Optional[int] = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_bytes(self) -> bytes:
        """Mesajı bayt dizisine dönüştürür (big-endian)."""

        # Payload boyutu
        payload_len = len(self.payload)

        # Zaman damgası (double, 8 byte)
        timestamp = self.timestamp if self.timestamp is not None else time.time()

        # Başlık oluştur (big-endian)
        header = struct.pack(
            ">BBHHHHd",
            self.version,
            self.msg_type,
            self.user_id,
            self.task_id,
            self.model_id,
            payload_len,
            timestamp,
        )

        # Checksum (header + payload üzerinden)
        checksum_data = header + self.payload
        checksum = compute_checksum(checksum_data)

        # Tam mesaj = header + checksum + payload
        return header + struct.pack(">H", checksum) + self.payload

    @classmethod
    def from_bytes(cls, data: bytes) -> "BinaryMessage":
        """Bayt dizisinden BinaryMessage oluşturur."""
        if len(data) < HEADER_SIZE + 2:
            raise ValueError("Mesaj çok kısa (minimum 18 byte gerekli)")

        # Başlık parçalama
        header = data[:HEADER_SIZE]
        (
            version,
            msg_type,
            user_id,
            task_id,
            model_id,
            payload_len,
            timestamp,
        ) = struct.unpack(">BBHHHHd", header)

        # Checksum
        checksum = struct.unpack(">H", data[HEADER_SIZE:HEADER_SIZE + 2])[0]

        # Payload
        payload = data[HEADER_SIZE + 2:HEADER_SIZE + 2 + payload_len]

        return cls(
            version=version,
            msg_type=msg_type,
            user_id=user_id,
            task_id=task_id,
            model_id=model_id,
            payload=payload,
            timestamp=timestamp,
            checksum=checksum,
        )

    def validate_checksum(self) -> bool:
        """Checksum'u doğrular."""
        header = self.to_bytes()[:HEADER_SIZE]
        payload = self.payload
        expected = struct.unpack(">H", self.to_bytes()[HEADER_SIZE:HEADER_SIZE + 2])[0]
        actual = compute_checksum(header + payload)
        return actual == expected

    def __repr__(self) -> str:
        cmd_name = COMMAND_NAMES.get(self.msg_type, f"UNKNOWN(0x{self.msg_type:02X})")
        return (
            f"BinaryMessage(msg_type={cmd_name}(0x{self.msg_type:02X}), "
            f"user_id={self.user_id}, task_id={self.task_id}, "
            f"model_id={self.model_id}, payload_len={len(self.payload)}, "
            f"checksum_ok={self.validate_checksum()})"
        )


def create_command(
    msg_type: int,
    payload: bytes = b"",
    user_id: int = 0,
    task_id: int = 0,
    model_id: int = 0,
) -> BinaryMessage:
    """Yeni bir komut mesajı oluşturur."""
    return BinaryMessage(
        msg_type=msg_type,
        payload=payload,
        user_id=user_id,
        task_id=task_id,
        model_id=model_id,
    )


def create_user_input(
    text: str,
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Kullanıcı girdisi mesajı oluşturur."""
    return BinaryMessage(
        msg_type=CMD_USER_INPUT,
        payload=text.encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
    )


def create_model_request(
    prompt: str,
    model_id: int = 0,
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Model isteği mesajı oluşturur."""
    return BinaryMessage(
        msg_type=CMD_MODEL_REQUEST,
        payload=prompt.encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
        model_id=model_id,
    )


def create_model_response(
    response_text: str,
    model_id: int = 0,
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Model cevabı mesajı oluşturur."""
    return BinaryMessage(
        msg_type=CMD_MODEL_RESPONSE,
        payload=response_text.encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
        model_id=model_id,
    )


def create_tool_execute(
    tool_name: str,
    args: Dict[str, Any],
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Araç çalıştırma mesajı oluşturur."""
    import json

    payload = {
        "tool": tool_name,
        "args": args,
    }
    return BinaryMessage(
        msg_type=CMD_TOOL_EXECUTE,
        payload=json.dumps(payload).encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
    )


def create_evaluate(
    input_text: str,
    expected: str,
    actual: str,
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Değerlendirme mesajı oluşturur."""
    import json

    payload = {
        "input": input_text,
        "expected": expected,
        "actual": actual,
    }
    return BinaryMessage(
        msg_type=CMD_EVALUATE,
        payload=json.dumps(payload).encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
    )


def create_memory_write(
    key: str,
    value: Any,
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Hafıza yazma mesajı oluşturur."""
    import json

    payload = {
        "key": key,
        "value": value,
    }
    return BinaryMessage(
        msg_type=CMD_MEMORY_WRITE,
        payload=json.dumps(payload).encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
    )


def create_memory_read(
    key: str,
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Hafıza okuma mesajı oluşturur."""
    import json

    payload = {
        "key": key,
    }
    return BinaryMessage(
        msg_type=CMD_MEMORY_READ,
        payload=json.dumps(payload).encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
    )


def create_learn(
    data: Dict[str, Any],
    user_id: int = 0,
    task_id: int = 0,
) -> BinaryMessage:
    """Öğrenme mesajı oluşturur."""
    import json

    payload = {
        "learn_data": data,
    }
    return BinaryMessage(
        msg_type=CMD_LEARN,
        payload=json.dumps(payload).encode("utf-8"),
        user_id=user_id,
        task_id=task_id,
    )


if __name__ == "__main__":
    # Hızlı test
    msg = create_user_input("Merhaba EMOG", user_id=1, task_id=1)
    encoded = msg.to_bytes()
    print("Created message:", msg)
    print(f"Byte length: {len(encoded)}")
    print(f"Hex: {encoded.hex()}")

    decoded = BinaryMessage.from_bytes(encoded)
    print(f"Decoded: {decoded}")
    print(f"Checksum valid: {decoded.validate_checksum()}")
    print(f"Payload text: {decoded.payload.decode('utf-8')}")
