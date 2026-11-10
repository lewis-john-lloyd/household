"""Structured log lines: each line the engine prints has a level and a topic, and a run prints it as

    [elapsed][LEVEL][stage][topic] message

A line is still the plain text it always was (Line is a str), so a stage called on its own with log=print prints
it unchanged and the views read it as before; the level rides along, kept in Reports' run_notes. The topic is
the words before the line's first colon ("prices: 3 added" is topic prices), a year or a run id left off, so
every existing line has one; a line with none (an indented detail, a file's progress) belongs to the topic of
the line before it.
"""

import re

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
TOPIC = re.compile(r"^([A-Za-z][A-Za-z _'-]{0,40}?)(?: \d{4}\b[^:]*| [0-9A-Z]{8,}[^:]*)?:")


class Line(str):
    """A log line's text, with its level."""

    level = "INFO"

    def __new__(cls, text, level="INFO"):
        if level not in LEVELS:
            raise ValueError(f"no such level: {level}")
        line = super().__new__(cls, text)
        line.level = level
        return line


def info(text):
    return Line(text, "INFO")


def warn(text):
    return Line(text, "WARN")


def error(text):
    return Line(text, "ERROR")


def level_of(line):
    return getattr(line, "level", "INFO")


def topic_of(text):
    """(topic, rest): the words before the first colon and what follows, or ("", text) when the line names none
    (it is indented, or starts with something other than words)."""
    if text[:1].isspace():
        return "", text
    m = TOPIC.match(text)
    if not m:
        return "", text
    return m.group(1).strip(), text[len(m.group(1).rstrip()) :].lstrip(" :")


def structured(text, stage, topic, level, elapsed=""):
    """The line as a run prints it: [elapsed][LEVEL][stage][topic] message."""
    head = (f"[{elapsed}]" if elapsed else "") + f"[{level}][{stage}][{topic or stage}]"
    return f"{head} {text}"


def counted(n, one, many=None):
    """n and the noun in the form n takes: "1 stub", "2 stubs" (many defaults to one + "s")."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"
