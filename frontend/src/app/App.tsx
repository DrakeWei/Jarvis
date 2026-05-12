import { FormEvent, KeyboardEvent, useEffect, useState } from "react";

import {
  createSession,
  createTask,
  createTeammate,
  decideApproval,
  fetchApprovals,
  fetchBootstrap,
  fetchSubagents,
  fetchTeammateMessages,
  fetchTeammates,
  fetchTimeline,
  fetchToolExecutions,
  openSessionEvents,
  runSubagent,
  sendMessage,
  sendTeammateMessage,
  type ApprovalSummary,
  type SessionSummary,
  type SubagentSummary,
  type TaskSummary,
  type TeammateMessageSummary,
  type TeammateSummary,
  type TimelineEvent,
  type ToolExecutionSummary,
} from "../lib/api";

const ACTIVE_SESSION_KEY = "jarvis.activeSession";
const WORKBENCH_TABS = [
  { id: "tasks", label: "Tasks" },
  { id: "approvals", label: "Approvals" },
  { id: "logs", label: "Logs" },
  { id: "subagents", label: "Subagents" },
  { id: "teammates", label: "Teammates" },
] as const;

type WorkbenchTabId = (typeof WORKBENCH_TABS)[number]["id"];

type TimelineCard = {
  tone: "user" | "assistant" | "result" | "status";
  kind: "message" | "result" | "status";
  label: string;
  title: string;
  content: string;
};

