"""
Document chunking strategies for RAG indexing.
"""
from typing import Any, Dict, List


def split_into_chunks(
    text: str,
    max_tokens: int = 512,
    overlap: int = 50
) -> List[Dict[str, Any]]:
    """
    Split text into chunks with a sliding window.
    """
    chunk_size = max_tokens * 4  # Rough estimate: 1 token ≈ 4 chars
    overlap_chars = overlap * 4
    
    chunk_size = min(chunk_size, len(text))
    if chunk_size <= 0:
        return []
    
    chunk_list = []
    start = 0
    idx = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        
        # Optimize boundary at natural separators
        if idx > 0:
            for sep in ["\n\n", "\n", ". ", "。"]:
                last_sep = chunk_text.rfind(sep)
                if last_sep > chunk_size // 2:
                    end = start + last_sep + len(sep)
                    chunk_text = text[start:end]
                    break
        
        chunk_list.append({
            "content": chunk_text.strip(),
            "start_char": start,
            "end_char": end,
            "chunk_index": idx,
        })
        
        next_start = end - overlap_chars
        if next_start <= start:
            next_start = start + max(1, chunk_size // 2)
        start = next_start
        if start >= len(text):
            break
        idx += 1
    
    return chunk_list


def split_markdown_sections(content: str) -> List[str]:
    """
    Split markdown document content into sections by '## ' headings.
    """
    import re
    sections = re.split(r"(?=\n##\s)", content)
    return [s.strip() for s in sections if s.strip()]
