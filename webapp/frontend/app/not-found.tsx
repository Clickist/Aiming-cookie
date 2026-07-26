import { Button, Notice, Panel } from "@/ui/primitives";

export default function NotFound() {
  return (
    <main className="ac-page-shell">
      <Panel title="Page not found">
        <Notice title="This route is unavailable." tone="warning">
          The page may have moved or the local product state may still be loading.
        </Notice>
        <p>
          <Button href="/" variant="secondary">Return to workspace</Button>
        </p>
      </Panel>
    </main>
  );
}
