import { FormEvent, useEffect, useState } from "react";

import { createTask, fetchBootstrap, fetchTimeline, sendMessage, type SessionSummary, type TaskSummary, type TimelineEvent } from "../lib/api";

const panels = ["Tasks", "Teammates", "Approvals", "Logs"];

export function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    fetchBootstrap().then((data) => {
      setSessions(data.sessions);
      setTasks(data.tasks);
      setActiveSessionId(data.sessions[0]?.session_id ?? "");
    });
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    fetchTimeline(activeSessionId).then(setEvents).catch(() => setEvents([]));
  }, [activeSessionId]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!activeSessionId || !draft.trim()) return;
    const content = draft.trim();
    setDraft("");
    await sendMessage(activeSessionId, content);
    const next = await fetchTimeline(activeSessionId);
    setEvents(next);
  }

  async function onCreateTask() {
    if (!activeSessionId) return;
    const task = await createTask("Follow up latest runtime turn", activeSessionId);
    setTasks((current) => [task, ...current]);
  }

  return <div className="shell"><h1>Jarvis Agent Cockpit</h1><div className="cockpit"><aside className="panel">{sessions.map((session) => <button key={session.session_id} className={session.session_id === activeSessionId ? "session-card active" : "session-card"} onClick={() => setActiveSessionId(session.session_id)} type="button">{session.title}</button>)}</aside><section className="panel"><div className="timeline">{events.map((entry, index) => <article key={`${entry.created_at}-${index}`} className="timeline-event"><span className="pill">{entry.type}</span><p>{entry.content}</p></article>)}</div><form className="composer" onSubmit={onSubmit}><textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={4} /><button type="submit">Send Turn</button></form></section><aside className="panel"><div className="ops-grid">{panels.map((panel) => <article key={panel} className="ops-card"><h3>{panel}</h3>{panel === "Tasks" ? <><button type="button" onClick={onCreateTask}>New Task</button><ul>{tasks.slice(0, 4).map((task) => <li key={task.id}>{task.subject}</li>)}</ul></> : null}</article>)}</div></aside></div></div>;
}
