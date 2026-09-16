// The panels this repository ships, placed into their slots.
//
// One import in `Conversation.tsx` reaches all of them, so adding a panel is a line here
// rather than an edit to the view's JSX — and a panel from outside the repository calls
// `registerSlot` from its own entry point and needs neither.
//
// Imported for effect. Without this file nothing would reference the modules and a
// bundler would drop the registrations along with them.
import { GoalCard } from './GoalCard';
import { PlanBar } from './PlanBar';
import { registerSlot } from './slots';

registerSlot('conversation.above-thread', 'goal', GoalCard);
registerSlot('conversation.below-thread', 'plan', PlanBar);
