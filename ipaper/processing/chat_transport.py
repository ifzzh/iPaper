"""One streaming request in a short-lived process, with a wall-clock deadline."""

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from .common import ProcessingError, encoded


def stream_request(profile, messages, output_limit=4096, deadline=120):
    payload = encoded(
        {"profile": profile, "messages": messages, "outputLimit": output_limit}
    ).encode()
    if len(payload) > 256 * 1024:
        raise ProcessingError("model_input_too_large", 413)
    process = subprocess.Popen(
        [sys.executable, "-m", "ipaper.processing.chat_transport"],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    stop = time.monotonic() + deadline
    selector = selectors.DefaultSelector()
    buffer = b""
    total = 0
    completed = False
    try:
        process.stdin.write(payload)
        process.stdin.close()
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < stop:
            if not selector.select(min(0.25, max(0, stop - time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            buffer += chunk
            total += len(chunk)
            if total > 1024**2 or len(buffer) > 256 * 1024:
                raise ProcessingError("model_output_too_large", 502)
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                value = json.loads(line)
                if value.get("type") == "text":
                    yield value["text"]
                elif value.get("type") == "done":
                    completed = True
                else:
                    raise ProcessingError(
                        value.get("error", "model_result_unknown"), 502
                    )
        if not completed:
            raise ProcessingError("model_result_unknown", 502)
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
        process.stdout.close()


def main():
    from openai import OpenAI, DefaultHttpxClient, APIStatusError
    from .translation import generation_options, _request_clock, _bound_response

    def emit(value):
        sys.stdout.write(encoded(value) + "\n")
        sys.stdout.flush()

    try:
        raw = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError()
        value = json.loads(raw)
        profile = value["profile"]
        with OpenAI(
            api_key=profile["key"],
            base_url=profile["baseUrl"],
            timeout=120,
            max_retries=0,
            http_client=DefaultHttpxClient(
                follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
                event_hooks={
                    "request": [_request_clock],
                    "response": [_bound_response],
                },
            ),
        ) as client:
            result = client.chat.completions.create(
                model=profile["model"],
                messages=value["messages"],
                stream=True,
                max_tokens=value["outputLimit"],
                **generation_options(profile["model"]),
            )
            length = 0
            finished = False
            for chunk in result:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason == "length":
                    emit({"type": "error", "error": "model_output_incomplete"})
                    return
                if choice.finish_reason == "stop":
                    finished = True
                text = choice.delta.content
                if isinstance(text, str) and text:
                    length += len(text.encode())
                    if length > 256 * 1024:
                        raise ValueError()
                    emit({"type": "text", "text": text})
            if not finished or not length:
                raise ValueError()
            emit({"type": "done"})
    except APIStatusError:
        emit({"type": "error", "error": "model_request_rejected"})
    except Exception:
        emit({"type": "error", "error": "model_result_unknown"})


if __name__ == "__main__":
    main()
