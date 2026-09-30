from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.bridge import Bridge, EchoBackend
from bridge.transport import DirectoryTransport


class BridgeEchoTests(unittest.TestCase):
    def test_echo_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, EchoBackend())
            transport.write("request.tns", "Hello\n第二行".encode("utf-8"))
            transport.write("request.id.tns", b"42")
            self.assertEqual(bridge.step(), "42")
            self.assertEqual(transport.read("response.id.tns"), b"42")
            self.assertEqual(transport.read("response.tns"), "Mac received: Hello\n第二行".encode("utf-8"))
            self.assertIsNone(bridge.step())

    def test_long_text_and_repeated_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, EchoBackend())
            prompt = "x" * 10000
            transport.write("request.tns", prompt.encode())
            transport.write("request.id.tns", b"1")
            self.assertEqual(bridge.step(), "1")
            transport.write("request.tns", b"new")
            transport.write("request.id.tns", b"2")
            self.assertEqual(bridge.step(), "2")
            self.assertEqual(transport.read("response.tns"), b"Mac received: new")

    def test_oversized_response_is_rejected_before_calculator_limit(self):
        class HugeBackend:
            def reset(self):
                pass

            def answer(self, prompt):
                return "x" * (256 * 1024 + 1)

        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, HugeBackend())
            transport.write("request.tns", b"short")
            transport.write("request.id.tns", b"1-1")
            self.assertEqual(bridge.step(), "1-1")
            self.assertLess(len(transport.read("response.tns")), 256 * 1024)

    def test_incomplete_request_does_not_publish_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            bridge = Bridge(transport, EchoBackend())
            transport.write("request.id.tns", b"7")
            self.assertIsNone(bridge.step())
            self.assertIsNone(transport.read("response.id.tns"))

    def test_conversation_namespace_resets_backend(self):
        class RecordingBackend(EchoBackend):
            def __init__(self):
                self.reset_count = 0

            def reset(self):
                self.reset_count += 1

        with tempfile.TemporaryDirectory() as tmp:
            transport = DirectoryTransport(Path(tmp))
            backend = RecordingBackend()
            bridge = Bridge(transport, backend)
            for request_id in (b"1-1", b"1-2", b"2-1"):
                transport.write("request.tns", b"hello")
                transport.write("request.id.tns", request_id)
                self.assertIsNotNone(bridge.step())
            self.assertEqual(backend.reset_count, 2)

    def test_failed_ready_marker_upload_does_not_repeat_backend(self):
        class FlakyTransport(DirectoryTransport):
            def __init__(self, root):
                super().__init__(root)
                self.fail_ready_once = True

            def write(self, name, data):
                if name == "response.id.tns" and self.fail_ready_once:
                    self.fail_ready_once = False
                    raise OSError("simulated USB disconnect")
                return super().write(name, data)

        class CountingBackend(EchoBackend):
            def __init__(self):
                self.calls = 0

            def answer(self, prompt):
                self.calls += 1
                return super().answer(prompt)

        with tempfile.TemporaryDirectory() as tmp:
            transport = FlakyTransport(Path(tmp))
            backend = CountingBackend()
            bridge = Bridge(transport, backend)
            transport.write("request.tns", b"once")
            transport.write("request.id.tns", b"1-1")
            with self.assertRaises(OSError):
                bridge.step()
            self.assertEqual(backend.calls, 1)
            self.assertEqual(bridge.step(), "1-1")
            self.assertEqual(backend.calls, 1)




class FakeCall:
    def __init__(self, identifier, name, arguments):
        self.id = identifier
        self.function = type("Function", (), {"name": name, "arguments": arguments})()


