'use client';

import Link from 'next/link';

import { ChatClient } from './chat/client';
import { CapabilityControls } from './capabilities/[id]/controls';
import { SettingsClient } from './settings/client';
import type {
  ActiveAdapter,
  CapabilitySpec,
  ClustersResponse,
  DatasetEntry,
  FailureRow,
  RecipeRow,
  ReplaySetRecord,
  Scorecard,
  SettingsPayload,
  TraceListResponse,
  TrainingRunRow,
} from '@/lib/gateway';

type WorkspaceTab = 'capabilities' | 'chat' | 'traces' | 'settings';

export interface CapabilitySnapshot {
  capability: CapabilitySpec;
  scorecard: Scorecard | null;
  activeAdapter: ActiveAdapter | null;
}

export interface SelectedCapabilityDetail {
  spec: CapabilitySpec;
  scorecard: Scorecard | null;
  clusters: ClustersResponse | null;
  datasets: DatasetEntry[];
  runs: TrainingRunRow[];
  failures: FailureRow[];
  replaySets: ReplaySetRecord[];
  activeAdapter: ActiveAdapter | null;
  recipes: RecipeRow[];
}

interface WorkspaceClientProps {
  activeTab: WorkspaceTab;
  snapshots: CapabilitySnapshot[];
  selectedCapability: SelectedCapabilityDetail | null;
  capabilities: CapabilitySpec[];
  traces: TraceListResponse;
  settings: SettingsPayload;
  loadError: string | null;
  filters: {
    project_id?: string;
    capability_id?: string;
    status?: string;
    provider?: string;
  };
}

const tabs: Array<{ id: WorkspaceTab; label: string }> = [
  { id: 'capabilities', label: 'Capabilities' },
  { id: 'chat', label: 'Chat' },
  { id: 'traces', label: 'Traces' },
  { id: 'settings', label: 'Settings' },
];

export function WorkspaceClient({
  activeTab,
  snapshots,
  selectedCapability,
  capabilities,
  traces,
  settings,
  loadError,
  filters,
}: WorkspaceClientProps) {
  return (
    <main className="min-h-screen bg-[#f6f7f8] text-neutral-950">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-5 px-5 py-5 lg:px-8">
        <header className="border-b border-neutral-200 pb-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">
                FlyChain
              </div>
              <h1 className="mt-1 text-2xl font-semibold tracking-tight">Operator workspace</h1>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-neutral-600">
                Capabilities, chat traffic, traces, and runtime controls in one local surface.
              </p>
            </div>
            <RuntimeStrip settings={settings} traceTotal={traces.total} />
          </div>

          <nav className="mt-5 flex flex-wrap gap-2" aria-label="Workspace tabs">
            {tabs.map((tab) => (
              <Link
                key={tab.id}
                href={`/?tab=${tab.id}`}
                className={`rounded-full px-4 py-2 text-sm font-medium transition ${
                  activeTab === tab.id
                    ? 'bg-neutral-950 text-white'
                    : 'border border-neutral-200 bg-white text-neutral-600 hover:border-neutral-300 hover:text-neutral-950'
                }`}
              >
                {tab.label}
              </Link>
            ))}
            <Link
              href="/capabilities/new"
              className="ml-auto rounded-full bg-emerald-500 px-4 py-2 text-sm font-medium text-neutral-950 transition hover:bg-emerald-400"
            >
              New capability
            </Link>
          </nav>
        </header>

        {loadError ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <span className="font-medium">Gateway not reachable.</span> {loadError}
          </div>
        ) : null}

        <section className="flex-1">
          {activeTab === 'capabilities' ? (
            <CapabilitiesPanel snapshots={snapshots} selected={selectedCapability} />
          ) : null}
          {activeTab === 'chat' ? (
            <ChatClient capabilities={capabilities} loadError={loadError} embedded />
          ) : null}
          {activeTab === 'traces' ? (
            <TracesPanel traces={traces} capabilities={capabilities} filters={filters} />
          ) : null}
          {activeTab === 'settings' ? <SettingsClient initial={settings} /> : null}
        </section>
      </div>
    </main>
  );
}

