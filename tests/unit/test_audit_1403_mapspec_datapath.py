
"""#1403: ref-authoritative strip must drop dataPath."""
from pathlib import Path

def test_ref_strip_pops_datapath():
    src = Path("app/services/mapspec/pipeline.py").read_text()
    idx = src.find("A session ref is the authoritative carrier")
    assert idx > 0
    window = src[idx: idx + 700]
    assert 'source_entry.pop("dataPath", None)' in window
