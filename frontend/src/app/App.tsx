import { FormEvent, KeyboardEvent, type ChangeEvent, type Dispatch, type MutableRefObject, type SetStateAction, useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  createSession,
  createTask,
  createTeammate,
  decideApproval,
  fetchApprovals,
  fetchBootstrap,
  resolveWorkspace,
  fetchSessionAssets,
  fetchSessionState,
  fetchSessionMemory,
  fetchSessions,
  fetchSkills,
  fetchSubagents,
  fetchTurns,
  fetchTeammateMessages,
  fetchTeammates,
  fetchTimeline,
  fetchToolExecutions,
  openSessionEvents,
  renameSession,
  resumeTurn,
  runSubagent,
  uploadSessionAssets,
  sendMessage,
  sendTeammateMessage,
  stopSessionTurn,
  deleteSessionAsset,
  deleteSession,
  type ApprovalSummary,
  type SessionAssetSummary,
  type SessionSummary,
  type SessionStateSummary,
  type SessionMemorySummary,
  type SkillSummary,
  type SubagentSummary,
  type TaskSummary,
  type TeammateMessageSummary,
  type TeammateSummary,
  type TimelineEvent,
  type TimelinePart,
  type TurnSummary,
  type ToolExecutionSummary,
} from "../lib/api";

const ACTIVE_SESSION_KEY = "jarvis.activeSession";
const DRAFT_SESSION_PREFIX = "draft:";
const DISPLAY_LOCALE = "zh-CN";
const DISPLAY_TIME_ZONE = "Asia/Shanghai";
const APP_LOGO_SRC = new URL("../assets/app-logo.png", import.meta.url).href;
const WORKBENCH_TABS = [
  { id: "tasks", label: "Tasks" },
  { id: "approvals", label: "Approvals" },
  { id: "memory", label: "Memory" },
  { id: "turns", label: "Turns" },
  { id: "logs", label: "Logs" },
  { id: "subagents", label: "Subagents" },
  { id: "teammates", label: "Teammates" },
] as const;

type WorkbenchTabId = (typeof WORKBENCH_TABS)[number]["id"];
type SidePanelMode = "workbench" | "skills";
type SessionItem = SessionSummary & { isDraft?: boolean };
type SessionContextMenuState = {
  sessionId: string;
  x: number;
  y: number;
} | null;
type SessionDialogState =
  | {
      mode: "create";
    }
  | {
      mode: "rename";
      sessionId: string;
      value: string;
    }
  | {
      mode: "hide";
      sessionId: string;
    }
  | null;

type TimelineCard = {
  tone: "user" | "assistant" | "result" | "status";
  kind: "message" | "result" | "status";
  layout?: "card" | "inline";
  label: string;
  title: string;
  content: string;
  parts?: TimelinePart[];
};

type TimelineRenderItem =
  | {
      kind: "single";
      key: string;
      event: TimelineEvent;
      card: TimelineCard;
    }
  | {
      kind: "tool-group";
      key: string;
      events: TimelineEvent[];
    };

const sessionStampFormatter = new Intl.DateTimeFormat(DISPLAY_LOCALE, {
  timeZone: DISPLAY_TIME_ZONE,
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const timelineStampFormatter = new Intl.DateTimeFormat(DISPLAY_LOCALE, {
  timeZone: DISPLAY_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function parseTimestamp(timestamp: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp);
  const normalized = hasZone ? timestamp : `${timestamp}Z`;
  return new Date(normalized);
}

function formatSessionStamp(timestamp: string): string {
  return sessionStampFormatter.format(parseTimestamp(timestamp));
}

function sortSessionsByActivity<T extends { updated_at: string; created_at: string }>(items: T[]): T[] {
  return [...items].sort(
    (left, right) =>
      parseTimestamp(right.updated_at ?? right.created_at).getTime() -
      parseTimestamp(left.updated_at ?? left.created_at).getTime(),
  );
}

function groupSessionsByWorkspace(items: SessionItem[]): Array<{ key: string; label: string; sessions: SessionItem[] }> {
  const groups = new Map<string, { key: string; label: string; sessions: SessionItem[] }>();
  for (const session of sortSessionsByActivity(items)) {
    const label = session.workspace_mode === "default"
        ? "Default Conversations"
        : session.workspace_label;
    const key = session.workspace_mode === "default" ? "default" : session.workspace_fingerprint;
    const group = groups.get(key);
    if (group) {
      group.sessions.push(session);
    } else {
      groups.set(key, { key, label, sessions: [session] });
    }
  }
  return Array.from(groups.values());
}

function startDraftSession(
  setSessions: Dispatch<SetStateAction<SessionItem[]>>,
  setActiveSessionId: Dispatch<SetStateAction<string>>,
  activeSessionRef: MutableRefObject<string>,
  setEventsBySession: Dispatch<SetStateAction<Record<string, TimelineEvent[]>>>,
  setStreamingBySession: Dispatch<SetStateAction<Record<string, { content: string; created_at: string } | null>>>,
  setDraft: Dispatch<SetStateAction<string>>,
  config: {
    workspaceMode: "bound" | "default";
    workspacePath?: string;
    workspaceLabel: string;
    workspaceFingerprint?: string;
  },
) {
  const timestamp = new Date().toISOString();
  const draftId = `${DRAFT_SESSION_PREFIX}${Date.now()}`;
  const draftSession: SessionItem = {
    session_id: draftId,
    title: nextSessionTitle(),
    workspace_mode: config.workspaceMode,
    canonical_workspace_path: config.workspacePath ?? "",
    workspace_label: config.workspaceLabel,
    workspace_fingerprint: config.workspaceFingerprint ?? "",
    status: "draft",
    created_at: timestamp,
    updated_at: timestamp,
    isDraft: true,
  };
  setSessions((current) => sortSessionsByActivity([draftSession, ...current]));
  setActiveSessionId(draftId);
  activeSessionRef.current = draftId;
  setEventsBySession((current) => ({ ...current, [draftId]: [] }));
  setStreamingBySession((current) => ({ ...current, [draftId]: null }));
  setDraft("");
}

function touchSession<T extends { session_id: string; title: string; updated_at: string; created_at: string }>(
  items: T[],
  sessionId: string,
  updatedAt: string,
  title?: string,
): T[] {
  return sortSessionsByActivity(
    items.map((session) =>
      session.session_id === sessionId
        ? {
            ...session,
            title: title ?? session.title,
            updated_at: updatedAt,
          }
        : session,
    ),
  );
}

function sortAssets(items: SessionAssetSummary[]): SessionAssetSummary[] {
  return [...items].sort(
    (left, right) =>
      parseTimestamp(right.updated_at ?? right.created_at).getTime() -
      parseTimestamp(left.updated_at ?? left.created_at).getTime(),
  );
}

function latestDurableEventId(events: TimelineEvent[]): number | undefined {
  const ids = events
    .map((event) => event.event_id)
    .filter((value): value is number => typeof value === "number");
  return ids.length ? Math.max(...ids) : undefined;
}

function formatTimelineStamp(timestamp: string): string {
  return timelineStampFormatter.format(parseTimestamp(timestamp));
}

function previewText(content: string, maxLength = 180): string {
  const text = content.trim().replace(/\s+/g, " ");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}…`;
}

function summarizeExecution(content: string): string {
  const [toolName, status] = content.split("->").map((part) => part.trim());
  if (!toolName || !status) {
    return content;
  }
  const verbs: Record<string, string> = {
    bash: "Command",
    read_file: "Read",
    write_file: "Write",
    edit_file: "Edit",
  };
  const toolLabel = verbs[toolName] ?? toolName;
  return `${toolLabel} ${status}.`;
}

function normalizeTimelineParts(event: TimelineEvent): TimelinePart[] {
  if (Array.isArray(event.parts) && event.parts.length) {
    return event.parts;
  }
  if (!event.content.trim()) {
    return [];
  }
  return [{ type: "text", text: event.content }];
}

function renderTimelineParts(parts: TimelinePart[], fallbackContent: string) {
  if (!parts.length) {
    return <p>{fallbackContent}</p>;
  }
  return (
    <div className="timeline-message-stack">
      {parts.map((part, index) => {
        if (part.type === "text") {
          return (
            <div key={`text-${index}`} className="markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {part.text}
              </ReactMarkdown>
            </div>
          );
        }
        if (part.kind === "image") {
          const imagePath = part.preview_path ?? part.storage_path ?? "";
          return (
            <figure key={`${part.asset_id}-${index}`} className="timeline-image-block">
              {imagePath ? (
                <img src={`file://${imagePath}`} alt={part.filename} className="timeline-image" />
              ) : null}
              <figcaption>{part.filename}</figcaption>
            </figure>
          );
        }
        return (
          <div key={`${part.asset_id}-${index}`} className="timeline-asset-chip">
            <strong>{part.filename}</strong>
            <span>{part.status}</span>
          </div>
        );
      })}
    </div>
  );
}

