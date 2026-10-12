import json
import logging

from utils.logging_setup import JsonFormatter


def _record(level: int, msg: str, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("agent.runtime", level, __file__, 1, msg, None, exc_info)


def test_json_levels_use_railway_names():
    fmt = JsonFormatter()
    assert json.loads(fmt.format(_record(logging.INFO, "hi")))["level"] == "info"
    assert json.loads(fmt.format(_record(logging.WARNING, "hm")))["level"] == "warn"
    assert json.loads(fmt.format(_record(logging.CRITICAL, "no")))["level"] == "error"


def test_json_keeps_korean_and_traceback_in_one_line():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        line = JsonFormatter().format(_record(logging.ERROR, "실패", sys.exc_info()))
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"].startswith("실패") and "ValueError: boom" in payload["message"]
    assert payload["logger"] == "agent.runtime"
