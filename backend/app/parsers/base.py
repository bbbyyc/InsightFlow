from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ChunkData:
    content: str
    chunk_index: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    token_count: int = 0
    metadata: dict = field(default_factory=dict)


class BaseParser(ABC):

    @abstractmethod
    def parse(self, file_path: str) -> str:
        """抽取文件原始文本"""
        ...

    @abstractmethod
    def chunk(self, text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[ChunkData]:
        """切分文本为 chunk"""
        ...