function formatSessionStamp(timestamp: string): string {
  return new Date(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatTimelineStamp(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
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

function buildTimelineCard(event: TimelineEvent): TimelineCard {
  if (event.type === "message.user" || event.type === "message.user.local") {
    return {
      tone: "user",
      kind: "message",
      label: "Instruction",
      title: "You",
      content: event.content,
    };
  }

  if (event.type === "message.assistant") {
    return {
      tone: "assistant",
      kind: "message",
      label: "Response",
      title: "Jarvis",
      content: event.content,
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
      label: "Tool Activity",
      title: "Execution summary",
      content: summarizeExecution(event.content),
    };
  }

  if (event.type === "approval.requested") {
    return {
      tone: "status",
      kind: "status",
      label: "Approval",
      title: "Approval requested",
      content: event.content,
    };
  }

  if (event.type === "approval.resolved") {
    return {
      tone: "status",
      kind: "status",
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

  return {
    tone: "status",
    kind: "status",
    label: event.type.replace(".", " / "),
    title: "System event",
    content: event.content,
  };
}

function nextSessionTitle(): string {
  return "New Session";
}

function summarizeSessionTitle(content: string): string {
  let text = content.trim().replace(/\s+/g, " ");
  if (!text) return "New Session";
  const prefixes = [
    "请帮我",
    "帮我",
    "麻烦",
    "请",
    "能不能",
    "可以帮我",
    "can you ",
    "could you ",
    "please ",
    "help me ",
  ];
  for (const prefix of prefixes) {
    if (text.toLowerCase().startsWith(prefix.toLowerCase())) {
      text = text.slice(prefix.length).trim();
      break;
    }
  }
  text = text.replace(/^[\s.,!?:;，。！？：；"'()[\]{}]+|[\s.,!?:;，。！？：；"'()[\]{}]+$/g, "");
  if (!text) return "New Session";
  const hasCjk = /[\u4e00-\u9fff]/.test(text);
  if (hasCjk) return text.slice(0, 14);
  return text.split(" ").slice(0, 5).join(" ").slice(0, 36);
}

export function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [teammates, setTeammates] = useState<TeammateSummary[]>([]);
  const [subagents, setSubagents] = useState<SubagentSummary[]>([]);
  const [subagentDraft, setSubagentDraft] = useState("Scan the workspace and summarize the next technical priorities.");
  const [selectedTeammateId, setSelectedTeammateId] = useState<number | null>(null);
  const [teammateMessages, setTeammateMessages] = useState<TeammateMessageSummary[]>([]);
  const [teammateDraft, setTeammateDraft] = useState("Review the latest runtime activity.");
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [executions, setExecutions] = useState<ToolExecutionSummary[]>([]);
  const [selectedExecutionId, setSelectedExecutionId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [streamingAssistant, setStreamingAssistant] = useState<{ content: string; created_at: string } | null>(null);
  const [connectionState, setConnectionState] = useState("offline");
  const [bootstrapState, setBootstrapState] = useState("booting");
  const [sessionSearch, setSessionSearch] = useState("");
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [activeWorkbenchTab, setActiveWorkbenchTab] = useState<WorkbenchTabId>("approvals");

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
        setSessions(data.sessions);
        setTasks(data.tasks);
        setTeammates(data.teammates);
        setSelectedTeammateId(data.teammates[0]?.id ?? null);
        setSubagents(data.subagents);
        setApprovals(data.approvals);
        setExecutions(data.tool_executions);
        setSelectedExecutionId(data.tool_executions[0]?.id ?? null);
        setActiveSessionId(nextSessionId);
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
    window.localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
  }, [activeSessionId]);

  async function refreshSessionState(sessionId: string) {
    const [nextSubagents, nextApprovals, nextExecutions, nextTeammates] = await Promise.all([
      fetchSubagents(sessionId),
      fetchApprovals(sessionId),
      fetchToolExecutions(sessionId),
      fetchTeammates(sessionId),
    ]);

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
  }

  useEffect(() => {
    if (!activeSessionId) return;
    fetchTimeline(activeSessionId).then(setEvents).catch(() => setEvents([]));
    setStreamingAssistant(null);
    refreshSessionState(activeSessionId).catch(() => {
      setSubagents([]);
      setTeammates([]);
      setApprovals([]);
      setExecutions([]);
    });
  }, [activeSessionId]);

  useEffect(() => {
    if (!selectedTeammateId) return;
    fetchTeammateMessages(selectedTeammateId)
      .then(setTeammateMessages)
      .catch(() => setTeammateMessages([]));
  }, [selectedTeammateId]);

  useEffect(() => {
    if (!activeSessionId) return;
    let disposed = false;
    let retryTimer: number | undefined;

    const connect = () => {
      if (disposed) return () => {};
      setConnectionState("connecting");
      return openSessionEvents(
        activeSessionId,
        (event) => {
          if (event.type === "message.user") {
            setEvents((current) => {
              const localIndex = current.findIndex(
                (item) => item.type === "message.user.local" && item.content === event.content,
              );
              if (localIndex === -1) {
                return current.some(
                  (item) =>
                    item.created_at === event.created_at &&
                    item.type === event.type &&
                    item.content === event.content,
                )
                  ? current
                  : [...current, event];
              }
              const next = [...current];
              next.splice(localIndex, 1, event);
              return next;
            });
            return;
          }
          if (event.type === "message.assistant.delta") {
            setStreamingAssistant((current) => ({
              content: `${current?.content ?? ""}${event.content}`,
              created_at: current?.created_at ?? event.created_at,
            }));
            return;
          }
          if (event.type === "message.assistant") {
            setStreamingAssistant(null);
          }
          setEvents((current) => {
            const exists = current.some(
              (item) =>
                item.created_at === event.created_at &&
                item.type === event.type &&
                item.content === event.content,
            );
            return exists ? current : [...current, event];
          });
          if (
            event.type.startsWith("tool.") ||
            event.type.startsWith("approval.") ||
            event.type.startsWith("teammate.") ||
            event.type.startsWith("subagent.")
          ) {
            refreshSessionState(activeSessionId).catch(() => undefined);
          }
          if (event.type.startsWith("teammate.") && selectedTeammateId) {
            fetchTeammateMessages(selectedTeammateId).then(setTeammateMessages).catch(() => undefined);
          }
        },
        {
          onOpen: () => setConnectionState("live"),
          onError: () => setConnectionState("degraded"),
          onClose: () => {
            if (disposed) return;
            setConnectionState("reconnecting");
            retryTimer = window.setTimeout(() => {
              cleanup = connect();
            }, 1200);
          },
        },
      );
    };

    let cleanup = connect();
    return () => {
      disposed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      cleanup();
      setConnectionState("offline");
    };
  }, [activeSessionId, selectedTeammateId]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    await submitDraft();
  }

  async function submitDraft() {
    if (!activeSessionId || !draft.trim()) return;
    const content = draft.trim();
    const nextTitle = summarizeSessionTitle(content);
    setDraft("");
    const optimisticCreatedAt = new Date().toISOString();
    setSessions((current) =>
      current.map((session) =>
        session.session_id === activeSessionId && session.title.startsWith("New Session")
          ? { ...session, title: nextTitle }
          : session,
      ),
    );
    setEvents((current) => [
      ...current,
      {
        session_id: activeSessionId,
        type: "message.user.local",
        content,
        created_at: optimisticCreatedAt,
      } as TimelineEvent,
    ]);
    try {
      await sendMessage(activeSessionId, content);
      setStreamingAssistant(null);
    } catch (error) {
      setEvents((current) =>
        current.filter(
          (item) =>
            !(item.type === "message.user.local" && item.content === content && item.created_at === optimisticCreatedAt),
        ),
      );
      throw error;
    }
  }

  async function onCreateSession() {
    const session = await createSession(nextSessionTitle());
    setSessions((current) => [session, ...current]);
    setActiveSessionId(session.session_id);
    setEvents([]);
    setDraft("");
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
    setEvents(await fetchTimeline(activeSessionId));
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
    setEvents(await fetchTimeline(activeSessionId));
  }

  async function onRunSubagent() {
    if (!activeSessionId || !subagentDraft.trim()) return;
    await runSubagent(activeSessionId, `Explorer ${subagents.length + 1}`, subagentDraft.trim());
    await refreshSessionState(activeSessionId);
    setEvents(await fetchTimeline(activeSessionId));
    setActiveWorkbenchTab("subagents");
    setWorkbenchOpen(true);
  }

  const activeSession = sessions.find((session) => session.session_id === activeSessionId) ?? null;
  const filteredSessions = sessions.filter((session) =>
    session.title.toLowerCase().includes(sessionSearch.trim().toLowerCase()),
  );
  const pendingApprovals = approvals.filter((approval) => approval.status === "pending");
  const selectedExecution = executions.find((item) => item.id === selectedExecutionId) ?? null;
  const selectedTeammate = teammates.find((item) => item.id === selectedTeammateId) ?? null;
  const timelineCards = events
    .filter((event) => event.type !== "runtime.state")
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
          label: "Response",
          title: "Jarvis",
          content: streamingAssistant.content,
        },
      }
    : null;

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
            <button type="button" className="secondary-button" onClick={onRunSubagent}>Run explorer</button>
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

  return (
    <div className="app-shell">
      <aside className="left-rail">
        <div className="brand-block">
          <div className="brand-mark">J</div>
          <div>
            <p className="micro-label">Jarvis</p>
            <h1>Sessions</h1>
          </div>
        </div>

        <button type="button" className="primary-button full-width" onClick={onCreateSession}>
          New Session
        </button>

        <label className="search-block">
          <span className="micro-label">Search</span>
          <input
            value={sessionSearch}
            onChange={(e) => setSessionSearch(e.target.value)}
            placeholder="Find a conversation"
          />
        </label>

        <div className="rail-meta">
          <button type="button" className="rail-link active-link" onClick={() => setWorkbenchOpen(false)}>
            Sessions
          </button>
          <button type="button" className="rail-link" onClick={() => setWorkbenchOpen(true)}>
            Workbench
          </button>
          <span className="rail-hint">Skills</span>
          <span className="rail-hint">Plugins</span>
          <span className="rail-hint">Automations</span>
        </div>

        <div className="session-column">
          <div className="section-heading compact">
            <div>
              <p className="micro-label">Recent</p>
              <h2>Sessions</h2>
            </div>
            <span className="section-count">{sessions.length}</span>
          </div>

          <div className="session-list">
            {filteredSessions.map((session) => (
              <button
                key={session.session_id}
                type="button"
                className={session.session_id === activeSessionId ? "session-card active-card" : "session-card"}
                onClick={() => setActiveSessionId(session.session_id)}
              >
                <strong>{session.title}</strong>
                <span>{formatSessionStamp(session.created_at)}</span>
              </button>
            ))}
            {!filteredSessions.length ? <p className="empty-inline">No matching sessions.</p> : null}
          </div>
        </div>
      </aside>

      <main className="workspace-shell">
        <header className="workspace-header">
          <div className="workspace-title">
            <h2 className="session-heading">{activeSession?.title ?? "Jarvis"}</h2>
          </div>
          <div className="header-actions">
            <span className={`connection-pill ${connectionState}`}>{connectionState}</span>
            <button
              type="button"
              className="secondary-button"
              onClick={() => {
                setActiveWorkbenchTab(pendingApprovals.length ? "approvals" : "logs");
                setWorkbenchOpen((current) => !current);
              }}
            >
              Workbench
              {pendingApprovals.length ? <span className="button-count">{pendingApprovals.length}</span> : null}
            </button>
          </div>
        </header>

        <section className="workspace-body">
          {bootstrapState !== "ready" ? (
            <div className="empty-state offline-state">
              <p className="micro-label">Backend offline</p>
              <h3>Waiting for Jarvis runtime</h3>
              <p>The frontend is ready, but the local backend has not connected yet. Keep this window open while the service starts.</p>
            </div>
          ) : !activeSession ? (
            <div className="empty-state">
              <p className="micro-label">No session selected</p>
              <h3>Start a fresh thread</h3>
              <p>Create a new session from the left rail to begin a focused agent conversation.</p>
            </div>
          ) : (
            <div className="conversation-frame">
              <div className="timeline-stream">
                {[...timelineCards, ...(liveAssistantCard ? [liveAssistantCard] : [])].map(({ event, card }, index) => (
                  <article
                    key={`${event.created_at}-${index}`}
                    className={`timeline-card ${card.kind} ${card.tone}`}
                  >
                    <div className="timeline-meta">
                      <span className="mini-pill">{card.label}</span>
                      <span>{formatTimelineStamp(event.created_at)}</span>
                    </div>
                    <h3>{card.title}</h3>
                    <p>{card.content}</p>
                  </article>
                ))}
                {!timelineCards.length ? (
                  <div className="empty-state compact-state">
                    <p className="micro-label">Ready</p>
                    <h3>This session is empty</h3>
                    <p>Use the composer below to ask Jarvis to inspect files, run tools, or summarize work.</p>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </section>

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
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder="Message Jarvis"
            rows={5}
            disabled={!activeSessionId}
          />
          <div className="composer-footer simple-footer">
            <button type="submit" className="primary-button" disabled={!activeSessionId || !draft.trim()}>
              Send
            </button>
          </div>
        </form>
      </main>

      <aside className="status-rail">
        <div className="status-rail-header">
          <p className="micro-label">Overview</p>
          <button type="button" className="secondary-button compact-button" onClick={() => setWorkbenchOpen(true)}>
            Workbench
          </button>
        </div>
        <div className="status-rail-list">
          <article className="status-rail-card">
            <p className="micro-label">Approvals</p>
            <strong>{pendingApprovals.length}</strong>
            <span>{pendingApprovals.length ? "Pending review" : "No pending review"}</span>
          </article>
          <article className="status-rail-card">
            <p className="micro-label">Tools</p>
            <strong>{executions.length}</strong>
            <span>Recent executions</span>
          </article>
          <article className="status-rail-card">
            <p className="micro-label">Scouts</p>
            <strong>{teammates.length}</strong>
            <span>Active teammates</span>
          </article>
        </div>
      </aside>

      <div
        className={workbenchOpen ? "workbench-backdrop open" : "workbench-backdrop"}
        onClick={() => setWorkbenchOpen(false)}
      />
      <aside className={workbenchOpen ? "workbench-drawer open" : "workbench-drawer"}>
        <div className="drawer-header">
          <div>
            <p className="micro-label">Operational depth</p>
            <h2>Workbench</h2>
          </div>
          <button type="button" className="secondary-button" onClick={() => setWorkbenchOpen(false)}>
            Close
          </button>
        </div>

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

        <div className="drawer-body">{renderWorkbenchPanel()}</div>
      </aside>
    </div>
  );
}