function buildTimelineCard(event: TimelineEvent): TimelineCard {
  if (event.type === "message.user" || event.type === "message.user.local") {
    return {
      tone: "user",
      kind: "message",
      label: "Instruction",
      title: "You",
      content: event.content,
      parts: normalizeTimelineParts(event),
    };
  }

  if (event.type === "message.assistant") {
    return {
      tone: "assistant",
      kind: "message",
      label: "Response",
      title: "Jarvis",
      content: event.content,
      parts: normalizeTimelineParts(event),
    };
  }

  if (event.type === "subagent.summary") {
    return {
      tone: "result",
      kind: "result",
      label: "Explorer Result",
      title: "Subagent summary",
      content: event.content,
    };
  }

  if (event.type === "teammate.reply") {
    return {
      tone: "result",
      kind: "result",
      label: "Scout Reply",
      title: "Teammate update",
      content: event.content,
    };
  }

  if (event.type === "tool.execution") {
    return {
      tone: "status",
      kind: "status",
      layout: "inline",
      label: "Tool Activity",
      title: "Execution summary",
      content: summarizeExecution(event.content),
    };
  }

  if (event.type === "approval.requested") {
    return {
      tone: "status",
      kind: "status",
      layout: "inline",
      label: "Approval",
      title: "Approval requested",
      content: event.content,
    };
  }

  if (event.type === "approval.resolved") {
    return {
      tone: "status",
      kind: "status",
      layout: "inline",
      label: "Approval",
      title: "Approval resolved",
      content: event.content,
    };
  }

  if (event.type === "runtime.state") {
    return {
      tone: "status",
      kind: "status",
      label: "Runtime",
      title: "Lead runtime",
      content: event.content,
    };
  }

  if (event.type === "teammate.created" || event.type === "teammate.message") {
    return {
      tone: "status",
      kind: "status",
      label: "Scout",
      title: "Teammate activity",
      content: event.content,
    };
  }

  if (event.type === "subagent.started") {
    return {
      tone: "status",
      kind: "status",
      label: "Explorer",
      title: "Subagent running",
      content: event.content,
    };
  }

  if (event.type === "turn.cancelled") {
    return {
      tone: "status",
      kind: "status",
      label: "Runtime",
      title: "Turn stopped",
      content: event.content,
    };
  }

  return {
    tone: "status",
    kind: "status",
    label: event.type.replace(".", " / "),
    title: "System event",
    content: event.content,
  };
}

function groupTimelineItems(
  entries: Array<{ event: TimelineEvent; card: TimelineCard }>,
): TimelineRenderItem[] {
  const grouped: TimelineRenderItem[] = [];
  let index = 0;
  while (index < entries.length) {
    const current = entries[index];
    if (current.event.type !== "tool.execution") {
      grouped.push({
        kind: "single",
        key: `${current.event.created_at}-${index}`,
        event: current.event,
        card: current.card,
      });
      index += 1;
      continue;
    }

    const toolEvents = [current.event];
    let cursor = index + 1;
    while (cursor < entries.length && entries[cursor].event.type === "tool.execution") {
      toolEvents.push(entries[cursor].event);
      cursor += 1;
    }
    if (toolEvents.length === 1) {
      grouped.push({
        kind: "single",
        key: `${current.event.created_at}-${index}`,
        event: current.event,
        card: current.card,
      });
    } else {
      grouped.push({
        kind: "tool-group",
        key: `${toolEvents[0].created_at}-${toolEvents.length}-${index}`,
        events: toolEvents,
      });
    }
    index = cursor;
  }
  return grouped;
}

function nextSessionTitle(): string {
  return "New Session";
}