class FakeMessage:
    def __init__(self, content=None, tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls
        if reasoning is not None:
            self.reasoning_content = reasoning


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        message = self.replies.pop(0) if self.replies else FakeMessage(
            tool_calls=[FakeCall("again", "web_search", '{"query": "more"}')])
        return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()


class FakeTools:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def describe(self, name, arguments):
        return f"{name} {arguments}"

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return f"result {len(self.calls)}"


def chat_backend(replies):
    from bridge.bridge import ChatCompletionsBackend

    backend = ChatCompletionsBackend.__new__(ChatCompletionsBackend)
    backend.model = "test-model"
    backend.messages = []
    completions = FakeCompletions(replies)
    backend.client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return backend, completions


class ToolLoopTests(unittest.TestCase):
    QUESTION = [{"role": "system", "content": "s"}, {"role": "user", "content": "news?"}]

    def test_without_tools_the_request_is_unchanged(self):
        backend, completions = chat_backend([FakeMessage(" plain ")])
        self.assertEqual(backend.complete(self.QUESTION, effort="off"), "plain")
        self.assertNotIn("tools", completions.requests[0])
        self.assertEqual(completions.requests[0]["messages"], self.QUESTION)

    def test_tool_results_go_back_with_the_reasoning(self):
        backend, completions = chat_backend([
            FakeMessage("", [FakeCall("c1", "web_search", '{"query": "news"}'),
                             FakeCall("c2", "open_url", '{"url": "https://example.com/"}')],
                        reasoning="I should look this up"),
            FakeMessage("The answer."),
        ])
        tools, notes = FakeTools(), []
        answer = backend.complete(self.QUESTION, effort="high", tools=tools, progress=notes.append)
        self.assertEqual(answer, "The answer.")
        self.assertEqual(tools.calls, [("web_search", '{"query": "news"}'),
                                       ("open_url", '{"url": "https://example.com/"}')])
        self.assertEqual(len(notes), 2)
        first, second = completions.requests
        self.assertEqual(first["tool_choice"], "auto")
        self.assertEqual(first["reasoning_effort"], "high")
        self.assertEqual([tool["function"]["name"] for tool in first["tools"]],
                         ["web_search", "open_url"])
        self.assertIn("Today is 20", first["tools"][0]["function"]["description"])
        self.assertEqual(second["messages"][:2], self.QUESTION)
        turn = second["messages"][2]
        self.assertEqual(turn["role"], "assistant")
        self.assertEqual(turn["reasoning_content"], "I should look this up")
        self.assertEqual([call["id"] for call in turn["tool_calls"]], ["c1", "c2"])
        self.assertEqual(second["messages"][3:], [
            {"role": "tool", "tool_call_id": "c1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "c2", "content": "result 2"},
        ])
        self.assertEqual(self.QUESTION[-1], {"role": "user", "content": "news?"})  # not modified

    def test_a_model_that_never_stops_searching_is_made_to_answer(self):
        from bridge.bridge import MAX_TOOL_ROUNDS

        backend, completions = chat_backend([])   # every reply asks for another search
        tools = FakeTools()
        backend.complete(self.QUESTION, tools=tools)
        self.assertEqual(len(tools.calls), MAX_TOOL_ROUNDS)
        self.assertEqual(len(completions.requests), MAX_TOOL_ROUNDS + 1)
        self.assertEqual(completions.requests[-1]["tool_choice"], "none")

    def test_a_failing_progress_note_does_not_cost_the_answer(self):
        backend, _completions = chat_backend([
            FakeMessage("", [FakeCall("c1", "web_search", "{}")]), FakeMessage("ok")])

        def broken(note):
            raise RuntimeError("the page went away")

        self.assertEqual(backend.complete(self.QUESTION, tools=FakeTools(), progress=broken), "ok")

    def test_echo_backend_exercises_the_tools(self):
        from bridge.bridge import EchoBackend

        tools, notes = FakeTools(), []
        answer = EchoBackend().complete([{"role": "user", "content": "search ti nspire"}],
                                        tools=tools, progress=notes.append)
        self.assertEqual(tools.calls, [("web_search", '{"query": "ti nspire"}')])
        self.assertIn("result 1", answer)
        self.assertEqual(len(notes), 1)
        self.assertIn("Echo", EchoBackend().complete([{"role": "user", "content": "search x"}]))


if __name__ == "__main__":
    unittest.main()
