"""
A scripted fake of the Messages API, for driving the real Agent SDK CLI offline.

Point the CLI at it with ANTHROPIC_BASE_URL. Requests that carry our MCP tools are the agent
loop and are answered from `script` (one list of content blocks per model turn); any other
request (e.g. side calls the CLI makes) gets a trivial text reply.
"""
from __future__ import annotations

import itertools
import json
from typing import Any

from aiohttp import web

AGENT_TOOL_PREFIX = "mcp__fitnudge__"


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def tool_use(name: str, **input_: Any) -> dict[str, Any]:
    return {"type": "tool_use", "name": f"{AGENT_TOOL_PREFIX}{name}", "input": input_}


class FakeMessagesAPI:
    def __init__(self, script: list[list[dict[str, Any]]]) -> None:
        self.script = list(script)
        self.agent_requests: list[dict[str, Any]] = []
        self.other_requests: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/v1/messages", self._messages)
        app.router.add_post("/v1/messages/count_tokens", self._count_tokens)
        app.router.add_route("*", "/{tail:.*}", self._other)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _count_tokens(self, request: web.Request) -> web.Response:
        return web.json_response({"input_tokens": 100})

    async def _other(self, request: web.Request) -> web.Response:
        return web.json_response({})

    async def _messages(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        tool_names = [t.get("name", "") for t in body.get("tools", [])]
        is_agent_loop = any(n.startswith(AGENT_TOOL_PREFIX) for n in tool_names)
        if is_agent_loop:
            self.agent_requests.append(body)
            blocks = self.script.pop(0) if self.script else [text("(script exhausted)")]
        else:
            self.other_requests.append(body)
            blocks = [text("ok")]

        content = []
        for b in blocks:
            if b["type"] == "tool_use":
                content.append({**b, "id": f"toolu_{next(self._ids):04d}"})
            else:
                content.append(b)
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        message = {
            "id": f"msg_{next(self._ids):04d}", "type": "message", "role": "assistant",
            "model": body.get("model", "claude-test"), "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        }
        if not body.get("stream"):
            return web.json_response(message)
        return await self._stream(request, message)

    async def _stream(self, request: web.Request, message: dict[str, Any]) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)

        async def event(name: str, data: dict[str, Any]) -> None:
            await resp.write(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())

        start = {**message, "content": [], "stop_reason": None,
                 "usage": {**message["usage"], "output_tokens": 1}}
        await event("message_start", {"type": "message_start", "message": start})
        for i, block in enumerate(message["content"]):
            if block["type"] == "text":
                await event("content_block_start", {"type": "content_block_start", "index": i,
                                                    "content_block": {"type": "text", "text": ""}})
                await event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                    "delta": {"type": "text_delta", "text": block["text"]}})
            else:
                await event("content_block_start", {"type": "content_block_start", "index": i,
                                                    "content_block": {**block, "input": {}}})
                await event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                    "delta": {"type": "input_json_delta",
                                                              "partial_json": json.dumps(block["input"],
                                                                                         ensure_ascii=False)}})
            await event("content_block_stop", {"type": "content_block_stop", "index": i})
        await event("message_delta", {"type": "message_delta",
                                      "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                                      "usage": {"output_tokens": message["usage"]["output_tokens"]}})
        await event("message_stop", {"type": "message_stop"})
        await resp.write_eof()
        return resp


def tool_results(request_body: dict[str, Any]) -> list[dict[str, Any]]:
    """All tool_result blocks in the last user message (the CLI may append a system message after it)."""
    last = next(m for m in reversed(request_body["messages"]) if m["role"] == "user")
    content = last.get("content", [])
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def result_text(block: dict[str, Any]) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    return " ".join(part.get("text", "") for part in c or [] if isinstance(part, dict))