export function App() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [assetsBySession, setAssetsBySession] = useState<Record<string, SessionAssetSummary[]>>({});
  const [selectedAssetIdsBySession, setSelectedAssetIdsBySession] = useState<Record<string, string[]>>({});
  const [sessionStateBySession, setSessionStateBySession] = useState<Record<string, SessionStateSummary>>({});
  const [memoryBySession, setMemoryBySession] = useState<Record<string, SessionMemorySummary[]>>({});
  const [turnsBySession, setTurnsBySession] = useState<Record<string, TurnSummary[]>>({});
  const [eventsBySession, setEventsBySession] = useState<Record<string, TimelineEvent[]>>({});
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [teammates, setTeammates] = useState<TeammateSummary[]>([]);
  const [subagents, setSubagents] = useState<SubagentSummary[]>([]);
  const [subagentDraft, setSubagentDraft] = useState("Investigate the current workspace, collect evidence, and return a concise summary with the next technical actions.");
  const [selectedTeammateId, setSelectedTeammateId] = useState<number | null>(null);
  const [teammateMessages, setTeammateMessages] = useState<TeammateMessageSummary[]>([]);
  const [teammateDraft, setTeammateDraft] = useState("Review the latest runtime activity.");
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [executions, setExecutions] = useState<ToolExecutionSummary[]>([]);
  const [selectedExecutionId, setSelectedExecutionId] = useState<number | null>(null);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [draft, setDraft] = useState("");
  const [streamingBySession, setStreamingBySession] = useState<Record<string, { content: string; created_at: string } | null>>({});
  const [connectionState, setConnectionState] = useState("offline");
  const [bootstrapState, setBootstrapState] = useState("booting");
  const [sessionSearch, setSessionSearch] = useState("");
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [sidePanelMode, setSidePanelMode] = useState<SidePanelMode>("workbench");
  const [activeWorkbenchTab, setActiveWorkbenchTab] = useState<WorkbenchTabId>("approvals");
  const [autoScrollTimeline, setAutoScrollTimeline] = useState(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [createSessionError, setCreateSessionError] = useState("");
  const [assetUploadError, setAssetUploadError] = useState("");
  const [isUploadingAssets, setIsUploadingAssets] = useState(false);
  const [sessionContextMenu, setSessionContextMenu] = useState<SessionContextMenuState>(null);
  const [sessionDialog, setSessionDialog] = useState<SessionDialogState>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const composerFileInputRef = useRef<HTMLInputElement | null>(null);
  const sessionContextMenuRef = useRef<HTMLDivElement | null>(null);
  const activeSessionRef = useRef("");
  const eventsBySessionRef = useRef<Record<string, TimelineEvent[]>>({});
  const lastRealtimeEventAtRef = useRef<Record<string, number>>({});
  const lastRecoveryAttemptAtRef = useRef<Record<string, number>>({});
  const sessionRefreshTimersRef = useRef<Record<string, number>>({});
  const sessionRefreshInFlightRef = useRef<Record<string, boolean>>({});
  const sessionRefreshNeedsTimelineRef = useRef<Record<string, boolean>>({});
  const sessionSocketsRef = useRef<Record<string, () => void>>({});

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;

    const attempt = async () => {
      try {
        const data = await fetchBootstrap();
        if (cancelled) return;
        const storedSessionId = window.localStorage.getItem(ACTIVE_SESSION_KEY);
        const fallbackSessionId = data.sessions[0]?.session_id ?? "";
        const nextSessionId = data.sessions.some((session) => session.session_id === storedSessionId)
          ? storedSessionId ?? fallbackSessionId
          : fallbackSessionId;
        setSessions(sortSessionsByActivity(data.sessions));
        setTasks(data.tasks);
        setTeammates(data.teammates);
        setSelectedTeammateId(data.teammates[0]?.id ?? null);
        setSubagents(data.subagents);
        setApprovals(data.approvals);
        setExecutions(data.tool_executions);
        setSelectedExecutionId(data.tool_executions[0]?.id ?? null);
        setActiveSessionId(nextSessionId);
        activeSessionRef.current = nextSessionId;
        setBootstrapState("ready");
      } catch {
        if (cancelled) return;
        setBootstrapState("waiting-for-backend");
        retryTimer = window.setTimeout(attempt, 1200);
      }
    };

    attempt();
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    activeSessionRef.current = activeSessionId;
    window.localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
  }, [activeSessionId]);

  async function refreshSessionState(sessionId: string) {
    const [nextSessions, nextSubagents, nextApprovals, nextExecutions, nextTeammates, nextSessionState, nextTurns, nextMemory, nextAssets] = await Promise.all([
      fetchSessions(),
      fetchSubagents(sessionId),
      fetchApprovals(sessionId),
      fetchToolExecutions(sessionId),
      fetchTeammates(sessionId),
      fetchSessionState(sessionId),
      fetchTurns(sessionId),
      fetchSessionMemory(sessionId),
      fetchSessionAssets(sessionId),
    ]);

    setSessions(sortSessionsByActivity(nextSessions));
    setSubagents(nextSubagents);
    setApprovals(nextApprovals);
    setExecutions(nextExecutions);
    setSelectedExecutionId((current) =>
      nextExecutions.some((item) => item.id === current) ? current : nextExecutions[0]?.id ?? null,
    );
    setTeammates(nextTeammates);
    setSelectedTeammateId((current) =>
      nextTeammates.some((item) => item.id === current) ? current : nextTeammates[0]?.id ?? null,
    );
    setSessionStateBySession((current) => ({ ...current, [sessionId]: nextSessionState }));
    setTurnsBySession((current) => ({ ...current, [sessionId]: nextTurns }));
    setMemoryBySession((current) => ({ ...current, [sessionId]: nextMemory }));
    setAssetsBySession((current) => ({ ...current, [sessionId]: nextAssets }));
    setSelectedAssetIdsBySession((current) => ({
      ...current,
      [sessionId]: (current[sessionId] ?? []).filter((assetId) => nextAssets.some((asset) => asset.id === assetId)),
    }));
  }

  async function refreshRealtimeRecovery(sessionId: string) {
    const [timeline, nextSessionState] = await Promise.all([
      fetchTimeline(sessionId),
      fetchSessionState(sessionId),
    ]);
    setEventsBySession((current) => ({ ...current, [sessionId]: timeline }));
    setSessionStateBySession((current) => ({ ...current, [sessionId]: nextSessionState }));
    if (!nextSessionState.active_turn) {
      setStreamingBySession((current) => ({ ...current, [sessionId]: null }));
    }
  }

  async function runScheduledSessionRefresh(sessionId: string) {
    if (sessionRefreshInFlightRef.current[sessionId]) {
      return;
    }
    sessionRefreshInFlightRef.current[sessionId] = true;
    const includeTimeline = Boolean(sessionRefreshNeedsTimelineRef.current[sessionId]);
    sessionRefreshNeedsTimelineRef.current[sessionId] = false;
    try {
      if (includeTimeline) {
        const [timeline] = await Promise.all([
          fetchTimeline(sessionId),
          refreshSessionState(sessionId),
        ]);
        setEventsBySession((current) => ({ ...current, [sessionId]: timeline }));
      } else {
        await refreshSessionState(sessionId);
      }
    } finally {
      sessionRefreshInFlightRef.current[sessionId] = false;
      if (sessionRefreshNeedsTimelineRef.current[sessionId]) {
        scheduleSessionRefresh(sessionId, { includeTimeline: sessionRefreshNeedsTimelineRef.current[sessionId] });
      }
    }
  }

  function scheduleSessionRefresh(sessionId: string, options?: { includeTimeline?: boolean }) {
    if (options?.includeTimeline) {
      sessionRefreshNeedsTimelineRef.current[sessionId] = true;
    }
    if (sessionRefreshTimersRef.current[sessionId]) {
      return;
    }
    sessionRefreshTimersRef.current[sessionId] = window.setTimeout(() => {
      delete sessionRefreshTimersRef.current[sessionId];
      void runScheduledSessionRefresh(sessionId);
    }, 150);
  }

  useEffect(() => {
    if (!activeSessionId) return;
    let cancelled = false;
    if (eventsBySession[activeSessionId] === undefined && !activeSessionId.startsWith(DRAFT_SESSION_PREFIX)) {
      fetchTimeline(activeSessionId)
      .then((items) => {
        if (cancelled || activeSessionRef.current !== activeSessionId) return;
        setEventsBySession((current) => ({ ...current, [activeSessionId]: items }));
      })
      .catch(() => {
        if (cancelled || activeSessionRef.current !== activeSessionId) return;
        setEventsBySession((current) => ({ ...current, [activeSessionId]: [] }));
      });
    }
    setAutoScrollTimeline(true);
    if (activeSessionId.startsWith(DRAFT_SESSION_PREFIX)) {
      return () => {
        cancelled = true;
      };
    }
    refreshSessionState(activeSessionId).catch(() => {
      setSubagents([]);
      setTeammates([]);
      setApprovals([]);
      setExecutions([]);
      setAssetsBySession((current) => ({ ...current, [activeSessionId]: [] }));
    });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (!selectedTeammateId) return;
    fetchTeammateMessages(selectedTeammateId)
      .then(setTeammateMessages)
      .catch(() => setTeammateMessages([]));
  }, [selectedTeammateId]);

  useEffect(() => {
    eventsBySessionRef.current = eventsBySession;
  }, [eventsBySession]);

  useEffect(() => {
    const cleanups = sessionSocketsRef.current;
    const liveSessionIds = sessions
      .filter((session) => !session.isDraft)
      .map((session) => session.session_id);

    for (const sessionId of liveSessionIds) {
      if (cleanups[sessionId]) continue;
      setConnectionState("connecting");
      cleanups[sessionId] = openSessionEvents(
        sessionId,
        (event) => {
          lastRealtimeEventAtRef.current[event.session_id] = Date.now();
          if (event.type === "message.user") {
            setSessions((current) => touchSession(current, event.session_id, event.created_at));
            setEventsBySession((current) => {
              const sessionEvents = current[event.session_id] ?? [];
              const localIndex = sessionEvents.findIndex(
                (item) => item.type === "message.user.local" && item.content === event.content,
              );
              if (localIndex === -1) {
                const exists = sessionEvents.some(
                  (item) =>
                    (typeof event.event_id === "number" && item.event_id === event.event_id)
                    || (
                      item.created_at === event.created_at &&
                      item.type === event.type &&
                      item.content === event.content
                    ),
                );
                return exists ? current : { ...current, [event.session_id]: [...sessionEvents, event] };
              }
              const next = [...sessionEvents];
              next.splice(localIndex, 1, event);
              return { ...current, [event.session_id]: next };
            });
            return;
          }
          if (event.type === "message.assistant.delta") {
            setStreamingBySession((current) => ({
              ...current,
              [event.session_id]: {
                content: `${current[event.session_id]?.content ?? ""}${event.content}`,
                created_at: current[event.session_id]?.created_at ?? event.created_at,
              },
            }));
            return;
          }
          if (event.type === "message.assistant") {
            setStreamingBySession((current) => ({ ...current, [event.session_id]: null }));
          }
          if (event.type === "turn.cancelled") {
            setStreamingBySession((current) => ({ ...current, [event.session_id]: null }));
          }
          if (event.type === "session.renamed") {
            setSessions((current) => touchSession(current, event.session_id, event.created_at, event.content));
            return;
          }
          setSessions((current) => touchSession(current, event.session_id, event.created_at));
          setEventsBySession((current) => {
            const sessionEvents = current[event.session_id] ?? [];
            const exists = sessionEvents.some(
              (item) =>
                (typeof event.event_id === "number" && item.event_id === event.event_id)
                || (
                  item.created_at === event.created_at &&
                  item.type === event.type &&
                  item.content === event.content
                ),
            );
            return exists ? current : { ...current, [event.session_id]: [...sessionEvents, event] };
          });
          if (
            event.type.startsWith("asset.") ||
            event.type.startsWith("tool.") ||
            event.type.startsWith("approval.") ||
            event.type.startsWith("turn.") ||
            event.type.startsWith("task.") ||
            event.type.startsWith("teammate.") ||
            event.type.startsWith("subagent.")
          ) {
            scheduleSessionRefresh(event.session_id);
          }
          if (event.type.startsWith("teammate.") && selectedTeammateId) {
            fetchTeammateMessages(selectedTeammateId).then(setTeammateMessages).catch(() => undefined);
          }
        },
        () => latestDurableEventId(eventsBySessionRef.current[sessionId] ?? []),
        {
          onOpen: () => {
            lastRealtimeEventAtRef.current[sessionId] = Date.now();
            setConnectionState("live");
          },
          onError: () => setConnectionState("degraded"),
          onClose: () => setConnectionState("reconnecting"),
        },
      );
    }

    for (const sessionId of Object.keys(cleanups)) {
      if (liveSessionIds.includes(sessionId)) continue;
      cleanups[sessionId]?.();
      delete cleanups[sessionId];
    }

    return () => undefined;
  }, [sessions, selectedTeammateId]);

  useEffect(() => {
    return () => {
      for (const cleanup of Object.values(sessionSocketsRef.current)) {
        cleanup();
      }
      for (const timer of Object.values(sessionRefreshTimersRef.current)) {
        window.clearTimeout(timer);
      }
      sessionRefreshTimersRef.current = {};
      sessionSocketsRef.current = {};
      setConnectionState("offline");
    };
  }, []);

  useEffect(() => {
    if (!sessionContextMenu) return;
    const closeMenu = () => setSessionContextMenu(null);
    const closeOnPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && sessionContextMenuRef.current?.contains(target)) return;
      closeMenu();
    };
    const closeOnKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    window.addEventListener("pointerdown", closeOnPointerDown);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("keydown", closeOnKeyDown);
    return () => {
      window.removeEventListener("pointerdown", closeOnPointerDown);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("keydown", closeOnKeyDown);
    };
  }, [sessionContextMenu]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    await submitDraft();
  }

  async function onStopTurn() {
    if (!activeSessionId || !isActiveTurnRunning) return;
    await stopSessionTurn(activeSessionId);
  }

  async function realizeDraftSession(sourceSessionId: string, preferredContent = ""): Promise<string> {
    if (!sourceSessionId.startsWith(DRAFT_SESSION_PREFIX)) {
      return sourceSessionId;
    }
    const draftSession = sessions.find((session) => session.session_id === sourceSessionId) ?? null;
    const resolvedWorkspace =
      draftSession?.workspace_mode === "bound" && draftSession.canonical_workspace_path
        ? {
            workspace_path: draftSession.canonical_workspace_path,
            workspace_label: draftSession.workspace_label,
            workspace_fingerprint: draftSession.workspace_fingerprint,
          }
        : preferredContent.trim()
          ? await resolveWorkspace(preferredContent).catch(() => null)
          : null;
    const created = resolvedWorkspace
      ? await createSession(nextSessionTitle(), resolvedWorkspace.workspace_path)
      : await createSession(nextSessionTitle(), undefined);
    setSessions((current) =>
      sortSessionsByActivity(
        current.map((session) =>
          session.session_id === sourceSessionId ? { ...created } : session,
        ),
      ),
    );
    setEventsBySession((current) => {
      const draftEvents = current[sourceSessionId] ?? [];
      const { [sourceSessionId]: _, ...rest } = current;
      return {
        ...rest,
        [created.session_id]: draftEvents.map((event) => ({ ...event, session_id: created.session_id })),
      };
    });
    setStreamingBySession((current) => {
      const draftStreaming = current[sourceSessionId] ?? null;
      const { [sourceSessionId]: _, ...rest } = current;
      return { ...rest, [created.session_id]: draftStreaming };
    });
    setAssetsBySession((current) => {
      const draftAssets = current[sourceSessionId] ?? [];
      const { [sourceSessionId]: _, ...rest } = current;
      return { ...rest, [created.session_id]: draftAssets };
    });
    setSelectedAssetIdsBySession((current) => {
      const draftAssetIds = current[sourceSessionId] ?? [];
      const { [sourceSessionId]: _, ...rest } = current;
      return { ...rest, [created.session_id]: draftAssetIds };
    });
    setActiveSessionId(created.session_id);
    activeSessionRef.current = created.session_id;
    return created.session_id;
  }

  async function submitDraft() {
    const selectedAssetIds = selectedAssetIdsBySession[activeSessionId] ?? [];
    const selectedAssets = (assetsBySession[activeSessionId] ?? []).filter((asset) => selectedAssetIds.includes(asset.id));
    if (!activeSessionId || (!draft.trim() && !selectedAssetIds.length) || isActiveTurnRunning) return;
    const content = draft.trim();
    const sourceSessionId = activeSessionId;
    let targetSessionId = sourceSessionId;
    setDraft("");
    setAutoScrollTimeline(true);
    const optimisticCreatedAt = new Date().toISOString();
    const attachmentSummary = selectedAssets.length
      ? selectedAssets.map((asset) => asset.filename).join(", ")
      : `${selectedAssetIds.length} attachment(s)`;
    const optimisticContent = content || attachmentSummary;
    setStreamingBySession((current) => ({
      ...current,
      [sourceSessionId]: {
        content: "",
        created_at: optimisticCreatedAt,
      },
    }));
    setSessions((current) => touchSession(
      current,
      sourceSessionId,
      optimisticCreatedAt,
    ));
    setEventsBySession((current) => ({
      ...current,
      [sourceSessionId]: [
        ...(current[sourceSessionId] ?? []),
        {
          session_id: sourceSessionId,
          type: "message.user.local",
          content: optimisticContent,
          created_at: optimisticCreatedAt,
        } as TimelineEvent,
      ],
    }));
    try {
      if (sourceSessionId.startsWith(DRAFT_SESSION_PREFIX)) {
        targetSessionId = await realizeDraftSession(sourceSessionId, content);
      }

      await sendMessage(targetSessionId, content, selectedAssetIds);
      setSelectedAssetIdsBySession((current) => ({ ...current, [targetSessionId]: [] }));
    } catch (error) {
      const rollbackSessionId = targetSessionId;
      setStreamingBySession((current) => ({ ...current, [rollbackSessionId]: null }));
      setEventsBySession((current) => ({
        ...current,
        [rollbackSessionId]: (current[rollbackSessionId] ?? []).filter(
          (item) =>
            !(item.type === "message.user.local" && item.content === optimisticContent && item.created_at === optimisticCreatedAt),
        ),
      }));
      throw error;
    }
  }

  async function onCreateSession() {
    setCreateSessionError("");
    setSessionDialog({ mode: "create" });
  }

  async function onCreateDefaultSession() {
    setCreateSessionError("");
    startDraftSession(
      setSessions,
      setActiveSessionId,
      activeSessionRef,
      setEventsBySession,
      setStreamingBySession,
      setDraft,
      {
        workspaceMode: "default",
        workspaceLabel: "Default Conversations",
      },
    );
    setSessionDialog(null);
  }

  async function onCreateBoundSession() {
    try {
      setCreateSessionError("");
      const selection = await open({
        directory: true,
        multiple: false,
        title: "Choose Workspace Folder",
      });
      if (!selection || Array.isArray(selection)) {
        return;
      }
      const path = String(selection);
      const parts = path.split("/").filter(Boolean);
      const label = parts[parts.length - 1] || path;
      startDraftSession(
        setSessions,
        setActiveSessionId,
        activeSessionRef,
        setEventsBySession,
        setStreamingBySession,
        setDraft,
        {
          workspaceMode: "bound",
          workspacePath: path,
          workspaceLabel: label,
          workspaceFingerprint: path,
        },
      );
      setSessionDialog(null);
    } catch (error) {
      setCreateSessionError(error instanceof Error ? error.message : "Failed to open the folder picker.");
    }
  }

  async function uploadComposerFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList);
    if (!files.length || !activeSessionId) return;
    setAssetUploadError("");
    setIsUploadingAssets(true);
    try {
      const sourceSessionId = activeSessionId;
      const targetSessionId = sourceSessionId.startsWith(DRAFT_SESSION_PREFIX)
        ? await realizeDraftSession(sourceSessionId, draft)
        : sourceSessionId;
      const uploaded = await uploadSessionAssets(targetSessionId, files);
      setAssetsBySession((current) => ({
        ...current,
        [targetSessionId]: sortAssets([...(current[targetSessionId] ?? []), ...uploaded]),
      }));
      setSelectedAssetIdsBySession((current) => ({
        ...current,
        [targetSessionId]: [...new Set([...(current[targetSessionId] ?? []), ...uploaded.map((asset) => asset.id)])],
      }));
    } catch (error) {
      setAssetUploadError(error instanceof Error ? error.message : "Failed to upload attachments.");
    } finally {
      setIsUploadingAssets(false);
      if (composerFileInputRef.current) {
        composerFileInputRef.current.value = "";
      }
    }
  }

  function onComposerPickFiles() {
    composerFileInputRef.current?.click();
  }

  function onComposerFilesSelected(event: ChangeEvent<HTMLInputElement>) {
    if (!event.target.files?.length) return;
    void uploadComposerFiles(event.target.files);
  }

  function onToggleAssetSelection(assetId: string) {
    if (!activeSessionId) return;
    setSelectedAssetIdsBySession((current) => {
      const existing = current[activeSessionId] ?? [];
      const next = existing.includes(assetId)
        ? existing.filter((id) => id !== assetId)
        : [...existing, assetId];
      return { ...current, [activeSessionId]: next };
    });
  }

  async function onDeleteAsset(assetId: string) {
    if (!activeSessionId || activeSessionId.startsWith(DRAFT_SESSION_PREFIX)) return;
    await deleteSessionAsset(activeSessionId, assetId);
    setAssetsBySession((current) => ({
      ...current,
      [activeSessionId]: (current[activeSessionId] ?? []).filter((asset) => asset.id !== assetId),
    }));
    setSelectedAssetIdsBySession((current) => ({
      ...current,
      [activeSessionId]: (current[activeSessionId] ?? []).filter((id) => id !== assetId),
    }));
  }

  function onRenameSession(sessionId: string) {
    const target = sessions.find((session) => session.session_id === sessionId);
    if (!target) return;
    setSessionContextMenu(null);
    setSessionDialog({
      mode: "rename",
      sessionId,
      value: target.title,
    });
  }

  function onDeleteSession(sessionId: string) {
    setSessionContextMenu(null);
    setSessionDialog({
      mode: "hide",
      sessionId,
    });
  }

  async function confirmSessionDialog() {
    if (!sessionDialog) return;
    if (sessionDialog.mode === "create") {
      setSessionDialog(null);
      return;
    }
    if (sessionDialog.mode === "rename") {
      const nextTitle = sessionDialog.value.trim();
      const target = sessions.find((session) => session.session_id === sessionDialog.sessionId);
      if (!target || !nextTitle || nextTitle === target.title) {
        setSessionDialog(null);
        return;
      }
      const updated = await renameSession(sessionDialog.sessionId, nextTitle);
      setSessions((current) => touchSession(current, updated.session_id, updated.updated_at, updated.title));
      setSessionDialog(null);
      return;
    }

    await deleteSession(sessionDialog.sessionId);
    const remaining = sessions.filter((session) => session.session_id !== sessionDialog.sessionId);
    setSessions(remaining);
    setEventsBySession((current) => {
      const { [sessionDialog.sessionId]: _, ...rest } = current;
      return rest;
    });
    setStreamingBySession((current) => {
      const { [sessionDialog.sessionId]: _, ...rest } = current;
      return rest;
    });
    setAssetsBySession((current) => {
      const { [sessionDialog.sessionId]: _, ...rest } = current;
      return rest;
    });
    setSelectedAssetIdsBySession((current) => {
      const { [sessionDialog.sessionId]: _, ...rest } = current;
      return rest;
    });
    if (activeSessionId === sessionDialog.sessionId) {
      const nextActive = remaining[0]?.session_id ?? "";
      setActiveSessionId(nextActive);
      activeSessionRef.current = nextActive;
    }
    setSessionDialog(null);
  }

  async function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    await submitDraft();
  }

  async function onCreateTask() {
    if (!activeSessionId) return;
    const task = await createTask("Follow up latest runtime turn", activeSessionId);
    setTasks((current) => [task, ...current]);
    setActiveWorkbenchTab("tasks");
    setWorkbenchOpen(true);
  }

  async function onDecision(approvalId: number, approve: boolean) {
    await decideApproval(approvalId, approve);
    if (!activeSessionId) return;
    const timeline = await fetchTimeline(activeSessionId);
    setEventsBySession((current) => ({
      ...current,
      [activeSessionId]: timeline,
    }));
    await refreshSessionState(activeSessionId);
  }

  async function onResumeInterruptedTurn() {
    if (!activeSessionId) return;
    const turnId = activeSessionState?.latest_interrupted_turn?.id;
    if (!turnId) return;
    await resumeTurn(turnId);
    setActiveWorkbenchTab("turns");
    setWorkbenchOpen(true);
    await refreshSessionState(activeSessionId);
  }

  async function onCreateTeammate() {
    if (!activeSessionId) return;
    const teammate = await createTeammate(activeSessionId, `Scout ${teammates.length + 1}`, "Scout");
    setTeammates((current) => [teammate, ...current]);
    setSelectedTeammateId(teammate.id);
    setActiveWorkbenchTab("teammates");
    setWorkbenchOpen(true);
  }

  async function onSendTeammateMessage() {
    if (!selectedTeammateId || !teammateDraft.trim() || !activeSessionId) return;
    await sendTeammateMessage(selectedTeammateId, teammateDraft.trim());
    setTeammateMessages(await fetchTeammateMessages(selectedTeammateId));
    await refreshSessionState(activeSessionId);
    const timeline = await fetchTimeline(activeSessionId);
    setEventsBySession((current) => ({
      ...current,
      [activeSessionId]: timeline,
    }));
  }

  async function onRunSubagent() {
    if (!activeSessionId || !subagentDraft.trim()) return;
    await runSubagent(activeSessionId, `Explorer ${subagents.length + 1}`, subagentDraft.trim());
    await refreshSessionState(activeSessionId);
    const timeline = await fetchTimeline(activeSessionId);
    setEventsBySession((current) => ({
      ...current,
      [activeSessionId]: timeline,
    }));
    setActiveWorkbenchTab("subagents");
    setWorkbenchOpen(true);
  }

  const sessionAssets = activeSessionId ? assetsBySession[activeSessionId] ?? [] : [];
  const selectedAssetIds = activeSessionId ? selectedAssetIdsBySession[activeSessionId] ?? [] : [];
  const selectedComposerAssets = sessionAssets.filter((asset) => selectedAssetIds.includes(asset.id));
  const events = eventsBySession[activeSessionId] ?? [];
  const streamingAssistant = streamingBySession[activeSessionId] ?? null;
  const isActiveTurnRunning = Boolean(activeSessionId && streamingAssistant);

  useEffect(() => {
    if (!activeSessionId || activeSessionId.startsWith(DRAFT_SESSION_PREFIX)) return;
    if (!isActiveTurnRunning) return;
    const interval = window.setInterval(() => {
      const now = Date.now();
      const lastEventAt = lastRealtimeEventAtRef.current[activeSessionId] ?? 0;
      const lastRecoveryAt = lastRecoveryAttemptAtRef.current[activeSessionId] ?? 0;
      const staleForMs = now - lastEventAt;
      const sinceRecoveryMs = now - lastRecoveryAt;
      if (staleForMs < 8000 || sinceRecoveryMs < 8000) {
        return;
      }
      lastRecoveryAttemptAtRef.current[activeSessionId] = now;
      if (connectionState === "live") {
        setConnectionState("degraded");
      }
      refreshRealtimeRecovery(activeSessionId)
        .then(() => {
          lastRealtimeEventAtRef.current[activeSessionId] = Date.now();
        })
        .catch(() => undefined);
    }, 3000);

    return () => {
      window.clearInterval(interval);
    };
  }, [activeSessionId, connectionState, isActiveTurnRunning]);

  useEffect(() => {
    if (!autoScrollTimeline || !timelineRef.current) return;
    timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
    setShowScrollToBottom(false);
  }, [events, streamingAssistant, autoScrollTimeline]);

  function onTimelineScroll() {
    if (!timelineRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = timelineRef.current;
    const nearBottom = scrollHeight - (scrollTop + clientHeight) < 56;
    setAutoScrollTimeline(nearBottom);
    setShowScrollToBottom(!nearBottom);
  }

  function scrollTimelineToBottom() {
    if (!timelineRef.current) return;
    timelineRef.current.scrollTop = timelineRef.current.scrollHeight;
    setAutoScrollTimeline(true);
    setShowScrollToBottom(false);
  }

  const activeSession = sessions.find((session) => session.session_id === activeSessionId) ?? null;
  const activeSessionState = activeSessionId ? sessionStateBySession[activeSessionId] ?? null : null;
  const sessionMemory = activeSessionId ? memoryBySession[activeSessionId] ?? [] : [];
  const turns = activeSessionId ? turnsBySession[activeSessionId] ?? [] : [];
  const filteredSessions = sessions.filter((session) =>
    session.title.toLowerCase().includes(sessionSearch.trim().toLowerCase()),
  );
  const groupedSessions = groupSessionsByWorkspace(filteredSessions);
  const pendingApprovals = approvals.filter((approval) => approval.status === "pending");
  const selectedExecution = executions.find((item) => item.id === selectedExecutionId) ?? null;
  const selectedTeammate = teammates.find((item) => item.id === selectedTeammateId) ?? null;
  const timelineCards = events
    .filter(
      (event) =>
        event.type !== "runtime.state"
        && event.type !== "session.renamed"
        && event.type !== "turn.started"
        && event.type !== "turn.completed"
        && event.type !== "asset.uploaded"
        && event.type !== "asset.processing"
        && event.type !== "asset.ready"
        && event.type !== "asset.removed"
    )
    .map((event) => ({
      event,
      card: buildTimelineCard(event),
    }));
  const liveAssistantCard = streamingAssistant
    ? {
        event: {
          session_id: activeSessionId,
          type: "message.assistant",
          content: streamingAssistant.content,
          created_at: streamingAssistant.created_at,
        } as TimelineEvent,
        card: {
          tone: "assistant" as const,
          kind: "message" as const,
          layout: "card" as const,
          label: "Response",
          title: "Jarvis",
          content: streamingAssistant.content,
        },
      }
    : null;
  const timelineItems = groupTimelineItems([
    ...timelineCards,
    ...(liveAssistantCard ? [liveAssistantCard] : []),
  ]);
  const activeSessionStamp = activeSession ? formatSessionStamp(activeSession.updated_at ?? activeSession.created_at) : null;
  const statusRailCards: Array<{
    tab: WorkbenchTabId;
    label: string;
    value: string;
    emphasis?: "default" | "alert";
  }> = [
    {
      tab: "approvals",
      label: "Approvals",
      value: String(pendingApprovals.length),
      emphasis: pendingApprovals.length ? "alert" : "default",
    },
    {
      tab: "memory",
      label: "Memory",
      value: String(sessionMemory.length),
    },
    {
      tab: "tasks",
      label: "Tasks",
      value: String(tasks.length),
    },
    {
      tab: "turns",
      label: "Turns",
      value: String(turns.length),
    },
    {
      tab: "logs",
      label: "Runtime",
      value: String(executions.length),
    },
    {
      tab: "subagents",
      label: "Agents",
      value: String(subagents.length),
    },
    {
      tab: "teammates",
      label: "Scouts",
      value: String(teammates.length),
    },
  ];

  function openWorkbenchTab(tab: WorkbenchTabId) {
    setSidePanelMode("workbench");
    setActiveWorkbenchTab(tab);
    setWorkbenchOpen(true);
  }

  async function openSkillsPanel() {
    setSidePanelMode("skills");
    setWorkbenchOpen(true);
    try {
      setSkills(await fetchSkills());
    } catch {
      setSkills([]);
    }
  }

  function renderWorkbenchPanel() {
    if (activeWorkbenchTab === "tasks") {
      return (
        <section className="workbench-section">
          <div className="section-heading">
            <div>
              <p className="micro-label">Queue</p>
              <h3>Tasks</h3>
            </div>
            <button type="button" className="secondary-button" onClick={onCreateTask}>New task</button>
          </div>
          <div className="workbench-list">
            {tasks.map((task) => (
              <article key={task.id} className="workbench-card">
                <div className="workbench-card-header">
                  <strong>{task.subject}</strong>
                  <span className="mini-pill">{task.status}</span>
                </div>
                <p>{task.description || "Created from the current session."}</p>
              </article>
            ))}
            {!tasks.length ? <p className="empty-inline">No tasks yet.</p> : null}
          </div>
        </section>
      );
    }

    if (activeWorkbenchTab === "approvals") {
      return (
        <section className="workbench-section">
          <div className="section-heading">
            <div>
              <p className="micro-label">Review</p>
              <h3>Approvals</h3>
            </div>
            <span className="section-count">{pendingApprovals.length} pending</span>
          </div>
          <div className="workbench-list">
            {approvals.map((approval) => (
              <article key={approval.id} className="workbench-card">
                <div className="workbench-card-header">
                  <strong>#{approval.id} {approval.approval_type}</strong>
                  <span className="mini-pill">{approval.status}</span>
                </div>
                <p>{approval.prompt}</p>
                {approval.status === "pending" ? (
                  <div className="inline-actions">
                    <button type="button" className="primary-button" onClick={() => onDecision(approval.id, true)}>
                      Approve
                    </button>
                    <button type="button" className="secondary-button" onClick={() => onDecision(approval.id, false)}>
                      Reject
                    </button>
                  </div>
                ) : null}
              </article>
            ))}
            {!approvals.length ? <p className="empty-inline">No approvals in this session.</p> : null}
          </div>
        </section>
      );
    }

    if (activeWorkbenchTab === "memory") {
      return (
        <section className="workbench-section">
          <div className="section-heading">
            <div>
              <p className="micro-label">Context</p>
              <h3>Memory</h3>
            </div>
            <span className="section-count">{sessionMemory.length}</span>
          </div>
          <div className="workbench-list">
            {sessionMemory.map((entry) => (
              <article key={entry.id} className="workbench-card">
                <div className="workbench-card-header">
                  <strong>{entry.kind}</strong>
                  <span className="mini-pill">{entry.status}</span>
                </div>
                <p>{entry.content}</p>
                <p>Salience: {entry.salience}</p>
                {entry.path_ref ? <p>Path: {entry.path_ref}</p> : null}
                {entry.source_turn_id ? <p>Turn #{entry.source_turn_id}</p> : null}
              </article>
            ))}
            {!sessionMemory.length ? <p className="empty-inline">No memory entries in this session yet.</p> : null}
          </div>
        </section>
      );
    }

    if (activeWorkbenchTab === "turns") {
      return (
        <section className="workbench-section">
          <div className="section-heading">
            <div>
              <p className="micro-label">Lifecycle</p>
              <h3>Turns</h3>
            </div>
            <span className="section-count">{turns.length}</span>
          </div>
          <div className="workbench-list">
            {turns.map((turn) => (
              <article key={turn.id} className="workbench-card">
                <div className="workbench-card-header">
                  <strong>Turn #{turn.id}</strong>
                  <span className="mini-pill">{turn.status}</span>
                </div>
                <p>{formatSessionStamp(turn.updated_at)}</p>
                <p>Checkpoint seq: {turn.last_checkpoint_seq}</p>
                <p>{turn.workspace_path ?? "No workspace recorded."}</p>
                {turn.resume_hint ? <p>{turn.resume_hint}</p> : null}
                {turn.error_summary ? <p>{turn.error_summary}</p> : null}
              </article>
            ))}
            {!turns.length ? <p className="empty-inline">No turns in this session yet.</p> : null}
          </div>
        </section>
      );
    }

    if (activeWorkbenchTab === "logs") {
      return (
        <section className="workbench-section">
          <div className="section-heading">
            <div>
              <p className="micro-label">Audit</p>
              <h3>Logs</h3>
            </div>
          </div>
          <div className="log-stack">
            <div className="workbench-list">
              {executions.map((execution) => (
                <button
                  key={execution.id}
                  type="button"
                  className={execution.id === selectedExecutionId ? "workbench-card active-card" : "workbench-card"}
                  onClick={() => setSelectedExecutionId(execution.id)}
                >
                  <div className="workbench-card-header">
                    <strong>{execution.tool_name}</strong>
                    <span className="mini-pill">{execution.status}</span>
                  </div>
                  <p>{formatSessionStamp(execution.created_at)}</p>
                </button>
              ))}
              {!executions.length ? <p className="empty-inline">No tool executions yet.</p> : null}
            </div>
            {selectedExecution ? (
              <article className="workbench-card log-detail-card">
                <div className="section-heading compact">
                  <h3>{selectedExecution.tool_name}</h3>
                  <span className="mini-pill">{selectedExecution.status}</span>
                </div>
                <p className="detail-label">Input</p>
                <pre>{selectedExecution.input_json ?? "(no input)"}</pre>
                <p className="detail-label">Output</p>
                <pre>{selectedExecution.output_text ?? "(no output)"}</pre>
              </article>
            ) : null}
          </div>
        </section>
      );
    }

    if (activeWorkbenchTab === "subagents") {
      return (
        <section className="workbench-section">
          <div className="section-heading">
            <div>
              <p className="micro-label">Parallel Work</p>
              <h3>Subagents</h3>
            </div>
            <button type="button" className="secondary-button" onClick={onRunSubagent}>Run subagent</button>
          </div>
          <textarea
            value={subagentDraft}
            onChange={(e) => setSubagentDraft(e.target.value)}
            className="workbench-textarea"
            rows={4}
          />
          <div className="workbench-list">
            {subagents.map((subagent) => (
              <article key={subagent.id} className="workbench-card">
                <div className="workbench-card-header">
                  <strong>{subagent.name}</strong>
                  <span className="mini-pill">{subagent.status}</span>
                </div>
                <p>{subagent.role}</p>
              </article>
            ))}
            {!subagents.length ? <p className="empty-inline">No subagents yet.</p> : null}
          </div>
        </section>
      );
    }

    return (
      <section className="workbench-section">
        <div className="section-heading">
          <div>
            <p className="micro-label">Collaborators</p>
            <h3>Teammates</h3>
          </div>
          <button type="button" className="secondary-button" onClick={onCreateTeammate}>Add scout</button>
        </div>
        <div className="workbench-list">
          {teammates.map((teammate) => (
            <button
              key={teammate.id}
              type="button"
              className={teammate.id === selectedTeammateId ? "workbench-card active-card" : "workbench-card"}
              onClick={() => setSelectedTeammateId(teammate.id)}
            >
              <div className="workbench-card-header">
                <strong>{teammate.name}</strong>
                <span className="mini-pill">{teammate.status}</span>
              </div>
              <p>{teammate.role}</p>
            </button>
          ))}
          {!teammates.length ? <p className="empty-inline">No teammates yet.</p> : null}
        </div>
        <textarea
          value={teammateDraft}
          onChange={(e) => setTeammateDraft(e.target.value)}
          className="workbench-textarea"
          rows={4}
        />
        <div className="inline-actions">
          <button type="button" className="primary-button" onClick={onSendTeammateMessage}>Send brief</button>
        </div>
        {selectedTeammate ? (
          <div className="workbench-list">
            {teammateMessages.map((message) => (
              <article key={message.id} className="workbench-card">
                <div className="workbench-card-header">
                  <strong>{selectedTeammate.name}</strong>
                  <span className="mini-pill">{message.direction}</span>
                </div>
                <p>{message.content}</p>
              </article>
            ))}
            {!teammateMessages.length ? <p className="empty-inline">No messages yet.</p> : null}
          </div>
        ) : null}
      </section>
    );
  }

  function renderSkillsPanel() {
    return (
      <section className="workbench-section">
        <div className="section-heading">
          <div>
            <p className="micro-label">Project local</p>
            <h3>Skills</h3>
          </div>
          <span className="section-count">{skills.length}</span>
        </div>
        <div className="workbench-list">
          {skills.map((skill) => (
            <article key={`${skill.name}:${skill.path}`} className="workbench-card">
              <div className="workbench-card-header">
                <strong>{skill.name}</strong>
              </div>
              <p>{skill.path}</p>
            </article>
          ))}
          {!skills.length ? <p className="empty-inline">No project-local skills found.</p> : null}
        </div>
      </section>
    );
  }

  return (
    <div className="app-shell">
      <aside className="left-rail">
        <div className="left-rail-top">
          <div className="brand-block">
            <div className="brand-mark">
              <img src={APP_LOGO_SRC} alt="Jarvis logo" className="brand-logo" />
            </div>
            <div className="brand-copy">
              <p className="micro-label">Local coding agent</p>
              <h1>Jarvis</h1>
            </div>
          </div>

          <button type="button" className="primary-button full-width" onClick={onCreateSession}>
            New Session
          </button>

          <label className="search-block">
            <input
              value={sessionSearch}
              onChange={(e) => setSessionSearch(e.target.value)}
              placeholder="Search conversations"
            />
          </label>

          <div className="rail-meta">
            <button
              type="button"
              className={workbenchOpen && sidePanelMode === "skills" ? "rail-link active-link" : "rail-link"}
              onClick={openSkillsPanel}
            >
              Skills
            </button>
          </div>
        </div>

        <div className="session-column">
          <div className="session-list">
            {groupedSessions.map((group) => (
              <details key={group.key} className="session-drawer" open>
                <summary className="session-drawer-heading">
                  <svg viewBox="0 0 20 20" aria-hidden="true" className="drawer-folder-icon">
                    <path d="M2.5 5.5a2 2 0 0 1 2-2h3l1.4 1.8H15.5a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2z" />
                  </svg>
                  <strong>{group.label}</strong>
                </summary>
                <div className="session-drawer-body">
                  {group.sessions.map((session) => (
                    <button
                      key={session.session_id}
                      type="button"
                      className={session.session_id === activeSessionId ? "session-card active-card" : "session-card"}
                      onClick={() => setActiveSessionId(session.session_id)}
                      onContextMenu={(event) => {
                        if (session.isDraft) return;
                        event.preventDefault();
                        setSessionContextMenu({
                          sessionId: session.session_id,
                          x: event.clientX,
                          y: event.clientY,
                        });
                      }}
                    >
                      <strong>{session.title}</strong>
                      <span>{formatSessionStamp(session.updated_at ?? session.created_at)}</span>
                    </button>
                  ))}
                </div>
              </details>
            ))}
            {!groupedSessions.length ? <p className="empty-inline">No matching sessions.</p> : null}
          </div>
        </div>

        <div className="left-rail-footer">
          <span className={`connection-pill ${connectionState}`}>{connectionState}</span>
          <span className="rail-hint">{executions.length} runtime events</span>
        </div>
      </aside>

      <main className="workspace-shell">
        <section className="workspace-panel">
        <header className="workspace-header">
          <div className="workspace-title">
            <h2 className="session-heading">{activeSession?.title ?? "Jarvis"}</h2>
            <p className="workspace-subtitle">
              {activeSession
                ? `${activeSession.workspace_label || "Workspace pending"} · ${activeSession.status}${activeSessionStamp ? ` · Updated ${activeSessionStamp}` : ""}`
                : "Choose a session or create a new one to begin."}
            </p>
          </div>
          <div className="header-actions">
            {pendingApprovals.length ? <span className="header-chip alert">{pendingApprovals.length} pending</span> : null}
            <button
              type="button"
              className="secondary-button"
              onClick={() => openWorkbenchTab(pendingApprovals.length ? "approvals" : "logs")}
            >
              Open Workbench
              {pendingApprovals.length ? <span className="button-count">{pendingApprovals.length}</span> : null}
            </button>
          </div>
        </header>

        <section className="workspace-body">
          {bootstrapState !== "ready" ? (
            <div className="empty-state offline-state">
              <p className="micro-label">Runtime</p>
              <h3>Waiting for Jarvis runtime</h3>
              <p>The frontend is ready, but the local backend has not connected yet. Keep this window open while the service starts.</p>
            </div>
          ) : !activeSession ? (
            <div className="empty-state">
              <p className="micro-label">Workspace ready</p>
              <h3>Start a fresh thread</h3>
              <p>Create a new session from the left rail to begin a focused agent conversation.</p>
            </div>
          ) : (
            <div className="conversation-frame">
              <div className="timeline-stream" ref={timelineRef} onScroll={onTimelineScroll}>
                {timelineItems.map((item) => {
                  if (item.kind === "tool-group") {
                    const startedAt = item.events[0]?.created_at ?? "";
                    return (
                      <article key={item.key} className="timeline-collapsible-card status">
                        <details>
                          <summary>
                            <div className="timeline-inline-meta">
                              <span>Tool Activity</span>
                              <span>{item.events.length} events</span>
                            </div>
                            <span className="timeline-collapse-stamp">{formatTimelineStamp(startedAt)}</span>
                          </summary>
                          <div className="grouped-activity-list">
                            {item.events.map((event, index) => (
                              <div key={`${event.created_at}-${index}`} className="grouped-activity-row">
                                <span>{formatTimelineStamp(event.created_at)}</span>
                                <p>{summarizeExecution(event.content)}</p>
                              </div>
                            ))}
                          </div>
                        </details>
                      </article>
                    );
                  }

                  const { event, card } = item;
                  const isSubagentSummary = event.type === "subagent.summary";
                  return (
                    <article
                      key={item.key}
                      className={
                        card.layout === "inline"
                          ? `timeline-inline-row ${card.tone}`
                          : `timeline-card ${card.kind} ${card.tone}`
                      }
                    >
                      {card.layout === "inline" ? (
                        <>
                          <div className="timeline-inline-meta">
                            <span>{card.label}</span>
                            <span>{formatTimelineStamp(event.created_at)}</span>
                          </div>
                          <p>{card.content}</p>
                        </>
                      ) : (
                        <>
                          <div className="timeline-meta">
                            <span className="mini-pill">{card.label}</span>
                            <span>{formatTimelineStamp(event.created_at)}</span>
                          </div>
                          <h3>{card.title}</h3>
                          {isSubagentSummary ? (
                            <details className="summary-collapsible">
                              <summary>{previewText(card.content)}</summary>
                              <div className="markdown-body">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {card.content}
                                </ReactMarkdown>
                              </div>
                            </details>
                          ) : card.tone === "assistant" ? (
                            renderTimelineParts(card.parts ?? [], card.content)
                          ) : (
                            <p>{card.content}</p>
                          )}
                        </>
                      )}
                    </article>
                  );
                })}
                {!timelineItems.length ? (
                  <div className="empty-state compact-state">
                    <p className="micro-label">Ready</p>
                    <h3>This session is empty</h3>
                    <p>Use the composer below to ask Jarvis to inspect files, run tools, or summarize work.</p>
                  </div>
                ) : null}
              </div>
              {showScrollToBottom ? (
                <button type="button" className="scroll-to-bottom" onClick={scrollTimelineToBottom}>
                  回到底部
                </button>
              ) : null}
            </div>
          )}
        </section>

        {activeSession?.status === "interrupted" ? (
          <div className="inline-approval-stack">
            <article className="inline-approval-bar">
              <div className="approval-copy">
                <p className="micro-label">Recovered session</p>
                <strong>Previous turn was interrupted</strong>
                <p>
                  {activeSessionState?.latest_interrupted_turn?.resume_hint
                    ?? "Jarvis restored this session to a safe idle point. Your next message can continue the work from the current workspace."}
                </p>
                {activeSessionState?.rolling_summary ? (
                  <p>{activeSessionState.rolling_summary}</p>
                ) : null}
              </div>
              {activeSessionState?.latest_interrupted_turn?.resumable ? (
                <div className="inline-actions">
                  <button type="button" className="primary-button" onClick={onResumeInterruptedTurn}>
                    Continue
                  </button>
                </div>
              ) : null}
            </article>
          </div>
        ) : null}

        {pendingApprovals.length ? (
          <div className="inline-approval-stack">
            {pendingApprovals.map((approval) => (
              <article key={approval.id} className="inline-approval-bar">
                <div className="approval-copy">
                  <p className="micro-label">Pending approval</p>
                  <strong>#{approval.id} {approval.approval_type}</strong>
                  <p>{approval.prompt}</p>
                </div>
                <div className="inline-actions">
                  <button type="button" className="primary-button" onClick={() => onDecision(approval.id, true)}>
                    Allow
                  </button>
                  <button type="button" className="secondary-button" onClick={() => onDecision(approval.id, false)}>
                    Reject
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : null}

        <form className="composer-shell" onSubmit={onSubmit}>
          <input
            ref={composerFileInputRef}
            type="file"
            multiple
            className="hidden-file-input"
            onChange={onComposerFilesSelected}
          />
          <div
            className="composer-frame"
            onDragOver={(event) => {
              event.preventDefault();
            }}
            onDrop={(event) => {
              event.preventDefault();
              if (event.dataTransfer.files?.length) {
                void uploadComposerFiles(event.dataTransfer.files);
              }
            }}
          >
            {selectedComposerAssets.length ? (
              <div className="attachment-tray">
                {selectedComposerAssets.map((asset) => {
                  const isSelected = selectedAssetIds.includes(asset.id);
                  return (
                    <div
                      key={asset.id}
                      className={isSelected ? "attachment-chip selected" : "attachment-chip"}
                      onClick={() => onToggleAssetSelection(asset.id)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onToggleAssetSelection(asset.id);
                        }
                      }}
                    >
                      <div className="attachment-chip-main">
                        {asset.preview_path ? (
                          <img src={`file://${asset.preview_path}`} alt={asset.filename} className="attachment-preview" />
                        ) : (
                          <span className="attachment-kind">{asset.kind.toUpperCase()}</span>
                        )}
                        <div className="attachment-copy">
                          <strong>{asset.filename}</strong>
                          <span>{asset.status}</span>
                        </div>
                      </div>
                      {!activeSessionId.startsWith(DRAFT_SESSION_PREFIX) ? (
                        <button
                          type="button"
                          className="attachment-remove"
                          onClick={(event) => {
                            event.stopPropagation();
                            void onDeleteAsset(asset.id);
                          }}
                          aria-label={`Delete ${asset.filename}`}
                          title={`Delete ${asset.filename}`}
                        >
                          ×
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : null}
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onComposerKeyDown}
              placeholder="Message Jarvis"
              rows={3}
              disabled={!activeSessionId}
            />
          </div>
          <div className="composer-footer simple-footer">
            <span>
              {assetUploadError
                ? assetUploadError
                : isUploadingAssets
                  ? "Uploading attachments..."
                  : selectedAssetIds.length
                    ? `${selectedAssetIds.length} attachment(s) selected · Enter to send`
                    : "Enter to send"}
            </span>
            <div className="composer-action-group">
              <button
                type="button"
                className="secondary-button composer-attach-button"
                onClick={onComposerPickFiles}
                disabled={!activeSessionId || isUploadingAssets}
                aria-label="Add attachment"
                title="Add attachment"
              >
                +
              </button>
              {isActiveTurnRunning ? (
                <button
                  type="button"
                  className="primary-button composer-action-button stop"
                  onClick={onStopTurn}
                  aria-label="Stop current turn"
                  title="Stop current turn"
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true">
                    <rect x="5.5" y="5.5" width="9" height="9" rx="1.5" />
                  </svg>
                </button>
              ) : (
                <button
                  type="submit"
                  className="primary-button composer-action-button"
                  disabled={!activeSessionId || (!draft.trim() && !selectedAssetIds.length) || isUploadingAssets}
                  aria-label="Send message"
                  title="Send message"
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true">
                    <path d="M3.5 10.5 15.5 4l-3 12-2.2-4.2z" />
                    <path d="M7.8 11.2 12.4 8" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </form>
        </section>
      </main>

      <aside className="status-rail">
        <div className="status-rail-header">
          <p className="micro-label">Signals</p>
        </div>
        <div className="status-rail-list">
          {statusRailCards.map((card) => (
            <button
              key={card.tab}
              type="button"
              className={card.emphasis === "alert" ? "status-rail-card alert" : "status-rail-card"}
              onClick={() => openWorkbenchTab(card.tab)}
              aria-label={`${card.label}: ${card.value}`}
            >
              <span className="status-rail-card-label">{card.label}</span>
              <strong>{card.value}</strong>
            </button>
          ))}
        </div>
      </aside>

      <div
        className={workbenchOpen ? "workbench-backdrop open" : "workbench-backdrop"}
        onClick={() => setWorkbenchOpen(false)}
      />
      {sessionContextMenu ? (
        <div
          ref={sessionContextMenuRef}
          className="session-context-menu"
          style={{ left: sessionContextMenu.x, top: sessionContextMenu.y }}
        >
          <button
            type="button"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              void onRenameSession(sessionContextMenu.sessionId);
            }}
          >
            Rename
          </button>
          <button
            type="button"
            className="danger"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              void onDeleteSession(sessionContextMenu.sessionId);
            }}
          >
            Hide
          </button>
        </div>
      ) : null}
      {sessionDialog ? (
        <div className="dialog-backdrop" onClick={() => setSessionDialog(null)}>
          <div className="session-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="session-dialog-header">
              <h3>
                {sessionDialog.mode === "create"
                  ? "New Session"
                  : sessionDialog.mode === "rename"
                    ? "Rename Session"
                    : "Hide Session"}
              </h3>
            </div>
            {sessionDialog.mode === "create" ? (
              <div className="session-dialog-body">
                <p>Choose a workspace folder for a bound session, or start in the default conversation drawer.</p>
                {createSessionError ? <p className="dialog-error">{createSessionError}</p> : null}
              </div>
            ) : sessionDialog.mode === "rename" ? (
              <div className="session-dialog-body">
                <input
                  value={sessionDialog.value}
                  onChange={(event) =>
                    setSessionDialog((current) =>
                      current && current.mode === "rename"
                        ? {
                            ...current,
                            value: event.target.value,
                          }
                        : current,
                    )
                  }
                  placeholder="Session title"
                  autoFocus
                />
              </div>
            ) : (
              <div className="session-dialog-body">
                <p>Hide this session from the left rail? It will remain in the local database.</p>
              </div>
            )}
            <div className="session-dialog-actions">
              <button type="button" className="secondary-button" onClick={() => setSessionDialog(null)}>
                Cancel
              </button>
              {sessionDialog.mode === "create" ? (
                <>
                  <button type="button" className="secondary-button" onClick={() => void onCreateDefaultSession()}>
                    Start in Default Conversations
                  </button>
                  <button type="button" className="primary-button" onClick={() => void onCreateBoundSession()}>
                    Choose Folder
                  </button>
                </>
              ) : (
                <button type="button" className="primary-button" onClick={() => void confirmSessionDialog()}>
                  {sessionDialog.mode === "rename" ? "Save" : "Hide"}
                </button>
              )}
            </div>
          </div>
        </div>
      ) : null}
      <aside className={workbenchOpen ? "workbench-drawer open" : "workbench-drawer"}>
        <div className="drawer-header">
          <div>
            <p className="micro-label">{sidePanelMode === "skills" ? "Project local" : "Operational depth"}</p>
            <h2>{sidePanelMode === "skills" ? "Skills" : "Workbench"}</h2>
          </div>
          <button type="button" className="secondary-button" onClick={() => setWorkbenchOpen(false)}>
            Close
          </button>
        </div>

        {sidePanelMode === "workbench" ? (
          <div className="workbench-tabs">
            {WORKBENCH_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={tab.id === activeWorkbenchTab ? "tab-button active-tab" : "tab-button"}
                onClick={() => setActiveWorkbenchTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        ) : null}

        <div className="drawer-body">{sidePanelMode === "skills" ? renderSkillsPanel() : renderWorkbenchPanel()}</div>
      </aside>
    </div>
  );
}
