# React component and state examples

Read only when the project uses React and these patterns are needed. These are illustrations,
not a requirement to adopt React, Tailwind, Storybook or a state library.

## Component Architecture

### File Structure

Colocate related code when it helps maintain a component. This is an optional example;
do not scaffold tests, stories or extra files merely to match it:

```
src/components/
  TaskList/
    TaskList.tsx          # Component implementation
    TaskList.test.tsx     # Tests
    TaskList.stories.tsx  # Storybook stories (if using)
    use-task-list.ts      # Custom hook (if complex state)
    types.ts              # Component-specific types (if needed)
```

### Component Patterns

**Prefer composition over configuration:**

Start from the content's semantic structure. Composition does not require a Card wrapper;
choose a section, list, table, figure or pane to suit the design.

```tsx
// Good: Composable
<section aria-labelledby="tasks-heading" className="task-section">
  <header className="section-heading">
    <h2 id="tasks-heading">Tasks</h2>
    <TaskFilters />
  </header>
  <TaskList tasks={tasks} onToggle={toggleTask} onDelete={deleteTask} />
</section>

// Avoid: Over-configured
<TaskSection
  title="Tasks"
  headerVariant="large"
  bodyPadding="md"
  content={<TaskList tasks={tasks} onToggle={toggleTask} onDelete={deleteTask} />}
/>
```

**Keep components focused:**

```tsx
// Good: Does one thing
export function TaskItem({ task, onToggle, onDelete }: TaskItemProps) {
  return (
    <li className="task-row">
      <label className="task-toggle">
        <input type="checkbox" checked={task.done} onChange={() => onToggle(task.id)} />
        <span className={task.done ? 'is-complete' : undefined}>{task.title}</span>
      </label>
      <button type="button" aria-label={`Delete ${task.title}`} onClick={() => onDelete(task.id)}>
        <TrashIcon aria-hidden="true" />
      </button>
    </li>
  );
}
```

**Separate data fetching from presentation:**

```tsx
// Container: handles data
export function TaskListContainer() {
  const { tasks, isLoading, error, refetch, toggleTask, deleteTask } = useTasks();

  if (isLoading) return <TaskListSkeleton />;
  if (error) return <ErrorState message="Failed to load tasks" retry={refetch} />;
  if (tasks.length === 0) return <EmptyState message="No tasks yet" />;

  return <TaskList tasks={tasks} onToggle={toggleTask} onDelete={deleteTask} />;
}

// Presentation: handles rendering
type TaskListProps = {
  tasks: Task[];
  onToggle: TaskItemProps['onToggle'];
  onDelete: TaskItemProps['onDelete'];
};

export function TaskList({ tasks, onToggle, onDelete }: TaskListProps) {
  return (
    <ul role="list" className="task-list">
      {tasks.map(task => (
        <TaskItem key={task.id} task={task} onToggle={onToggle} onDelete={onDelete} />
      ))}
    </ul>
  );
}
```

## State Management

**Choose the simplest approach that works:**

```
Local state (useState)           → Component-specific UI state
Lifted state                     → Shared between 2-3 sibling components
Context                          → Theme, auth, locale (read-heavy, write-rare)
URL state (searchParams)         → Filters, pagination, shareable UI state
Server state (React Query, SWR)  → Remote data with caching
Global store (Zustand, Redux)    → Complex client state shared app-wide
```

If pass-through props make a component tree hard to work with, consider composition,
moving state closer to its consumers or context. A fixed nesting depth is not a reason
to introduce a store. Keep rendering structure and styling independent enough that a
visual redesign does not rewrite the data model or break accessible control behavior.
