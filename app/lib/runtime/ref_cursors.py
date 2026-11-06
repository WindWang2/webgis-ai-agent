"""Store cursor syntax shared by runtime prompts and evaluation observers."""
import re

CONTEXT_OFFLOAD_REF_PREFIX = "ref:contextoffload-"

# Matches the same extensible type/id alphabet as the SessionStore resolver;
# adding a new store prefix must not make summaries and tombstones blind to it.
REF_CURSOR_RE = re.compile(r"\bref:[a-z][a-z0-9]*-[A-Za-z0-9_-]+(?![A-Za-z0-9_-])")
