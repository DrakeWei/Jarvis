import { FormEvent, useEffect, useState } from "react";
import { fetchBootstrap, openSessionEvents, sendMessage, type SessionSummary, type TimelineEvent } from "../lib/api";

const panels = [["Tasks","Dependency graph","Claim queue","Blocked work"],["Teammates","Role roster","Inbox","Status lane"],["Approvals","Plan review","Sensitive actions","Decision log"],["Logs","Tool executions","Runtime notices","Background jobs"]] as const;

export function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("Connecting to backend...");

  useEffect(() => { fetchBootstrap().then((data) => { setSessions(data.sessions); setActiveSessionId(data.sessions[0]?.session_id ?? ""); setStatus("Backend connected. Runtime scaffold is ready."); }).catch(() => setStatus("Backend is not reachable yet. Start the Python API to enable live sessions.")); }, []);
  useEffect(() => { if (!activeSessionId) return; setEvents([]); return openSessionEvents(activeSessionId, (entry) => setEvents((current) => [...current, entry])); }, [activeSessionId]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!activeSessionId || !draft.trim()) return;
    const content = draft.trim();
    setDraft("");
    try { await sendMessage(activeSessionId, content); } catch { setStatus("Send failed. Check whether the backend is running."); }
  }

  return <div className="shell"><header className="masthead"><div><p className="eyebrow">Jarvis Agent Cockpit</p><h1>Editorial control room for a local multi-agent runtime.</h1></div><div className="status-card"><span className="status-label">Runtime</span><strong>{status}</strong></div></header><main className="cockpit"><aside className="panel"><p className="eyebrow">Sessions</p><h2>Workspace Deck</h2><div className="session-list">{sessions.length === 0 ? <div className="session-card muted">No live sessions yet.</div> : sessions.map((session) => <button key={session.session_id} className={session.session_id === activeSessionId ? "session-card active" : "session-card"} onClick={() => setActiveSessionId(session.session_id)}><strong>{session.title}</strong><span>{new Date(session.created_at).toLocaleString()}</span></button>)}</div></aside><section className="panel"><p className="eyebrow">Conversation Timeline</p><h2>Lead Session</h2><div className="timeline">{events.length === 0 ? <article className="timeline-event assistant"><span className="pill">assistant</span><p>This rail is ready for streamed output, tool calls, summaries, and runtime notices.</p></article> : events.map((entry, index) => <article key={`${entry.created_at}-${index}`} className="timeline-event"><span className="pill">{entry.type}</span><p>{entry.content}</p></article>)}</div><form className="composer" onSubmit={onSubmit}><textarea value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Send the lead agent a task, command, or coordination request..." rows={4} /><div className="composer-actions"><button type="submit">Send Turn</button><button className="ghost" type="button">Stop</button></div></form></section><aside className="panel"><p className="eyebrow">Operations</p><h2>Control Surface</h2><div className="ops-grid">{panels.map(([title, a, b, c]) => <article key={title} className="ops-card"><h3>{title}</h3><ul><li>{a}</li><li>{b}</li><li>{c}</li></ul></article>)}</div></aside></main></div>;
}
