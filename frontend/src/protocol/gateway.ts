// Generated from agentevolver/gateway/types.py — do not edit.
//
// Regenerate with:
//   python -c "from agentevolver.gateway.typescript import write_typescript; write_typescript()"
//
// tests/test_gateway_contract.py fails while this file differs from what the Python
// models render, so an edit here is reverted by the next regeneration rather than kept.
//
// `kind` and `type` on GatewayEvent are two different axes and both are needed: `kind`
// says which envelope shape this is, `type` says which event it carries.

export const PROTOCOL_VERSION = 1;

// A request sent by an interactive client.
export interface GatewayCommand {
  id: string;
  method: string;
  params: Record<string, unknown>;
  protocol_version: number;
}

// Why a command was refused.
export interface GatewayError {
  code: string;
  message: string;
  // Where a refusal says what was wrong, as opposed to that something was. A client
  // that reads only `message` can report the failure but never explain it.
  details?: Record<string, unknown>;
}

// The direct result of a GatewayCommand.
export interface GatewayResponse {
  kind: 'response';
  id: string;
  ok: boolean;
  result: Record<string, unknown>;
  error?: GatewayError;
  // Echoed on every response, so a client can tell a server it no longer understands
  // from one that merely refused this command.
  protocol_version: number;
}

// An ordered, replayable server event.
export interface GatewayEvent {
  kind: 'event';
  type: string;
  payload: Record<string, unknown>;
  session_id?: string;
  // Which line of dialogue this belongs to. A project holds several and the Gateway
  // broadcasts every event to every client, so without this a client cannot tell its
  // own conversation's work from the one in the next tab. Absent on project-wide
  // events, such as a session opening or capabilities changing.
  conversation_id?: string;
  task_id?: string;
  seq_no: number;
  timestamp: string;
  protocol_version: number;
}

export type GatewayMessage = GatewayEvent | GatewayResponse;

export function isGatewayEvent(message: GatewayMessage): message is GatewayEvent {
  return message.kind === 'event';
}
