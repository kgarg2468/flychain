import Link from 'next/link';

import { gateway, type CapabilitySpec } from '@/lib/gateway';

async function loadData(): Promise<{ capabilities: CapabilitySpec[]; error: string | null }> {
  try {
    const capabilities = await gateway.listCapabilities();
    return { capabilities, error: null };
  } catch (e) {
    return { capabilities: [], error: (e as Error).message };
  }
}

export default async function HomePage() {
  const { capabilities, error } = await loadData();

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-10 px-6 py-12">
      <header className="flex flex-col gap-2">
        <span className="text-xs font-medium uppercase tracking-widest text-neutral-500">
          FlyChain
        </span>
        <h1 className="text-3xl font-semibold tracking-tight">Capability Workspace</h1>
        <p className="max-w-2xl text-neutral-600 dark:text-neutral-400">
          Every run is measured, and only better versions ship. Track a capability, let FlyChain
          auto-evaluate production traces against it, and promote adapters that measurably improve.
        </p>
      </header>

      <section className="flex items-center justify-between">
        <h2 className="text-xl font-medium">Tracked capabilities</h2>
        <Link
          href="/capabilities/new"
          className="rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
        >
          + New capability
        </Link>
      </section>

      {error ? (
        <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          <div className="font-medium">Gateway not reachable</div>
          <div className="mt-1 opacity-80">{error}</div>
          <div className="mt-2 text-xs opacity-60">
            Is the gateway running?{' '}
            <code className="rounded bg-red-100 px-1 py-0.5 dark:bg-red-900">
              docker compose up
            </code>
          </div>
        </div>
      ) : capabilities.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {capabilities.map((c) => (
            <li key={c.id}>
              <Link
                href={`/capabilities/${encodeURIComponent(c.id)}`}
                className="block rounded-lg border border-neutral-200 bg-white p-5 transition hover:border-neutral-400 hover:shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
              >
                <div className="font-mono text-xs text-neutral-500">{c.id}</div>
                <div className="mt-1 text-lg font-medium">{c.name}</div>
                <p className="mt-1 line-clamp-2 text-sm text-neutral-600 dark:text-neutral-400">
                  {c.description}
                </p>
                <div className="mt-3 flex gap-2 text-xs">
                  <Tag>{c.eval_dimensions.length} eval dimensions</Tag>
                  <Tag>{c.slice_rules.length} slice rules</Tag>
                  <Tag>{c.eligible_methods.join(' / ').toUpperCase()}</Tag>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded bg-neutral-100 px-2 py-0.5 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
      {children}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 p-8 text-center dark:border-neutral-700">
      <div className="text-sm font-medium">No capabilities tracked yet</div>
      <p className="mx-auto mt-1 max-w-md text-sm text-neutral-500">
        Pick a recommended template (groundedness, instruction following, code correctness,
        uncertainty calibration, multi-step reasoning) or describe a capability in your own words.
      </p>
      <Link
        href="/capabilities/new"
        className="mt-4 inline-block rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900"
      >
        Create your first capability
      </Link>
    </div>
  );
}
