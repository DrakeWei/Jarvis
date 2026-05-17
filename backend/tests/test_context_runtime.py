from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import app.services.context_assembler as context_assembler
import app.services.context_budget as context_budget
import app.services.conversation_search_service as conversation_search_service
import app.services.memory_retriever as memory_retriever
import app.services.memory_search_service as memory_search_service
import app.services.asset_service as asset_service
import app.providers.openai_adapter as openai_adapter
from app.runtime.manager import RuntimeManager


class ContextBudgetTests(TestCase):
    def test_build_tool_result_summary_shortens_long_output(self) -> None:
        content = "\n".join(f"/tmp/file_{index}.txt line {index}" for index in range(40))
        summary = context_budget.build_tool_result_summary(
            tool_name="read_file",
            content=content,
            limit=180,
        )
        self.assertIn("Compacted read_file result", summary)
        self.assertLessEqual(len(summary), 180)

    def test_compact_tool_result_messages_preserves_tool_use_id(self) -> None:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "X" * 1200,
                    }
                ],
            }
        ]
        compacted, count = context_budget.compact_tool_result_messages(
            messages,
            {"call-1": "bash"},
            per_result_limit=160,
        )
        self.assertEqual(count, 1)
        part = compacted[0]["content"][0]
        self.assertEqual(part["tool_use_id"], "call-1")
        self.assertIn("Compacted bash result", part["content"])


class SearchFormattingTests(TestCase):
    def test_memory_search_text_formats_ranked_rows(self) -> None:
        row = memory_retriever.RankedMemory(
            id=1,
            kind="constraint",
            content="Keep changes inside the current workspace only.",
            path_ref=None,
            source_turn_id=12,
            status="active",
            salience=90,
            score=500,
            path_match=False,
            text_matches=1,
        )
        with patch.object(memory_retriever, "search_memories", return_value=[row]):
            text = memory_search_service.search_memory_text(
                "session-1",
                query="workspace only",
            )
        self.assertIn("Memory search results:", text)
        self.assertIn("[constraint]", text)
        self.assertIn("turn=12", text)

    def test_conversation_search_text_formats_hits(self) -> None:
        hit = conversation_search_service.ConversationHit(
            id=1,
            role="user",
            content="Please inspect the backend runtime manager.",
            score=180,
        )
        with patch.object(conversation_search_service, "search_conversation", return_value=[hit]):
            text = conversation_search_service.search_conversation_text(
                "session-1",
                query="runtime manager",
            )
        self.assertIn("Conversation search results:", text)
        self.assertIn("[user]", text)
        self.assertIn("backend runtime manager", text)


