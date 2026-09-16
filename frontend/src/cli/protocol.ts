// The CLI speaks the same protocol as the browser, so it reads the same generated
// declarations. This file used to declare them again by hand; nothing checked that copy,
// and it drifted from the server for as long as it existed.
export {
  PROTOCOL_VERSION,
  isGatewayEvent,
  type GatewayCommand,
  type GatewayError,
  type GatewayEvent,
  type GatewayMessage,
  type GatewayResponse,
} from '../protocol/gateway';
