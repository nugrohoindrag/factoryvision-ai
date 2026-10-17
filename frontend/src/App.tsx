import { useEffect, useState } from "react";

type Health = { status: string; warehouse: string };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  return (
    <main>
      <h1>Factory Vision AI</h1>
      <p>Order-to-Delivery copilot with a live digital twin.</p>
      <p data-testid="api-status">API: {health ? `${health.status} (${health.warehouse})` : "offline"}</p>
    </main>
  );
}