class ContextAssemblerTests(TestCase):
    def test_build_initial_loop_messages_keeps_tail_and_extra_signal_messages(self) -> None:
        transcript = [
            {"role": "user", "content": f"message {index}"}
            for index in range(16)
        ]
        transcript[2]["content"] = "Please inspect /tmp/project/README.md"
        with patch.object(context_assembler.session_service, "list_message_records", return_value=transcript):
            selected = context_assembler.build_initial_loop_messages("session-1", lookback=24, keep=12)
        self.assertEqual(len(selected), 12)
        self.assertEqual(selected[-1]["content"], "message 15")
        self.assertTrue(any("README.md" in item["content"] for item in selected))

    def test_assemble_context_injects_runtime_context_into_tool_result_message(self) -> None:
        retrieval = memory_retriever.RetrievalResult(
            stable=[],
            dynamic=[
                memory_retriever.RankedMemory(
                    id=1,
                    kind="progress",
                    content="We are currently inspecting the runtime manager flow.",
                    path_ref="backend/app/runtime/manager.py",
                    source_turn_id=3,
                    status="active",
                    salience=80,
                    score=300,
                    path_match=True,
                    text_matches=1,
                )
            ],
            counts_by_kind={"progress": 1},
        )
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "read_file",
                        "input": {"path": "backend/app/runtime/manager.py"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "runtime manager content",
                    }
                ],
            },
        ]
        with patch.object(
            context_assembler.session_service,
            "get_session",
            return_value=SimpleNamespace(workspace_mode="bound", workspace_label="Jarvis"),
        ), patch.object(
            context_assembler.memory_retriever,
            "retrieve_context_memories",
            return_value=retrieval,
        ):
            assembled = context_assembler.assemble_context(
                session_id="session-1",
                workspace=SimpleNamespace(as_posix=lambda: "/tmp/workspace"),
                messages=messages,
                base_system_prompt="You are Jarvis.",
                allowed_external_reads=[],
                max_tokens=4000,
            )
        first_user = next(message for message in assembled.messages if message["role"] == "user")
        self.assertEqual(first_user["content"][1]["tool_use_id"], "call-1")
        self.assertIn("<runtime-context>", first_user["content"][0]["text"])

    def test_assemble_context_compacts_large_tool_result_when_over_budget(self) -> None:
        retrieval = memory_retriever.RetrievalResult(stable=[], dynamic=[], counts_by_kind={})
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "bash",
                        "input": {"command": "rg TODO backend"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "\n".join(f"/tmp/file_{index}.py:TODO item {index}" for index in range(80)),
                    }
                ],
            },
        ]
        with patch.object(
            context_assembler.session_service,
            "get_session",
            return_value=SimpleNamespace(workspace_mode="bound", workspace_label="Jarvis"),
        ), patch.object(
            context_assembler.memory_retriever,
            "retrieve_context_memories",
            return_value=retrieval,
        ):
            assembled = context_assembler.assemble_context(
                session_id="session-1",
                workspace=SimpleNamespace(as_posix=lambda: "/tmp/workspace"),
                messages=messages,
                base_system_prompt="You are Jarvis.",
                allowed_external_reads=[],
                max_tokens=256,
            )
        first_user = next(message for message in assembled.messages if message["role"] == "user")
        tool_result_part = next(
            part for part in first_user["content"] if isinstance(part, dict) and part.get("type") == "tool_result"
        )
        self.assertIn("Compacted bash result", tool_result_part["content"])
        self.assertEqual(assembled.debug_meta["summarized_tool_results"], 1)

    def test_runtime_git_prompt_section_includes_branch_metadata(self) -> None:
        runtime = RuntimeManager()
        session = SimpleNamespace(
            git_enabled=True,
            repo_root="/tmp/repo",
            lead_branch="feature/test",
            working_tree_status="dirty",
            detached_head=False,
        )
        with patch("app.runtime.manager.session_service.get_session", return_value=session):
            section = runtime._session_git_prompt_section("session-1")

        self.assertIn("Repository root: /tmp/repo", section)
        self.assertIn("Lead branch: feature/test", section)
        self.assertIn("Working tree status: dirty", section)

    def test_session_git_state_tool_output_includes_branch_metadata(self) -> None:
        runtime = RuntimeManager()
        session = SimpleNamespace(
            git_enabled=True,
            repo_root="/tmp/repo",
            lead_branch="main",
            head_revision="abc123",
            working_tree_status="dirty",
            detached_head=False,
        )
        with patch("app.runtime.manager.session_service.get_session", return_value=session):
            output = runtime._session_git_state_tool_output("session-1")

        self.assertIn("Repository root: /tmp/repo", output)
        self.assertIn("Lead branch: main", output)
        self.assertIn("HEAD revision: abc123", output)
        self.assertIn("Working tree status: dirty", output)

    def test_assemble_context_expands_document_asset_reference(self) -> None:
        retrieval = memory_retriever.RetrievalResult(stable=[], dynamic=[], counts_by_kind={})
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize the attached plan."},
                    {"type": "asset_ref", "asset_id": "asset-1", "filename": "plan.docx", "kind": "docx", "status": "ready"},
                ],
            }
        ]
        asset = SimpleNamespace(
            id="asset-1",
            filename="plan.docx",
            kind="docx",
            status="ready",
            error_message=None,
            storage_path="/tmp/plan.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        chunks = [
            SimpleNamespace(
                id=1,
                asset_id="asset-1",
                chunk_index=0,
                page_number=None,
                sheet_name=None,
                slide_number=None,
                section_path="Status",
                content="The runtime is now asset aware.",
                summary="The runtime is now asset aware.",
                char_count=31,
                created_at="2026-05-16T00:00:00+00:00",
            )
        ]
        with patch.object(
            context_assembler.session_service,
            "get_session",
            return_value=SimpleNamespace(workspace_mode="bound", workspace_label="Jarvis"),
        ), patch.object(
            context_assembler.memory_retriever,
            "retrieve_context_memories",
            return_value=retrieval,
        ), patch.object(
            asset_service,
            "get_asset",
            return_value=asset,
        ), patch.object(
            asset_service,
            "search_asset_chunks",
            return_value=chunks,
        ):
            assembled = context_assembler.assemble_context(
                session_id="session-1",
                workspace=SimpleNamespace(as_posix=lambda: "/tmp/workspace"),
                messages=messages,
                base_system_prompt="You are Jarvis.",
                allowed_external_reads=[],
                max_tokens=4000,
        )
        first_user = assembled.messages[0]
        self.assertEqual(first_user["role"], "user")
        text_blocks = [
            part["text"]
            for part in first_user["content"]
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        self.assertTrue(any("Attached file: plan.docx" in block for block in text_blocks))


class OpenAIAdapterFormattingTests(TestCase):
    def test_responses_input_supports_input_image_parts(self) -> None:
        with patch.object(openai_adapter.Path, "read_bytes", return_value=b"fake-image"):
            items = openai_adapter._responses_input(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Inspect this screenshot."},
                            {
                                "type": "input_image",
                                "path": "/tmp/screenshot.png",
                                "mime_type": "image/png",
                            },
                        ],
                    }
                ]
            )
        self.assertEqual(items[0]["role"], "user")
        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertEqual(items[0]["content"][1]["type"], "input_image")
        self.assertTrue(str(items[0]["content"][1]["image_url"]).startswith("data:image/png;base64,"))
