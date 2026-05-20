from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import app.services.context_assembler as context_assembler
import app.services.tavily_search_service as tavily_search_service
from app.runtime.manager import RuntimeManager, SessionTurn
from app.providers import TextBlock
import app.services.speech_generation_service as speech_generation_service
import app.services.video_generation_service as video_generation_service


class AssetRuntimeToolTests(IsolatedAsyncioTestCase):
    async def test_list_session_assets_tool_formats_assets(self) -> None:
        runtime = RuntimeManager()
        asset = SimpleNamespace(
            id="asset-1",
            filename="report.pdf",
            kind="pdf",
            status="ready",
        )
        with patch("app.runtime.manager.asset_service.list_assets", return_value=[asset]):
            status, output = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="list_session_assets",
                tool_input={},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(status, "completed")
        self.assertIn("report.pdf", output)
        self.assertIn("asset-1", output)

    async def test_search_asset_chunks_tool_formats_chunk_matches(self) -> None:
        runtime = RuntimeManager()
        asset = SimpleNamespace(
            id="asset-1",
            filename="report.pdf",
            kind="pdf",
            status="ready",
        )
        chunk = SimpleNamespace(
            chunk_index=2,
            page_number=5,
            sheet_name=None,
            slide_number=None,
            content="Quarterly revenue grew by 18 percent year over year.",
        )
        with patch("app.runtime.manager.asset_service.get_asset", return_value=asset), patch(
            "app.runtime.manager.asset_service.search_asset_chunks",
            return_value=[chunk],
        ):
            status, output = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="search_asset_chunks",
                tool_input={"asset_id": "asset-1", "query": "revenue growth", "limit": 3},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(status, "completed")
        self.assertIn("report.pdf", output)
        self.assertIn("chunk_index=2", output)
        self.assertIn("page=5", output)

    async def test_publish_assistant_reply_can_skip_delta_replay(self) -> None:
        runtime = RuntimeManager()
        emitted_events = []

        async def fake_emit(event):
            emitted_events.append(event)
            return event

        async def fake_publish(event):
            emitted_events.append(event)
            return event

        with patch.object(runtime, "emit_ephemeral", side_effect=fake_emit), patch.object(
            runtime, "publish", side_effect=fake_publish
        ), patch("app.runtime.manager.session_service.create_message_record"), patch(
            "app.runtime.manager.memory_service.remember_progress"
        ), patch("app.runtime.manager.memory_service.refresh_rolling_summary"), patch(
            "app.runtime.manager.RuntimeManager._capture_assistant_memory_signals"
        ):
            await runtime._publish_assistant_reply(
                "session-1",
                "Final streamed answer",
                source_turn_id=12,
                emit_deltas=False,
            )

        self.assertFalse(any(event.type == "message.assistant.delta" for event in emitted_events))
        self.assertTrue(any(event.type == "message.assistant" for event in emitted_events))

    async def test_timeline_message_content_does_not_append_attachment_names_to_prompt(self) -> None:
        runtime = RuntimeManager()
        asset = SimpleNamespace(filename="中文计划.pdf")
        with patch("app.runtime.manager.asset_service.get_asset", return_value=asset):
            content = runtime._timeline_message_content(
                "session-1",
                SimpleNamespace(content="请总结这个文档", asset_ids=["asset-1"]),
            )
        self.assertEqual(content, "请总结这个文档")

    async def test_stream_agent_response_coalesces_small_deltas(self) -> None:
        runtime = RuntimeManager()
        cancel_event = __import__("asyncio").Event()
        emitted = []

        class DummyClient:
            def stream_response(self, **kwargs):
                yield {"type": "text_delta", "delta": "Hel"}
                yield {"type": "text_delta", "delta": "lo "}
                yield {"type": "text_delta", "delta": "world."}
                yield {"type": "done"}

        async def fake_emit(event):
            emitted.append(event)
            return event

        runtime.session_turns["session-1"] = SessionTurn(
            turn_id=1,
            task=SimpleNamespace(done=lambda: False),
            cancel_event=cancel_event,
        )
        with patch.object(runtime, "emit_ephemeral", side_effect=fake_emit):
            blocks = await runtime._stream_agent_response(
                client=DummyClient(),
                session_id="session-1",
                turn_id=1,
                system_prompt="You are Jarvis.",
                messages=[],
                tools=[],
                cancel_event=cancel_event,
                emit_stream_events=True,
            )

        self.assertEqual(blocks[0].text, "Hello world.")
        self.assertEqual([event.content for event in emitted], ["Hello world."])

    async def test_generate_speech_tool_surfaces_service_errors(self) -> None:
        runtime = RuntimeManager()
        with patch(
            "app.runtime.manager.speech_generation_service.generate_speech",
            side_effect=speech_generation_service.SpeechGenerationError("tts provider is not configured"),
        ):
            status, output = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="generate_speech",
                tool_input={"text": "Speak this reply"},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(status, "error")
        self.assertIn("not configured", output)

    async def test_generate_video_tool_surfaces_service_errors(self) -> None:
        runtime = RuntimeManager()
        with patch(
            "app.runtime.manager.video_generation_service.submit_video_generation",
            side_effect=video_generation_service.VideoGenerationError("video provider is not configured"),
        ):
            status, output = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="generate_video",
                tool_input={"prompt": "Make a short demo clip"},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(status, "error")
        self.assertIn("not configured", output)

    async def test_generate_speech_tool_returns_generated_asset_ids(self) -> None:
        runtime = RuntimeManager()
        generated_asset = SimpleNamespace(
            id="asset-tts-1",
            filename="generated-speech.wav",
            kind="generated_audio",
            origin="generated",
            status="ready",
            preview_path=None,
            storage_path="/tmp/generated-speech.wav",
            source_asset_id=None,
            metadata_json={"provider": "fake-tts"},
        )
        generated_result = SimpleNamespace(
            asset=generated_asset,
            provider_name="fake-tts",
        )
        with patch(
            "app.runtime.manager.speech_generation_service.generate_speech",
            return_value=generated_result,
        ), patch(
            "app.runtime.manager.asset_service.build_asset_reference",
            return_value={"type": "asset_ref", "asset_id": "asset-tts-1", "kind": "generated_audio"},
        ):
            result = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="generate_speech",
                tool_input={"text": "Speak this reply", "format": "wav"},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payload["asset_ids"], ["asset-tts-1"])

    async def test_continue_agent_loop_reprompts_once_after_empty_model_response(self) -> None:
        runtime = RuntimeManager()

        with patch("app.runtime.manager.create_client", return_value=SimpleNamespace()), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            side_effect=[[], [TextBlock(text="Final answer")]],
        ) as stream_mock, patch.object(
            context_assembler,
            "assemble_context",
            return_value=context_assembler.AssembledContext(
                system_prompt="You are Jarvis.",
                messages=[],
                debug_meta={},
            ),
        ), patch.object(
            runtime,
            "_write_checkpoint",
            return_value=None,
        ):
            reply = await runtime._continue_agent_loop(
                "session-1",
                __import__("pathlib").Path("/tmp"),
                [{"role": "user", "content": "Summarize the repository"}],
                __import__("asyncio").Event(),
            )

        self.assertEqual(reply.text, "Final answer")
        self.assertEqual(stream_mock.await_count, 2)

    async def test_continue_agent_loop_requires_change_or_blocker_for_code_change_tasks(self) -> None:
        runtime = RuntimeManager()

        with patch("app.runtime.manager.create_client", return_value=SimpleNamespace()), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            side_effect=[[TextBlock(text="I inspected the file and found the bug.")], [TextBlock(text="Blocked because the workspace is read-only.")]],
        ) as stream_mock, patch.object(
            context_assembler,
            "assemble_context",
            return_value=context_assembler.AssembledContext(
                system_prompt="You are Jarvis.",
                messages=[],
                debug_meta={},
            ),
        ), patch.object(
            runtime,
            "_write_checkpoint",
            return_value=None,
        ):
            reply = await runtime._continue_agent_loop(
                "session-1",
                __import__("pathlib").Path("/tmp"),
                [{"role": "user", "content": "Fix the bug in foo.py"}],
                __import__("asyncio").Event(),
            )

        self.assertIn("Blocked", reply.text)
        self.assertEqual(stream_mock.await_count, 2)

    async def test_continue_agent_loop_requires_verification_after_write(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "Fix the bug in foo.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "edit_file", "input": {"path": "foo.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "edit_file", "status": "completed", "content": "Edited foo.py"}]},
        ]

        with patch("app.runtime.manager.create_client", return_value=SimpleNamespace()), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            side_effect=[[TextBlock(text="I changed the file.")], [TextBlock(text="Unable to run tests because no tests exist.")]],
        ) as stream_mock, patch.object(
            context_assembler,
            "assemble_context",
            return_value=context_assembler.AssembledContext(
                system_prompt="You are Jarvis.",
                messages=[],
                debug_meta={},
            ),
        ), patch.object(
            runtime,
            "_write_checkpoint",
            return_value=None,
        ):
            reply = await runtime._continue_agent_loop(
                "session-1",
                __import__("pathlib").Path("/tmp"),
                messages,
                __import__("asyncio").Event(),
            )

        self.assertIn("Unable to run tests", reply.text)
        self.assertEqual(stream_mock.await_count, 2)

    async def test_execute_autonomous_tool_web_search_serializes_service_results(self) -> None:
        runtime = RuntimeManager()
        response = tavily_search_service.TavilySearchResponse(
            query="today cavaliers knicks final score",
            searched_at="2026-05-20T00:00:00+00:00",
            evidence_quality="strong",
            results=[
                tavily_search_service.TavilySearchItem(
                    title="Knicks vs Cavaliers Box Score",
                    url="https://www.nba.com/game/1",
                    score=0.93,
                    snippet="Final: Knicks 108, Cavaliers 101",
                    raw_excerpt="Final: Knicks 108, Cavaliers 101",
                )
            ],
            suggested_answer="Top evidence indicates: Final: Knicks 108, Cavaliers 101",
            include_domains=["nba.com"],
        )
        with patch("app.runtime.manager.tavily_search_service.search_web", return_value=response):
            status, output = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="web_search",
                tool_input={"query": "today cavaliers knicks final score"},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(status, "completed")
        payload = json.loads(output)
        self.assertEqual(payload["evidence_quality"], "strong")
        self.assertEqual(payload["results"][0]["url"], "https://www.nba.com/game/1")

    async def test_execute_autonomous_tool_web_search_surfaces_service_errors(self) -> None:
        runtime = RuntimeManager()
        with patch(
            "app.runtime.manager.tavily_search_service.search_web",
            side_effect=tavily_search_service.TavilySearchError("Web search is not configured."),
        ):
            status, output = await runtime._execute_autonomous_tool(
                session_id="session-1",
                tool_name="web_search",
                tool_input={"query": "today cavaliers knicks final score"},
                broker_for_workspace=SimpleNamespace(),
            )
        self.assertEqual(status, "error")
        self.assertIn("not configured", output)

    def test_completion_gate_requires_web_search_for_time_sensitive_prompt(self) -> None:
        runtime = RuntimeManager()
        followup = runtime._completion_gate_followup(
            messages=[{"role": "user", "content": "What is today's Cavaliers vs Knicks final score?"}],
            final_text="The Cavaliers beat the Knicks 101-99.",
            require_change_followup_used=False,
            require_verification_followup_used=False,
            require_web_search_followup_used=False,
            require_weak_evidence_followup_used=False,
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertIn("web_search", followup[0])

    def test_completion_gate_requires_uncertainty_for_weak_web_search_evidence(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "What is today's Cavaliers vs Knicks final score?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "web_search", "input": {"query": "today cavaliers knicks final score"}}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "tool_name": "web_search",
                        "status": "completed",
                        "content": json.dumps({"evidence_quality": "weak", "results": []}),
                    }
                ],
            },
        ]
        followup = runtime._completion_gate_followup(
            messages=messages,
            final_text="The Cavaliers beat the Knicks 101-99.",
            require_change_followup_used=False,
            require_verification_followup_used=False,
            require_web_search_followup_used=True,
            require_weak_evidence_followup_used=False,
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertIn("uncertain", followup[0].lower())

    def test_build_agent_system_prompt_includes_code_change_contract(self) -> None:
        runtime = RuntimeManager()
        prompt = runtime._build_agent_system_prompt(
            __import__("pathlib").Path("/tmp"),
            execution_mode="normal",
            requires_code_change=True,
        )
        self.assertIn("Code-change execution contract:", prompt)
        self.assertIn("apply_patch", prompt)
        self.assertIn("run_test", prompt)
