"""Which tools a request gets.

A small model asked a question ("what would it take to add --verbose?") often just does the
work: it edits files, runs git commands that change the project, and the user gets a changed
project instead of an answer. Prompt text asking it not to doesn't stop it. So a request that
clearly is a question gets only the tools that read: editing is impossible, not just discouraged.
`/do REQUEST` (in the interactive session) runs a request with all tools whatever it looks like.

The rule leans toward giving all tools. A request is a question only when it asks something
(it ends a sentence with "?", or starts with a question word) AND none of its sentences opens
with an instruction to change or run something. Everything else is work.
"""

from __future__ import annotations

import re

QUESTION = "question"
WORK = "work"

# The tools that cannot change anything (git is the read-only git tool, not run_shell).
READ_ONLY_TOOLS = frozenset({"read_file", "list_files", "search_code", "git"})

_QUESTION_START = re.compile(
    r"^(what|how|why|where|which|who|whom|whose|when|is|are|was|were|does|do|did|can|could|would|should|will|"
    r"explain|describe|summari[sz]e|tell me|walk me through|give me an? (overview|summary))\b",
    re.IGNORECASE,
)
# An instruction to change or run something. Only looked for at the start of a sentence, so
# "what would it take to add a flag?" is still a question while "fix the flag" is not.
_INSTRUCTION_VERBS = (
    "add|fix|change|update|create|write|delete|remove|rename|tag|merge|revert|undo|put|bring|extract|implement|"
    "refactor|make|set|edit|modify|replace|move|install|commit|push|pull|rebase|cherry-pick|apply|finish|resolve|"
    "run|execute|build|test|compile|deploy|generate|migrate|upgrade|convert|clean|format|drop|skip|normalize|"
    "classify|use|support|accept|enable|disable|patch|rewrite|amend|stage|checkout|switch|branch|fetch|"
    "let's|let us|go ahead|do"
)
_INSTRUCTION = re.compile(
    rf"^(please\s+)?((can|could|would|will)\s+you\s+(please\s+)?)?({_INSTRUCTION_VERBS})\b", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"(?<=[.?!])\s+|\n+")


def classify(request: str) -> str:
    """QUESTION when the request only asks for information, otherwise WORK."""
    sentences = [
        s.strip() for s in _SENTENCE_END.split(request.strip())
        if s.strip() and not s[0].isspace()  # indented lines are pasted code, logs or tracebacks
    ]
    if not sentences or any(_INSTRUCTION.match(s) for s in sentences):
        return WORK
    asks = any(s.endswith("?") for s in sentences) or bool(_QUESTION_START.match(sentences[0]))
    return QUESTION if asks else WORK


QUESTION_HINT = (
    "[beastron] This request reads as a question, so you only have tools that read "
    "(read_file, list_files, search_code, git). Answer from what you read, and don't change anything. "
    "If the answer needs something to be run or changed, say what and why: the user can ask again with /do."
)
NOT_AVAILABLE = (
    "Not available: this request was read as a question, so only read_file, list_files, search_code and git "
    "work. Answer from what you can read. If it needs running or changing something, say so: the user can "
    "ask again with /do."
)