function RuntimeStrip({ settings, traceTotal }: { settings: SettingsPayload; traceTotal: number }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
      <Metric label="Env" value={settings.runtime?.env ?? 'local'} />
      <Metric label="Traces" value={String(traceTotal)} />
      <Metric label="Auto eval" value={settings.settings.auto_eval_new_traces ? 'on' : 'off'} />
      <Metric label="Auto cluster" value={settings.settings.auto_cluster_failures ? 'on' : 'off'} />
    </dl>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
        {label}
      </dt>
      <dd className="mt-1 font-medium text-neutral-800">{value}</dd>
    </div>
  );
}

function CapabilitiesPanel({
  snapshots,
  selected,
}: {
  snapshots: CapabilitySnapshot[];
  selected: SelectedCapabilityDetail | null;
}) {
  if (snapshots.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[360px_1fr]">
      <div className="flex flex-col gap-3">
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">
          Capabilities
        </div>
        {snapshots.map((snapshot) => (
          <CapabilityRow key={snapshot.capability.id} snapshot={snapshot} selected={selected} />
        ))}
      </div>
      <CapabilityDetail selected={selected} />
    </div>
  );
}

function CapabilityRow({
  snapshot,
  selected,
}: {
  snapshot: CapabilitySnapshot;
  selected: SelectedCapabilityDetail | null;
}) {
  const score = snapshot.scorecard?.aggregate_score;
  const active = snapshot.activeAdapter?.active;
  const current = active?.candidate[snapshot.capability.id];
  const baseline = active?.baseline[snapshot.capability.id];
  const delta =
    typeof current === 'number' && typeof baseline === 'number' ? current - baseline : null;
  const isSelected = selected?.spec.id === snapshot.capability.id;

  return (
    <Link
      href={`/?tab=capabilities&capability=${encodeURIComponent(snapshot.capability.id)}`}
      className={`rounded-xl border bg-white p-4 transition hover:border-neutral-300 ${
        isSelected ? 'border-neutral-950' : 'border-neutral-200'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-[11px] uppercase tracking-[0.14em] text-neutral-400">
            {snapshot.capability.id}
          </div>
          <div className="mt-1 font-medium">{snapshot.capability.name}</div>
        </div>
        <div className="text-right text-sm font-semibold">
          {score === null || score === undefined ? '-' : score.toFixed(2)}
        </div>
      </div>
      <p className="mt-2 line-clamp-2 text-sm leading-5 text-neutral-600">
        {snapshot.capability.description}
      </p>
      <div className="mt-3 flex items-center justify-between text-xs text-neutral-500">
        <span>{snapshot.capability.eval_dimensions.length} checks</span>
        <span>{delta === null ? 'No adapter' : `${delta >= 0 ? '+' : ''}${delta.toFixed(3)}`}</span>
      </div>
    </Link>
  );
}

function CapabilityDetail({ selected }: { selected: SelectedCapabilityDetail | null }) {
  if (!selected) {
    return (
      <div className="flex min-h-[28rem] items-center justify-center rounded-xl border border-dashed border-neutral-300 bg-white text-sm text-neutral-500">
        Select a capability to inspect scorecards, failures, datasets, and promotion controls.
      </div>
    );
  }

  const score = selected.scorecard?.aggregate_score;
  const active = selected.activeAdapter?.active;
  const delta =
    active &&
    typeof active.candidate[selected.spec.id] === 'number' &&
    typeof active.baseline[selected.spec.id] === 'number'
      ? active.candidate[selected.spec.id] - active.baseline[selected.spec.id]
      : null;

  return (
    <div className="flex flex-col gap-5">
      <section className="rounded-xl border border-neutral-200 bg-white p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="font-mono text-xs text-neutral-500">{selected.spec.id}</div>
            <h2 className="mt-1 text-xl font-semibold tracking-tight">{selected.spec.name}</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-neutral-600">
              {selected.spec.description}
            </p>
          </div>
          <div className="grid grid-cols-3 gap-4 text-sm">
            <Metric
              label="Score"
              value={score === null || score === undefined ? '-' : score.toFixed(2)}
            />
            <Metric label="Failures" value={String(selected.failures.length)} />
            <Metric
              label="Delta"
              value={delta === null ? '-' : `${delta >= 0 ? '+' : ''}${delta.toFixed(3)}`}
            />
          </div>
        </div>
      </section>

      {active ? (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-5">
          <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700">
            Active adapter
          </h3>
          <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <Metric label="Run" value={active.active_run_id} />
            <Metric label="Candidate" value={String(active.candidate[selected.spec.id] ?? '-')} />
            <Metric label="Baseline" value={String(active.baseline[selected.spec.id] ?? '-')} />
            <Metric label="Adapter Dir" value={active.adapter_dir} />
          </div>
        </section>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <SummarySection title="Eval dimensions">
          {selected.spec.eval_dimensions.map((dimension) => (
            <div key={dimension.id} className="border-b border-neutral-100 py-3 last:border-0">
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-xs text-neutral-600">{dimension.id}</span>
                <span className="text-xs text-neutral-400">weight {dimension.weight}</span>
              </div>
              <p className="mt-1 text-sm leading-5 text-neutral-600">{dimension.description}</p>
            </div>
          ))}
        </SummarySection>
        <SummarySection title="Failure clusters">
          {selected.clusters === null || selected.clusters.clusters.length === 0 ? (
            <EmptyInline>No clusters stored yet.</EmptyInline>
          ) : (
            selected.clusters.clusters.map((cluster) => (
              <div key={cluster.id} className="border-b border-neutral-100 py-3 last:border-0">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium">{cluster.label}</span>
                  <span className="text-xs text-neutral-500">{cluster.size} traces</span>
                </div>
                <div className="mt-1 font-mono text-xs text-neutral-400">{cluster.id}</div>
              </div>
            ))
          )}
        </SummarySection>
        <SummarySection title="Datasets">
          {selected.datasets.length === 0 ? (
            <EmptyInline>No datasets synthesized yet.</EmptyInline>
          ) : (
            selected.datasets.map((dataset) => (
              <div key={dataset.id} className="border-b border-neutral-100 py-3 last:border-0">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-xs">{dataset.id}</span>
                  <span className="text-xs uppercase text-neutral-500">{dataset.method}</span>
                </div>
                <div className="mt-1 text-xs text-neutral-500">{dataset.row_count} rows</div>
              </div>
            ))
          )}
        </SummarySection>
        <SummarySection title="Training runs">
          {selected.runs.length === 0 ? (
            <EmptyInline>No training runs yet.</EmptyInline>
          ) : (
            selected.runs.map((run) => (
              <div key={run.id} className="border-b border-neutral-100 py-3 last:border-0">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-xs">{run.id}</span>
                  <span className="text-xs text-neutral-500">{run.status}</span>
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  {run.recipe_id} / {run.dataset_id}
                </div>
              </div>
            ))
          )}
        </SummarySection>
      </div>

      <CapabilityControls
        capabilityId={selected.spec.id}
        clusters={selected.clusters?.clusters ?? []}
        datasets={selected.datasets}
        runs={selected.runs}
        recipes={selected.recipes}
        failures={selected.failures}
        replaySets={selected.replaySets}
      />
    </div>
  );
}

function SummarySection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-500">
        {title}
      </h3>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function EmptyInline({ children }: { children: React.ReactNode }) {
  return <div className="py-3 text-sm text-neutral-500">{children}</div>;
}

function TracesPanel({
  traces,
  capabilities,
  filters,
}: {
  traces: TraceListResponse;
  capabilities: CapabilitySpec[];
  filters: WorkspaceClientProps['filters'];
}) {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-400">
          Traces
        </div>
        <h2 className="mt-1 text-xl font-semibold tracking-tight">Trace explorer</h2>
      </div>
      <form className="grid gap-3 rounded-xl border border-neutral-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-5">
        <input type="hidden" name="tab" value="traces" />
        <Input name="project_id" placeholder="Project id" defaultValue={filters.project_id} />
        <select
          name="capability_id"
          defaultValue={filters.capability_id ?? ''}
          className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500"
        >
          <option value="">All capabilities</option>
          {capabilities.map((capability) => (
            <option key={capability.id} value={capability.id}>
              {capability.name}
            </option>
          ))}
        </select>
        <Input name="provider" placeholder="Provider" defaultValue={filters.provider} />
        <select
          name="status"
          defaultValue={filters.status ?? ''}
          className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500"
        >
          <option value="">Any status</option>
          <option value="ok">ok</option>
          <option value="error">error</option>
        </select>
        <button
          type="submit"
          className="rounded-lg bg-neutral-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-800"
        >
          Apply filters
        </button>
      </form>

      <section className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <div className="text-sm text-neutral-600">{traces.total} matching traces</div>
          <div className="text-xs uppercase tracking-[0.16em] text-neutral-400">
            Gateway history
          </div>
        </div>
        {traces.traces.length === 0 ? (
          <div className="p-8 text-sm text-neutral-500">No traces matched the current filters.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-neutral-200 text-left text-sm">
              <thead className="bg-neutral-50 text-xs uppercase tracking-[0.14em] text-neutral-500">
                <tr>
                  <th className="px-4 py-3">Trace</th>
                  <th className="px-4 py-3">Provider</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Tokens</th>
                  <th className="px-4 py-3">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100">
                {traces.traces.map((trace) => (
                  <tr key={trace.trace_id} className="align-top">
                    <td className="px-4 py-4">
                      <div className="font-mono text-xs text-neutral-700">{trace.trace_id}</div>
                      <div className="mt-1 text-xs text-neutral-500">{trace.project_id}</div>
                      <div className="mt-2 max-w-xl text-neutral-600">
                        {promptPreview(trace.request, trace.method)}
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <div>{trace.provider}</div>
                      <div className="mt-1 text-xs text-neutral-500">{trace.model}</div>
                    </td>
                    <td className="px-4 py-4">
                      <span
                        className={`rounded-full px-2 py-1 text-xs font-medium ${
                          trace.status === 'ok'
                            ? 'bg-emerald-50 text-emerald-700'
                            : 'bg-red-50 text-red-700'
                        }`}
                      >
                        {trace.status}
                      </span>
                      {trace.error ? (
                        <div className="mt-2 max-w-xs text-xs text-red-600">{trace.error}</div>
                      ) : null}
                    </td>
                    <td className="px-4 py-4 text-neutral-600">{trace.total_tokens}</td>
                    <td className="px-4 py-4 text-neutral-500">
                      {new Date(trace.ts).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Input(props: { name: string; placeholder: string; defaultValue?: string }) {
  return (
    <input
      name={props.name}
      placeholder={props.placeholder}
      defaultValue={props.defaultValue}
      className="rounded-lg border border-neutral-200 bg-white px-3 py-2 text-sm outline-none placeholder:text-neutral-400 focus:border-neutral-500"
    />
  );
}

function promptPreview(request: Record<string, unknown>, fallback: string): string {
  const messages = request.messages;
  if (Array.isArray(messages) && messages.length > 0) {
    const first = messages[0];
    if (first && typeof first === 'object' && 'content' in first) {
      return String(first.content).slice(0, 120);
    }
  }
  return fallback;
}

function EmptyState() {
  return (
    <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-xl border border-dashed border-neutral-300 bg-white p-8 text-center">
      <div className="text-sm font-semibold uppercase tracking-[0.18em] text-neutral-400">
        No capabilities tracked yet
      </div>
      <p className="mt-3 max-w-xl text-sm leading-6 text-neutral-600">
        Create a capability from a template or compile one from plain language to start tracking
        scorecards, clusters, datasets, and adapter state.
      </p>
      <Link
        href="/capabilities/new"
        className="mt-5 rounded-full bg-neutral-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-800"
      >
        Create capability
      </Link>
    </div>
  );
}
