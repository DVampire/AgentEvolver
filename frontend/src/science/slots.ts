// Named positions in the conversation view that a panel can be registered into.
//
// The four shipped panels used to be imported and placed by `Conversation.tsx` directly.
// That works for panels we write and forecloses on panels we do not: adding one from
// outside this repository means editing `Conversation.tsx`, which means forking. A named
// registry is the cheapest thing that removes the fork without buying the rest of a slot
// system — no scoped stores, no declaration epochs, no ownership errors. Those exist
// upstream because dozens of plugins collided over one surface; four panels have not.
//
// Deliberately not general. Every entry receives the same three props, because every
// panel here already takes exactly those, and a registry whose contract is "whatever the
// caller passes" cannot be type-checked at the point that matters — the registration.
import type { ComponentType } from 'react';

import type { RequestFn } from '../canvas/types';
import type { GatewayEvent } from '../controllers/gateway';

/** What every panel in a slot is given. */
export interface SlotProps {
  request: RequestFn;
  subscribe: (listener: (event: GatewayEvent) => void) => () => void;
  sessionId: string;
}

/** The positions that exist. A name outside this union is a compile error, so a typo
 *  registers nothing rather than registering into a slot nobody renders. */
export type SlotName = 'conversation.above-thread' | 'conversation.below-thread';

export interface SlotEntry {
  id: string;
  Component: ComponentType<SlotProps>;
}

const registry = new Map<SlotName, SlotEntry[]>();

/**
 * Put a panel in a slot. Returns the disposer for *this* registration.
 *
 * The disposer removes the entry it added rather than the entry with that id, so a
 * reload that registers a replacement before disposing the old handle cannot have the
 * old cleanup delete the new panel.
 */
export function registerSlot(
  name: SlotName,
  id: string,
  Component: ComponentType<SlotProps>,
): () => void {
  const entry: SlotEntry = { id, Component };
  const entries = registry.get(name) ?? [];
  registry.set(name, [...entries.filter((e) => e.id !== id), entry]);
  return () => {
    const current = registry.get(name) ?? [];
    registry.set(name, current.filter((e) => e !== entry));
  };
}

/** What is registered in one slot, in registration order. */
export function slotEntries(name: SlotName): SlotEntry[] {
  return registry.get(name) ?? [];
}
