import { FormEvent, useEffect, useState } from "react";

import {
  createTask,
  fetchBootstrap,
  fetchTimeline,
  fetchToolExecutions,
  sendMessage,
  type SessionSummary,
  type TaskSummary,
  type TimelineEvent,
  type ToolExecutionSummary,
} from "../lib/api";

export function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [executions, setExecutions] = useState<ToolExecutionSummary[]>([]);
  const [selectedExecutionId, setSelectedExecutionId] = useState<number | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    fetchBootstrap().then((data) => {
      setSessions(data.sessions);
      setTasks(data.tasks);
      setExecutions(data.tool_executions);
      setSelectedExecutionId(data.tool_executions[0]?.id ?? null);
      setActiveSessionId(data.sessions[0]?.session_id ?? "");
    });
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    fetchTimeline(activeSessionId).then(setEvents).catch(() => setEvents([]));
    fetchToolExecutions(activeSessionId)
      .then((items) => {
        setExecutions(items);
        setSelectedExecutionId(items[0]?.id ?? null);
      })
      .catch(() => setExecutions([]));
  }, [activeSessionId]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!activeSessionId || !draft.trim()) return;
    const content = draft.trim();
    setDraft("");
    await sendMessage(activeSessionId, content);
    setEvents(await fetchTimeline(activeSessionId));
    const nextExecutions = await fetchToolExecutions(activeSessionId);
    setExecutions(nextExecutions);
    setSelectedExecutionId(nextExecutions[0]?.id ?? null);
  }

  async function onCreateTask() {
    if (!activeSessionId) return;
    const task = await createTask("Follow up latest runtime turn", activeSessionId);
    setTasks((current) => [task, ...current]);
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
              placeholder={"show files\nread README.md\nbash: pwd\nwrite data/note.txt\n<<<\nhello\n>>>"}
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
