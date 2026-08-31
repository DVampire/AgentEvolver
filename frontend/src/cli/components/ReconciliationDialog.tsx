import { Box, Text } from 'ink';

import type { ReconciliationState } from '../../reconciliation.js';

/** Terminal counterpart of the non-dismissible browser recovery prompt. */
export function ReconciliationDialog({ reconciliation }: {
  reconciliation: ReconciliationState;
}) {
  const call = reconciliation.calls[0];
  return (
    <Box borderStyle="double" borderColor="yellow" flexDirection="column" paddingX={1}>
      <Text bold color="yellow">Crash recovery confirmation required</Text>
      <Text>The process stopped before this action's result was recorded:</Text>
      <Text bold>{call.actionName}</Text>
      <Text dimColor>{call.actionType} · {call.callId}</Text>
      {Object.keys(call.arguments).length ? (
        <Text>{JSON.stringify(call.arguments)}</Text>
      ) : null}
      <Text>The action will not be repeated automatically.</Text>
      <Text dimColor>[y] already happened   [n] did not happen</Text>
      {reconciliation.calls.length > 1 ? (
        <Text dimColor>{reconciliation.calls.length - 1} more item(s) remain</Text>
      ) : null}
      {reconciliation.error ? <Text color="red">{reconciliation.error}</Text> : null}
    </Box>
  );
}
