from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.runtime.manager import RuntimeManager


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
