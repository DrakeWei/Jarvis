from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.mcp import ToolDefinition
import app.services.context_assembler as context_assembler
import app.services.tavily_search_service as tavily_search_service
from app.runtime.manager import ReflectionDecision, RuntimeManager, SessionTurn
from app.providers import TextBlock, ToolUseBlock
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

    async def test_continue_agent_loop_persists_reflection_and_checkpoints(self) -> None:
        runtime = RuntimeManager()
        checkpoint_phases: list[str] = []
        checkpoint_contexts: dict[str, dict[str, object] | None] = {}

        def fake_write_checkpoint(**kwargs):
            checkpoint_phases.append(kwargs["phase"])
            checkpoint_contexts[kwargs["phase"]] = kwargs.get("extra_context")
            return 41 if kwargs["phase"] == "after_reflection" else None

        with patch("app.runtime.manager.create_client", return_value=SimpleNamespace()), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            return_value=[TextBlock(text="All done.")],
        ), patch.object(
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
            side_effect=fake_write_checkpoint,
        ), patch.object(
            runtime,
            "_run_reflection",
            return_value=ReflectionDecision(
                verdict="done",
                reason_codes=[],
                next_action_prompt="",
                summary="Ready to finalize.",
            ),
        ), patch(
            "app.runtime.manager.reflection_service.create_reflection"
        ) as reflection_create:
            reply = await runtime._continue_agent_loop(
                "session-1",
                __import__("pathlib").Path("/tmp"),
                [{"role": "user", "content": "Summarize the repository"}],
                __import__("asyncio").Event(),
                turn_id=7,
            )

        self.assertEqual(reply.text, "All done.")
        self.assertIn("before_reflection", checkpoint_phases)
        self.assertIn("after_reflection", checkpoint_phases)
        self.assertIn("reviewer_packet", checkpoint_contexts["after_reflection"] or {})
        reflection_create.assert_called_once()

    async def test_continue_agent_loop_preserves_completion_summary_and_appends_verification_result(self) -> None:
        runtime = RuntimeManager()

        with patch("app.runtime.manager.create_client", return_value=SimpleNamespace()), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[ToolDefinition(name="run_test", description="run", input_schema={"type": "object"}, source="local")],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            side_effect=[
                [TextBlock(text="已实现 URL 批量检测脚本，位于 `url_batch_checker.py`。")],
                [ToolUseBlock(id="call-1", name="run_test", input={"argv": ["python3", "url_batch_checker.py", "--help"]})],
                [TextBlock(text="已验证：`python3 url_batch_checker.py --help` 运行成功，退出码 0，脚本可正常启动并显示参数说明。")],
            ],
        ), patch.object(
            context_assembler,
            "assemble_context",
            return_value=context_assembler.AssembledContext(
                system_prompt="You are Jarvis.",
                messages=[],
                debug_meta={},
            ),
        ), patch.object(
            runtime,
            "_execute_tool_definition",
            return_value=SimpleNamespace(
                status="completed",
                output="exit_code=0\nusage: url_batch_checker.py --help",
                remote_request_id=None,
                payload={"classification": "verification", "verification_kind": "script_run", "evidence_strength": "sufficient", "wrong_environment": False},
            ),
        ), patch.object(
            runtime,
            "_write_checkpoint",
            return_value=None,
        ), patch("app.runtime.manager.tool_service.create_tool_execution"), patch.object(
            runtime,
            "publish",
        ), patch.object(
            runtime,
            "_run_reflection",
            side_effect=[
                ReflectionDecision(
                    verdict="continue_with_verification",
                    reason_codes=["verification_gap"],
                    next_action_prompt="Run verification before finalizing.",
                    summary="Need verification.",
                ),
                ReflectionDecision(
                    verdict="done",
                    reason_codes=[],
                    next_action_prompt="",
                    summary="Ready to finalize.",
                ),
            ],
        ):
            reply = await runtime._continue_agent_loop(
                "session-1",
                __import__("pathlib").Path("/tmp"),
                [{"role": "user", "content": "实现一个 URL 批量检测脚本"}],
                __import__("asyncio").Event(),
            )

        self.assertIn("已实现 URL 批量检测脚本", reply.text)
        self.assertIn("已验证：`python3 url_batch_checker.py --help` 运行成功", reply.text)

    def test_reflection_followup_prompt_requires_execution_and_preserves_summary(self) -> None:
        runtime = RuntimeManager()
        prompt = runtime._reflection_followup_prompt(
            ReflectionDecision(
                verdict="continue_with_verification",
                reason_codes=["verification_gap"],
                next_action_prompt="Use run_test for one stronger verification step.",
                summary="Need verification.",
            ),
            "已实现 URL 批量检测脚本，位于 `url_batch_checker.py`。",
        )

        self.assertIn("Do not reply with review comments", prompt)
        self.assertIn("must either call the necessary tool", prompt)
        self.assertIn("Keep this existing user-facing result summary", prompt)
        self.assertIn("已实现 URL 批量检测脚本", prompt)

    async def test_continue_agent_loop_injects_progress_followup_after_repeated_read_only_batches(self) -> None:
        runtime = RuntimeManager()
        captured_messages: list[list[dict[str, object]]] = []

        def fake_assemble_context(**kwargs):
            messages = kwargs["messages"]
            captured_messages.append(messages.copy())
            return context_assembler.AssembledContext(
                system_prompt="You are Jarvis.",
                messages=messages,
                debug_meta={},
            )

        tool_batches = [
            [ToolUseBlock(id="call-1", name="list_files", input={"path": "."})],
            [ToolUseBlock(id="call-2", name="search_text", input={"query": "crawler"})],
            [ToolUseBlock(id="call-3", name="read_file", input={"path": "README.md"})],
            [ToolUseBlock(id="call-4", name="show_status", input={})],
            [TextBlock(text="Blocked because the destination directory is read-only.")],
            [TextBlock(text="Blocked because the destination directory is read-only.")],
        ]

        with patch("app.runtime.manager.create_client", return_value=SimpleNamespace()), patch.object(
            runtime,
            "_autonomous_tool_definitions",
            return_value=[
                ToolDefinition(name="list_files", description="list", input_schema={"type": "object"}, source="local"),
                ToolDefinition(name="search_text", description="search", input_schema={"type": "object"}, source="local"),
                ToolDefinition(name="read_file", description="read", input_schema={"type": "object"}, source="local"),
                ToolDefinition(name="show_status", description="status", input_schema={"type": "object"}, source="local"),
            ],
        ), patch.object(
            runtime,
            "_stream_agent_response",
            side_effect=tool_batches,
        ) as stream_mock, patch.object(
            context_assembler,
            "assemble_context",
            side_effect=fake_assemble_context,
        ), patch.object(
            runtime,
            "_execute_tool_definition",
            return_value=SimpleNamespace(status="completed", output="ok", remote_request_id=None, payload=None),
        ), patch.object(
            runtime,
            "_write_checkpoint",
            return_value=None,
        ), patch("app.runtime.manager.tool_service.create_tool_execution"), patch.object(
            runtime,
            "publish",
        ):
            reply = await runtime._continue_agent_loop(
                "session-1",
                __import__("pathlib").Path("/tmp"),
                [{"role": "user", "content": "在当前目录下实现一个简易的Python爬虫脚本"}],
                __import__("asyncio").Event(),
            )

        self.assertIn("Blocked because", reply.text)
        self.assertGreaterEqual(stream_mock.await_count, 5)
        self.assertTrue(
            any(
                any(
                    message.get("role") == "user"
                    and isinstance(message.get("content"), str)
                    and "Stop exploring and make concrete progress now." in message["content"]
                    for message in message_batch
                )
                for message_batch in captured_messages
            )
        )

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

    def test_run_reflection_requires_web_search_for_time_sensitive_prompt(self) -> None:
        runtime = RuntimeManager()
        decision = runtime._run_reflection(
            messages=[{"role": "user", "content": "What is today's Cavaliers vs Knicks final score?"}],
            final_text="The Cavaliers beat the Knicks 101-99.",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_verification")
        self.assertIn("missing_fresh_evidence", decision.reason_codes)
        self.assertIn("web_search", decision.next_action_prompt)

    def test_task_requires_web_search_ignores_current_directory_code_change_prompt(self) -> None:
        runtime = RuntimeManager()
        self.assertFalse(
            runtime._task_requires_web_search(
                [{"role": "user", "content": "在当前目录下实现一个简易的Python爬虫脚本"}]
            )
        )

    def test_latest_request_text_skips_runtime_web_search_followup(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "在这个目录下用Python实现一个简易的MNIST手写识别体。"},
            {
                "role": "user",
                "content": (
                    "The user asked for a time-sensitive external fact. "
                    "Call web_search before finalizing your answer. "
                    "If web_search fails or returns limited evidence, you may give a best guess only if you state that the answer is uncertain."
                ),
            },
        ]
        self.assertEqual(runtime._latest_request_text(messages), "在这个目录下用Python实现一个简易的MNIST手写识别体。")

    def test_original_goal_text_ignores_runtime_verification_followup(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "Fix the bug in foo.py"},
            {"role": "assistant", "content": "I changed the file."},
            {
                "role": "user",
                "content": (
                    "Run one stronger verification step that exercises the changed code path, "
                    "not just syntax validation, and report the concrete outcome."
                ),
            },
            {"role": "user", "content": "继续"},
        ]
        self.assertEqual(runtime._original_goal_text(messages), "Fix the bug in foo.py")

    def test_build_verification_review_packet_marks_read_only_analysis_as_soft(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "总结一下这个仓库结构"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "list_files", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "list_files", "status": "completed", "content": "README.md\nbackend/app/runtime/manager.py"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-2", "name": "read_file", "input": {"path": "README.md"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-2", "tool_name": "read_file", "status": "completed", "content": "# Jarvis"}]},
        ]

        packet = runtime._build_verification_review_packet(
            messages=messages,
            final_text="仓库分成前后端和运行时两部分。",
            remaining_auto_verify_attempts=1,
        )

        self.assertEqual(packet.task_profile.verify_level, "soft")
        self.assertEqual(packet.task_profile.completion_mode, "evidence_check")
        self.assertIn("read_only_analysis", packet.task_profile.task_kinds)

    def test_run_reflection_rejects_task_misaligned_generic_final_answer(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "在这个目录下用Python实现一个简易的MNIST手写识别体。"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "write_file", "input": {"path": "simple_mnist.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "write_file", "status": "completed", "content": "Wrote 100 bytes to /tmp/simple_mnist.py"}]},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call-2", "name": "run_test", "input": {"argv": ["python3", "simple_mnist.py", "--help"]}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-2",
                        "tool_name": "run_test",
                        "status": "completed",
                        "content": "exit_code=0",
                        "payload": {
                            "classification": "verification",
                            "verification_kind": "script_run",
                            "evidence_strength": "sufficient",
                            "wrong_environment": False,
                        },
                    }
                ],
            },
        ]
        decision = runtime._run_reflection(
            messages=messages,
            final_text="明白。之后如果用户询问这类时间敏感的外部事实，我会先查询最新信息再作答；若证据不足，我会明确说明不确定。",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_verification")
        self.assertIn("task_misalignment", decision.reason_codes)

    def test_run_reflection_requires_uncertainty_for_weak_web_search_evidence(self) -> None:
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
        decision = runtime._run_reflection(
            messages=messages,
            final_text="The Cavaliers beat the Knicks 101-99.",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_verification")
        self.assertIn("missing_fresh_evidence", decision.reason_codes)
        self.assertIn("uncertain", decision.next_action_prompt.lower())

    def test_run_reflection_requires_verification_after_write(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "Fix the bug in foo.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "edit_file", "input": {"path": "foo.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "edit_file", "status": "completed", "content": "Edited foo.py"}]},
        ]
        decision = runtime._run_reflection(
            messages=messages,
            final_text="I changed the file.",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_verification")
        self.assertIn("verification_gap", decision.reason_codes)
        self.assertIn("run_test", decision.next_action_prompt)

    def test_run_reflection_does_not_accept_weak_syntax_only_verification(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "Fix the bug in foo.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "edit_file", "input": {"path": "foo.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "edit_file", "status": "completed", "content": "Edited foo.py"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-2", "name": "run_test", "input": {"argv": ["python3", "-m", "py_compile", "foo.py"]}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-2", "tool_name": "run_test", "status": "completed", "content": "exit_code=0", "payload": {"classification": "verification", "verification_kind": "syntax_check", "evidence_strength": "weak", "wrong_environment": False}}]},
        ]
        decision = runtime._run_reflection(
            messages=messages,
            final_text="I fixed the bug and verified it.",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_verification")
        self.assertIn("verification_gap", decision.reason_codes)

    def test_run_reflection_blocks_repeated_weak_verification_loop(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "Fix the bug in foo.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "edit_file", "input": {"path": "foo.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "edit_file", "status": "completed", "content": "Edited foo.py"}]},
        ]
        for index in range(3):
            messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": f"call-v{index}", "name": "run_test", "input": {"argv": ["python3", "-m", "py_compile", "foo.py"]}}],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"call-v{index}",
                            "tool_name": "run_test",
                            "status": "completed",
                            "content": "exit_code=0",
                            "payload": {
                                "classification": "verification",
                                "verification_kind": "syntax_check",
                                "evidence_strength": "weak",
                                "wrong_environment": False,
                            },
                        }
                    ],
                }
            )
        decision = runtime._run_reflection(
            messages=messages,
            final_text="I fixed it.",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "blocked_uncertain")
        self.assertIn("verification_stalled", decision.reason_codes)

    def test_run_reflection_blocks_when_retry_budget_is_exhausted(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "Fix the bug in foo.py"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "edit_file", "input": {"path": "foo.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "edit_file", "status": "completed", "content": "Edited foo.py"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-2", "name": "run_test", "input": {"argv": ["python3", "-m", "py_compile", "foo.py"]}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-2", "tool_name": "run_test", "status": "completed", "content": "exit_code=0", "payload": {"classification": "verification", "verification_kind": "syntax_check", "evidence_strength": "weak", "wrong_environment": False}}]},
        ]
        decision = runtime._run_reflection(
            messages=messages,
            final_text="I fixed the bug and did a basic syntax check.",
            agent_kind="lead",
            execution_mode="normal",
            remaining_auto_verify_attempts=0,
        )
        self.assertEqual(decision.verdict, "blocked_uncertain")
        self.assertIn("blocked_uncertain", decision.reason_codes)

    def test_run_reflection_requires_dependency_install_verification_after_requirements_edit(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "安装一下对应依赖"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "edit_file", "input": {"path": "requirements.txt"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "tool_name": "edit_file", "status": "completed", "content": "Edited requirements.txt"}]},
        ]
        decision = runtime._run_reflection(
            messages=messages,
            final_text="已把 pypdf>=4.0.0 加到 requirements.txt。",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_verification")
        self.assertIn("verification_gap", decision.reason_codes)

    def test_run_reflection_repairs_dependency_install_claim_with_wrong_environment_evidence(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "那你直接用pip install帮我安装吧"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "call-1", "name": "run_test", "input": {"argv": ["python3", "-m", "pip", "show", "pypdf"]}}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "tool_name": "run_test",
                        "status": "completed",
                        "content": "exit_code=0",
                        "payload": {
                            "classification": "verification",
                            "verification_kind": "package_probe",
                            "evidence_strength": "sufficient",
                            "wrong_environment": True,
                        },
                    }
                ],
            },
        ]
        decision = runtime._run_reflection(
            messages=messages,
            final_text="已经装好了。",
            agent_kind="lead",
            execution_mode="normal",
        )
        self.assertEqual(decision.verdict, "continue_with_repair")
        self.assertEqual(decision.next_phase, "repair")
        self.assertIn("verification_gap", decision.reason_codes)

    async def test_resume_turn_from_after_reflection_blocked_context_returns_blocker(self) -> None:
        runtime = RuntimeManager()
        reply = await runtime._resume_turn_from_context(
            "session-1",
            12,
            {
                "workspace": "/tmp",
                "messages": [{"role": "user", "content": "Fix the bug in foo.py"}],
                "allowed_external_reads": [],
                "write_enabled": True,
                "allow_subagent_tool": True,
                "agent_kind": "lead",
                "emit_stream_events": True,
                "execution_mode": "normal",
                "reflection": {
                    "verdict": "blocked",
                    "reason_codes": ["missing_edit"],
                    "next_action_prompt": "",
                    "summary": "The requested code change could not be completed because the workspace is read-only.",
                },
                "reflection_final_text": "I am blocked because the workspace is read-only.",
            },
            __import__("asyncio").Event(),
        )
        self.assertIn("workspace is read-only", reply.text)

    def test_latest_request_text_skips_continuation_only_messages(self) -> None:
        runtime = RuntimeManager()
        messages = [
            {"role": "user", "content": "在当前目录下实现一个简易的Python爬虫脚本"},
            {"role": "assistant", "content": "任务执行达到了安全迭代上限。"},
            {"role": "user", "content": "继续"},
        ]
        self.assertEqual(runtime._latest_request_text(messages), "在当前目录下实现一个简易的Python爬虫脚本")
        self.assertTrue(runtime._task_requires_code_change(messages))

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
        self.assertIn("standalone script or utility", prompt)
