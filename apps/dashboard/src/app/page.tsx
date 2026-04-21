import Link from 'next/link';

import { gateway, type ActiveAdapter, type CapabilitySpec, type Scorecard } from '@/lib/gateway';

interface CapabilitySnapshot {
  capability: CapabilitySpec;
  scorecard: Scorecard | null;
  activeAdapter: ActiveAdapter | null;
}

async function loadData(): Promise<{
  snapshots: CapabilitySnapshot[];
  error: string | null;
}> {
  try {
    const capabilities = await gateway.listCapabilities();
    const snapshots = await Promise.all(
      capabilities.map(async (capability) => {
        const [scorecard, activeAdapter] = await Promise.all([
          gateway.scorecard(capability.id).catch(() => null),
          gateway.activeAdapter(capability.id).catch(() => null),
        ]);
        return { capability, scorecard, activeAdapter };
      }),
    );
    return { snapshots, error: null };
  } catch (e) {
    return { snapshots: [], error: (e as Error).message };
  }
}

function deltaFromActive(snapshot: CapabilitySnapshot): number | null {
  const active = snapshot.activeAdapter?.active;
  if (!active) return null;
  const current = active.candidate[snapshot.capability.id];
  const baseline = active.baseline[snapshot.capability.id];
  if (typeof current !== 'number' || typeof baseline !== 'number') return null;
  return current - baseline;
}

export default async function HomePage() {
  const { snapshots, error } = await loadData();

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(16,185,129,0.10),_transparent_32%),linear-gradient(180deg,_#f8fafc_0%,_#eef2f7_100%)] text-neutral-900">
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col gap-8 px-6 py-10">
        <header className="grid gap-6 rounded-[2rem] border border-white/70 bg-white/85 p-8 shadow-[0_24px_80px_-48px_rgba(15,23,42,0.5)] backdrop-blur sm:grid-cols-[1.5fr_1fr]">
          <div className="space-y-4">
            <div className="flex items-center gap-3 text-xs font-semibold uppercase tracking-[0.28em] text-emerald-700">
              <span>FlyChain</span>
              <span className="h-px flex-1 bg-emerald-200" />
              <span>Operator Workspace</span>
            </div>
            <div className="space-y-3">
              <h1 className="max-w-3xl text-4xl font-semibold tracking-tight text-neutral-950">
                Capability scorecards, trace triage, and promotion state in one surface.
              </h1>
              <p className="max-w-2xl text-sm leading-6 text-neutral-600">
                Track the current aggregate for each capability, inspect adapter deltas, and jump
                into the next action without leaving the local stack.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-3 rounded-[1.5rem] border border-neutral-200 bg-neutral-950 p-5 text-neutral-100">
            <div className="text-xs font-semibold uppercase tracking-[0.24em] text-neutral-400">
              Workspace Actions
            </div>
            <Link
              href="/capabilities/new"
              className="rounded-full bg-emerald-400 px-4 py-3 text-sm font-medium text-neutral-950 transition hover:bg-emerald-300"
            >
              New capability
            </Link>
            <div className="grid grid-cols-2 gap-3">
              <Link
                href="/traces"
                className="rounded-2xl border border-neutral-800 px-4 py-3 text-sm text-neutral-300 transition hover:border-neutral-600 hover:text-white"
              >
                Trace explorer
              </Link>
              <Link
                href="/settings"
                className="rounded-2xl border border-neutral-800 px-4 py-3 text-sm text-neutral-300 transition hover:border-neutral-600 hover:text-white"
              >
                Settings
              </Link>
            </div>
          </div>
        </header>

        {error ? (
          <div className="rounded-[1.5rem] border border-red-200 bg-red-50 p-5 text-sm text-red-700">
            <div className="font-medium">Gateway not reachable</div>
            <div className="mt-1">{error}</div>
            <div className="mt-3 text-xs text-red-500">
              Start the local stack with <code>docker compose up -d</code>.
            </div>
          </div>
        ) : snapshots.length === 0 ? (
          <EmptyState />
        ) : (
          <section className="grid gap-4 lg:grid-cols-2">
            {snapshots.map((snapshot) => {
              const score = snapshot.scorecard?.aggregate_score;
              const delta = deltaFromActive(snapshot);
              return (
                <Link
                  key={snapshot.capability.id}
                  href={`/capabilities/${encodeURIComponent(snapshot.capability.id)}`}
                  className="group grid gap-4 rounded-[1.75rem] border border-white/70 bg-white/85 p-6 shadow-[0_18px_50px_-40px_rgba(15,23,42,0.55)] transition hover:-translate-y-0.5 hover:shadow-[0_28px_90px_-48px_rgba(15,23,42,0.55)]"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-2">
                      <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-neutral-400">
                        {snapshot.capability.id}
                      </div>
                      <div className="text-2xl font-semibold tracking-tight">
                        {snapshot.capability.name}
                      </div>
                      <p className="max-w-xl text-sm leading-6 text-neutral-600">
                        {snapshot.capability.description}
                      </p>
                    </div>
                    <div className="rounded-full border border-neutral-200 px-3 py-1 text-[11px] uppercase tracking-[0.18em] text-neutral-500 transition group-hover:border-emerald-200 group-hover:text-emerald-700">
                      Open
                    </div>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-3">
                    <Stat
                      label="Current"
                      value={score === null || score === undefined ? 'No evals' : score.toFixed(2)}
                    />
                    <Stat
                      label="Adapter Delta"
                      value={delta === null ? 'No adapter' : `${delta >= 0 ? '+' : ''}${delta.toFixed(3)}`}
                    />
                    <Stat
                      label="Trend"
                      value={delta === null ? 'steady' : delta > 0 ? 'up' : delta < 0 ? 'down' : 'steady'}
                    />
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <MiniPanel
                      title="Dimensions"
                      body={`${snapshot.capability.eval_dimensions.length} checks across ${
                        snapshot.scorecard?.sample_count ?? 0
                      } scored traces.`}
                    />
                    <MiniPanel
                      title="Training Surface"
                      body={`${snapshot.capability.eligible_methods.join(' / ').toUpperCase()} • ${
                        snapshot.capability.recipe_refs.length
                      } recipes`}
                    />
                  </div>
                </Link>
              );
            })}
          </section>
        )}
      </div>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[1.25rem] border border-neutral-200/80 bg-neutral-50 px-4 py-3">
      <div className="text-[11px] uppercase tracking-[0.2em] text-neutral-400">{label}</div>
      <div className="mt-2 text-lg font-semibold text-neutral-950">{value}</div>
    </div>
  );
}

function MiniPanel({ title, body }: { title: string; body: string }) {
  return (
    <div className="border-t border-neutral-200 pt-3">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-neutral-400">
        {title}
      </div>
      <div className="mt-2 text-sm leading-6 text-neutral-600">{body}</div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-[1.75rem] border border-dashed border-neutral-300 bg-white/80 p-10 text-center shadow-[0_18px_50px_-40px_rgba(15,23,42,0.45)]">
      <div className="text-sm font-semibold uppercase tracking-[0.22em] text-neutral-400">
        No capabilities tracked yet
      </div>
      <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-neutral-600">
        Start with a recommended capability template or compile one from plain language, then come
        back here to watch scorecards, clusters, datasets, and adapter state accumulate.
      </p>
      <Link
        href="/capabilities/new"
        className="mt-6 inline-flex rounded-full bg-neutral-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-neutral-800"
      >
        Create your first capability
      </Link>
    </div>
  );
}
