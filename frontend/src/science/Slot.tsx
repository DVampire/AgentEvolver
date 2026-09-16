import { registerSlot, slotEntries, type SlotName, type SlotProps } from './slots';

/** Render everything registered in one slot. Renders nothing when the slot is empty. */
export function Slot({ name, ...props }: { name: SlotName } & SlotProps) {
  return (
    <>
      {slotEntries(name).map(({ id, Component }) => <Component key={id} {...props} />)}
    </>
  );
}

export { registerSlot, slotEntries, type SlotName, type SlotProps };
