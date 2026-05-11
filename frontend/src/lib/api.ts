export type SessionSummary = {
  session_id: string;
  title: string;
  created_at: string;
};

export type TimelineEvent = {
  session_id: string;
  type: string;
  content: string;
  created_at: string;
};

const API_BASE = "http://127.0.0.1:8731/api";

export async function fetchBootstrap(): Promise<{
  app: string;
  sessions: SessionSummary[];
  panels: string[];
}> {
  const response = await fetch(`${API_BASE}/bootstrap`);
  if (!response.ok) {
    throw new Error("Failed to load bootstrap state");
  }
  return response.json();
}

export async function sendMessage(
  sessionId: string,
  content: string,
): Promise<void> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      role: "user",
      content,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to send message");
  }
}

export function openSessionEvents(
  sessionId: string,
  onEvent: (event: TimelineEvent) => void,
): () => void {
  const socket = new WebSocket(`ws://127.0.0.1:8731/api/sessions/${sessionId}/events`);
  socket.onmessage = (message) => {
    onEvent(JSON.parse(message.data) as TimelineEvent);
  };
  return () => socket.close();
}
