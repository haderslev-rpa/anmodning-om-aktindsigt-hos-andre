from dataclasses import dataclass

@dataclass(frozen=True)
class BinaryFile:
    file_name: str
    content: bytes
    content_type: str = "application/octet-stream"
