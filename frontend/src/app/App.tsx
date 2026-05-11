import { FormEvent, useEffect, useState } from "react";

import {
  decideApproval,
  createTask,
  createTeammate,
  fetchApprovals,
  fetchBootstrap,
  fetchTeammateMessages,
  fetchTeammates,
  fetchTimeline,
  fetchToolExecutions,
  sendMessage,
  sendTeammateMessage,
  type ApprovalSummary,
  type SessionSummary,
  type TaskSummary,
  type TeammateMessageSummary,
  type TeammateSummary,
  type TimelineEvent,
  type ToolExecutionSummary,
} from "../lib/api";

export function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [teammates, setTeammates] = useState<TeammateSummary[]>([]);
  const [selectedTeammateId, setSelectedTeammateId] = useState<number | null>(null);
  const [teammateMessages, setTeammateMessages] = useState<TeammateMessageSummary[]>([]);
  const [teammateDraft, setTeammateDraft] = useState("Review the latest runtime activity.");
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [executions, setExecutions] = useState<ToolExecutionSummary[]>([]);
  const [selectedExecutionId, setSelectedExecutionId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    fetchBootstrap().then((data) => {
      setSessions(data.sessions);
      setTasks(data.tasks);
      setTeammates(data.teammates);
      setSelectedTeammateId(data.teammates[0]?.id ?? null);
      setApprovals(data.approvals);
      setExecutions(data.tool_executions);
      setSelectedExecutionId(data.tool_executions[0]?.id ?? null);
      setActiveSessionId(data.sessions[0]?.session_id ?? "");
    });
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    fetchTimeline(activeSessionId).then(setEvents).catch(() => setEvents([]));
    fetchTeammates(activeSessionId)
      .then((items) => {
        setTeammates(items);
        setSelectedTeammateId((current) => current ?? items[0]?.id ?? null);
      })
      .catch(() => setTeammates([]));
    fetchApprovals(activeSessionId).then(setApprovals).catch(() => setApprovals([]));
    fetchToolExecutions(activeSessionId)
      .then((items) => {
        setExecutions(items);
        setSelectedExecutionId(items[0]?.id ?? null);
      })
      .catch(() => setExecutions([]));
  }, [activeSessionId]);

  useEffect(() => {
    if (!selectedTeammateId) return;
    fetchTeammateMessages(selectedTeammateId)
      .then(setTeammateMessages)
      .catch(() => setTeammateMessages([]));
  }, [selectedTeammateId]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!activeSessionId || !draft.trim()) return;
    const content = draft.trim();
    setDraft("");
    await sendMessage(activeSessionId, content);
    setEvents(await fetchTimeline(activeSessionId));
    setApprovals(await fetchApprovals(activeSessionId));
    const nextExecutions = await fetchToolExecutions(activeSessionId);
    setExecutions(nextExecutions);
    setSelectedExecutionId(nextExecutions[0]?.id ?? null);
  }

  async function onCreateTask() {
    if (!activeSessionId) return;
    const task = await createTask("Follow up latest runtime turn", activeSessionId);
    setTasks((current) => [task, ...current]);
  }

  async function onDecision(approvalId: number, approve: boolean) {
    await decideApproval(approvalId, approve);
    if (!activeSessionId) return;
    setApprovals(await fetchApprovals(activeSessionId));
    setEvents(await fetchTimeline(activeSessionId));
    const nextExecutions = await fetchToolExecutions(activeSessionId);
    setExecutions(nextExecutions);
    setSelectedExecutionId(nextExecutions[0]?.id ?? null);
  }

  async function onCreateTeammate() {
    if (!activeSessionId) return;
    const teammate = await createTeammate(
      activeSessionId,
      `Scout ${teammates.length + 1}`,
      "Scout",
    );
    setTeammates((current) => [teammate, ...current]);
    setSelectedTeammateId(teammate.id);
  }

  async function onSendTeammateMessage() {
    if (!selectedTeammateId || !teammateDraft.trim() || !activeSessionId) return;
    await sendTeammateMessage(selectedTeammateId, teammateDraft.trim());
    setTeammateMessages(await fetchTeammateMessages(selectedTeammateId));
    setTeammates(await fetchTeammates(activeSessionId));
    setEvents(await fetchTimeline(activeSessionId));
  }

  const selected = executions.find((item) => item.id === selectedExecutionId) ?? null;

  return (
    <div className="shell">
      <h1>Jarvis Agent Cockpit</h1>
      <div className="cockpit">
        <aside className="panel">
          <h2>Sessions</h2>
          {sessions.map((session) => (
            <button
              key={session.session_id}
              className={session.session_id === activeSessionId ? "session-card active" : "session-card"}
              onClick={() => setActiveSessionId(session.session_id)}
              type="button"
            >
              {session.title}
            </button>
          ))}
        </aside>

        <section className="panel">
          <h2>Lead Session</h2>
          <div className="timeline">
            {events.map((entry, index) => (
              <article key={`${entry.created_at}-${index}`} className="timeline-event">
                <span className="pill">{entry.type}</span>
                <p>{entry.content}</p>
              </article>
            ))}
          </div>
          <form className="composer" onSubmit={onSubmit}>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={"show files\n\nread README.md\n\nbash: pwd\n\nwrite data/note.txt\n<<<\nhello\n>>>"}
              rows={8}
            />
            <button type="submit">Send Turn</button>
          </form>
        </section>

        <aside className="panel">
          <div className="ops-grid">
            <article className="ops-card">
              <h3>Tasks</h3>
              <button type="button" onClick={onCreateTask}>New Task</button>
              <ul>{tasks.slice(0, 4).map((task) => <li key={task.id}>{task.subject}</li>)}</ul>
            </article>
            <article className="ops-card">
              <h3>Teammates</h3>
              <button type="button" onClick={onCreateTeammate}>Add Scout</button>
              <div className="teammate-list">
                {teammates.slice(0, 4).map((teammate) => (
                  <button
                    key={teammate.id}
                    type="button"
                    className={teammate.id === selectedTeammateId ? "teammate-entry active" : "teammate-entry"}
                    onClick={() => setSelectedTeammateId(teammate.id)}
                  >
                    <strong>{teammate.name}</strong>
                    <p>{teammate.role} [{teammate.status}]</p>
                  </button>
                ))}
              </div>
              <textarea
                value={teammateDraft}
                onChange={(e) => setTeammateDraft(e.target.value)}
                rows={3}
              />
              <button type="button" onClick={onSendTeammateMessage}>Send Brief</button>
              <div className="teammate-thread">
                {teammateMessages.slice(0, 4).map((message) => (
                  <div key={message.id} className="teammate-message">
                    <strong>{message.direction}</strong>
                    <p>{message.content}</p>
                  </div>
                ))}
              </div>
            </article>
            <article className="ops-card">
              <h3>Approvals</h3>
              <div className="approval-list">
                {approvals.slice(0, 4).map((approval) => (
                  <div key={approval.id} className="approval-entry">
                    <strong>#{approval.id} {approval.approval_type}</strong>
                    <p>{approval.status}</p>
                    <pre>{approval.prompt}</pre>
                    {approval.status === "pending" ? (
                      <div className="approval-actions">
                        <button type="button" onClick={() => onDecision(approval.id, true)}>Approve</button>
                        <button type="button" className="ghost" onClick={() => onDecision(approval.id, false)}>Reject</button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </article>
            <article className="ops-card">
              <h3>Logs</h3>
              <div className="log-list">
                {executions.slice(0, 6).map((execution) => (
                  <button
                    key={execution.id}
                    type="button"
                    className={execution.id === selectedExecutionId ? "log-entry active" : "log-entry"}
                    onClick={() => setSelectedExecutionId(execution.id)}
                  >
                    <strong>{execution.tool_name} [{execution.status}]</strong>
                    <p>{new Date(execution.created_at).toLocaleString()}</p>
                  </button>
                ))}
              </div>
              {selected ? (
                <div className="log-detail">
                  <p><strong>Input</strong></p>
                  <pre>{selected.input_json ?? "(no input)"}</pre>
                  <p><strong>Output</strong></p>
                  <pre>{selected.output_text ?? "(no output)"}</pre>
                </div>
              ) : null}
            </article>
          </div>
        </aside>
      </div>
    </div>
  );
}
