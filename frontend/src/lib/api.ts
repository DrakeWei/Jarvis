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

export type TaskSummary = {
  id: number;
  subject: string;
  description: string;
  status: string;
  owner: string | null;
  created_at: string;
};

export type ApprovalSummary = {
  id: number;
  session_id: string | null;
  approval_type: string;
  status: string;
  prompt: string;
  feedback: string | null;
  created_at: string;
};

export type TeammateSummary = {
  id: number;
  session_id: string | null;
  name: string;
  role: string;
  kind: string;
  status: string;
  created_at: string;
};

export type TeammateMessageSummary = {
  id: number;
  agent_id: number;
  direction: string;
  content: string;
  created_at: string;
};

export type SubagentSummary = {
  id: number;
  session_id: string | null;
  name: string;
  role: string;
  kind: string;
  status: string;
  created_at: string;
};

export type ToolExecutionSummary = {
  id: number;
  session_id: string;
  tool_name: string;
  status: string;
  input_json: string | null;
  output_text: string | null;
  created_at: string;
};

const API_BASE = "http://127.0.0.1:8731/api";

export async function fetchBootstrap(): Promise<{
  app: string;
  sessions: SessionSummary[];
  panels: string[];
  tasks: TaskSummary[];
  teammates: TeammateSummary[];
  subagents: SubagentSummary[];
  approvals: ApprovalSummary[];
  tool_executions: ToolExecutionSummary[];
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

export async function fetchTimeline(sessionId: string): Promise<TimelineEvent[]> {
  const response = await fetch(`${API_BASE}/sessions/${sessionId}/timeline`);
  if (!response.ok) {
    throw new Error("Failed to load timeline");
  }
  return response.json();
}

export async function createTask(subject: string, sessionId: string): Promise<TaskSummary> {
  const response = await fetch(`${API_BASE}/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      subject,
      session_id: sessionId,
      description: "",
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to create task");
  }
  return response.json();
}

export async function fetchToolExecutions(
  sessionId?: string,
): Promise<ToolExecutionSummary[]> {
  const url = sessionId
    ? `${API_BASE}/tool-executions?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/tool-executions`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load tool executions");
  }
  return response.json();
}

export async function fetchApprovals(sessionId?: string): Promise<ApprovalSummary[]> {
  const url = sessionId
    ? `${API_BASE}/approvals?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/approvals`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load approvals");
  }
  return response.json();
}

export async function decideApproval(
  approvalId: number,
  approve: boolean,
): Promise<ApprovalSummary> {
  const response = await fetch(`${API_BASE}/approvals/${approvalId}/decision`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      approve,
      feedback: "",
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to decide approval");
  }
  return response.json();
}

export async function fetchTeammates(sessionId?: string): Promise<TeammateSummary[]> {
  const url = sessionId
    ? `${API_BASE}/teammates?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/teammates`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load teammates");
  }
  return response.json();
}

export async function createTeammate(
  sessionId: string,
  name: string,
  role: string,
): Promise<TeammateSummary> {
  const response = await fetch(`${API_BASE}/teammates`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      name,
      role,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to create teammate");
  }
  return response.json();
}

export async function fetchTeammateMessages(agentId: number): Promise<TeammateMessageSummary[]> {
  const response = await fetch(`${API_BASE}/teammates/${agentId}/messages`);
  if (!response.ok) {
    throw new Error("Failed to load teammate messages");
  }
  return response.json();
}

export async function sendTeammateMessage(
  agentId: number,
  content: string,
): Promise<{ sent: TeammateMessageSummary; reply: TeammateMessageSummary }> {
  const response = await fetch(`${API_BASE}/teammates/${agentId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    throw new Error("Failed to send teammate message");
  }
  return response.json();
}

export async function fetchSubagents(sessionId?: string): Promise<SubagentSummary[]> {
  const url = sessionId
    ? `${API_BASE}/subagents?session_id=${encodeURIComponent(sessionId)}`
    : `${API_BASE}/subagents`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Failed to load subagents");
  }
  return response.json();
}

export async function runSubagent(
  sessionId: string,
  name: string,
  prompt: string,
): Promise<{ subagent: SubagentSummary; summary: string }> {
  const response = await fetch(`${API_BASE}/subagents`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: sessionId,
      name,
      prompt,
    }),
  });
  if (!response.ok) {
    throw new Error("Failed to run subagent");
  }
  return response.json();
}

export function openSessionEvents(
  sessionId: string,
  onEvent: (event: TimelineEvent) => void,
  handlers?: {
    onOpen?: () => void;
    onClose?: () => void;
    onError?: () => void;
  },
): () => void {
  const socket = new WebSocket(`ws://127.0.0.1:8731/api/sessions/${sessionId}/events`);
  socket.onopen = () => handlers?.onOpen?.();
  socket.onmessage = (message) => {
    onEvent(JSON.parse(message.data) as TimelineEvent);
  };
  socket.onerror = () => handlers?.onError?.();
  socket.onclose = () => handlers?.onClose?.();
  return () => socket.close();
}
