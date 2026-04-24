import Link from 'next/link';
import { notFound } from 'next/navigation';

import { CapabilityControls } from './controls';
import {
  gateway,
  type ActiveAdapter,
  type ClustersResponse,
  type DatasetEntry,
  type FailureRow,
  type RecipeRow,
  type ReplaySetRecord,
  type Scorecard,
  type TrainingRunRow,
} from '@/lib/gateway';

interface Params {
  params: { id: string };
}

export default async function CapabilityPage({ params }: Params) {
  const id = decodeURIComponent(params.id);
  let spec;
  try {
    spec = await gateway.getCapability(id);
  } catch {
    notFound();
  }

  let scorecard: Scorecard | null = null;
  try {
    scorecard = await gateway.scorecard(id);
  } catch {
    scorecard = null;
  }

  let clusters: ClustersResponse | null = null;
  try {
    clusters = await gateway.clusters(id);
  } catch {
    clusters = null;
  }

  let datasets: DatasetEntry[] = [];
  try {
    datasets = (await gateway.datasets(id)).datasets;
  } catch {
    datasets = [];
  }

  let runs: TrainingRunRow[] = [];
  try {
    runs = (await gateway.trainingRuns(id)).runs;
  } catch {
    runs = [];
  }

  let failures: FailureRow[] = [];
  try {
    failures = (await gateway.failures(id)).failures;
  } catch {
    failures = [];
  }

  let replaySets: ReplaySetRecord[] = [];
  try {
    replaySets = (await gateway.replaySets(id)).replay_sets;
  } catch {
    replaySets = [];
  }

  let activeAdapter: ActiveAdapter | null = null;
  try {
    activeAdapter = await gateway.activeAdapter(id);
  } catch {
    activeAdapter = null;
  }

  let recipes: RecipeRow[] = [];
  try {
    recipes = (await gateway.recipes()).recipes;
  } catch {
    recipes = [];
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-8 px-6 py-12">
      <div className="flex items-center justify-between gap-4">
        <Link href="/" className="text-sm text-neutral-500 hover:underline">
          ← Back to workspace
        </Link>
        <div className="flex gap-3 text-sm text-neutral-500">
          <Link href="/traces" className="hover:text-neutral-900 hover:underline">
            Trace explorer
          </Link>
          <Link href="/settings" className="hover:text-neutral-900 hover:underline">
            Settings
          </Link>
        </div>
      </div>

      <header className="flex flex-col gap-2">
        <span className="font-mono text-xs text-neutral-500">{spec.id}</span>
        <h1 className="text-2xl font-semibold tracking-tight">{spec.name}</h1>
        <p className="text-neutral-600 dark:text-neutral-400">{spec.description}</p>
      </header>

      <Section title="Eval dimensions">
        <ul className="space-y-2">
          {spec.eval_dimensions.map((d) => (
            <li
              key={d.id}
              className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800"
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs">{d.id}</span>
                <span className="text-xs text-neutral-500">weight {d.weight}</span>
              </div>
              <div className="mt-1 text-neutral-700 dark:text-neutral-300">{d.description}</div>
              {d.judge_prompt_ref && (
                <div className="mt-1 font-mono text-xs text-neutral-500">
                  judge: {d.judge_prompt_ref}
                </div>
              )}
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Slice rules">
        {spec.slice_rules.length === 0 ? (
          <p className="text-sm text-neutral-500">
            No slice rules - this capability applies to every trace in the project.
          </p>
        ) : (
          <ul className="space-y-2">
            {spec.slice_rules.map((r, i) => (
              <li
                key={i}
                className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800"
              >
                <span className="font-mono text-xs text-neutral-500">{r.type}</span>
                <span className="ml-2">{r.value}</span>
                {r.negate && <span className="ml-2 text-xs text-red-500">negated</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <Section title="Eligible training methods">
          <div className="flex gap-2">
            {spec.eligible_methods.map((m) => (
              <span
                key={m}
                className="rounded bg-neutral-900 px-2 py-1 text-xs text-white dark:bg-white dark:text-neutral-900"
              >
                {m.toUpperCase()}
              </span>
            ))}
          </div>
        </Section>
        <Section title="Promotion gate">
          <p className="text-sm">
            +{(spec.promotion_gate.threshold * 100).toFixed(1)}% vs baseline required
            <span className="block text-xs text-neutral-500">
              max other regression: {(spec.promotion_gate.max_other_regression * 100).toFixed(1)}%
            </span>
          </p>
        </Section>
      </div>

      <Section title="Recipes">
        {spec.recipe_refs.length === 0 ? (
          <p className="text-sm text-neutral-500">No recipe refs configured yet.</p>
        ) : (
          <ul className="space-y-1 font-mono text-xs">
            {spec.recipe_refs.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Scorecard">
        {scorecard === null ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            Scorecard unavailable - gateway not reachable.
          </div>
        ) : scorecard.sample_count === 0 ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            No eval runs yet. Call{' '}
            <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
              POST /v1/eval
            </code>{' '}
            (or let the orchestrator run it on new traces) to populate this.
          </div>
        ) : (
          <div className="rounded-md border border-neutral-200 p-4 dark:border-neutral-800">
            <div className="flex items-baseline gap-3">
              <div className="text-3xl font-semibold">
                {(scorecard.aggregate_score ?? 0).toFixed(2)}
              </div>
              <div className="text-xs text-neutral-500">
                aggregate ({scorecard.sample_count} samples)
              </div>
            </div>
            <ul className="mt-3 space-y-1 text-sm">
              {scorecard.dimensions.map((d) => (
                <li
                  key={d.dimension}
                  className="flex items-center justify-between font-mono text-xs"
                >
                  <span>{d.dimension}</span>
                  <span>
                    {d.mean_score.toFixed(2)}{' '}
                    <span className="text-neutral-500">
                      (n={d.sample_count}, w={d.weight})
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section title="Triage - failure clusters">
        {clusters === null ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            Gateway not reachable.
          </div>
        ) : clusters.clusters.length === 0 ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            No clusters yet. Trigger{' '}
            <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
              POST /v1/capabilities/{id}/cluster-run
            </code>{' '}
            to produce them.
          </div>
        ) : (
          <ul className="space-y-2">
            {clusters.clusters.map((c) => (
              <li
                key={c.id}
                className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800"
              >
                <div className="flex items-center justify-between">
                  <div className="font-mono text-xs text-neutral-500">{c.id}</div>
                  <div className="text-xs">{c.size} traces</div>
                </div>
                <div className="mt-1 font-medium">{c.label}</div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Synthesized datasets">
        {datasets.length === 0 ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            No datasets synthesized yet. Trigger{' '}
            <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
              POST /v1/capabilities/{id}/synthesize-dataset
            </code>{' '}
            against a cluster to produce one.
          </div>
        ) : (
          <ul className="space-y-2">
            {datasets.map((d) => (
              <li
                key={d.id}
                className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800"
              >
                <div className="flex items-center justify-between">
                  <div className="font-mono text-xs text-neutral-500">{d.id}</div>
                  <div className="text-xs uppercase">{d.method}</div>
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  cluster {d.cluster_id ?? '-'} / {d.row_count} rows
                </div>
                <div className="mt-1 font-mono text-xs text-neutral-500">{d.path}</div>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Active adapter">
        {activeAdapter === null ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            Gateway not reachable.
          </div>
        ) : activeAdapter.active === null ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            No adapter promoted yet.
          </div>
        ) : (
          <div className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800">
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs">{activeAdapter.active.active_run_id}</span>
              <span className="text-xs text-neutral-500">
                +
                {(
                  (activeAdapter.active.candidate[id] ?? 0) -
                  (activeAdapter.active.baseline[id] ?? 0)
                ).toFixed(3)}{' '}
                vs baseline
              </span>
            </div>
            <div className="mt-1 font-mono text-xs text-neutral-500">
              {activeAdapter.active.adapter_dir}
            </div>
          </div>
        )}
      </Section>

      <Section title="Training runs">
        {runs.length === 0 ? (
          <div className="rounded-md border border-dashed border-neutral-300 p-4 text-sm text-neutral-500 dark:border-neutral-700">
            No training runs yet. Trigger{' '}
            <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
              POST /v1/training-runs
            </code>{' '}
            with a recipe + dataset to produce one.
          </div>
        ) : (
          <ul className="space-y-2">
            {runs.map((r) => (
              <li
                key={r.id}
                className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800"
              >
                <div className="flex items-center justify-between">
                  <div className="font-mono text-xs">{r.id}</div>
                  <span
                    className={
                      'rounded px-2 py-0.5 text-xs ' +
                      (r.status === 'promoted'
                        ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
                        : r.status === 'archived' || r.status === 'failed'
                          ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
                          : 'bg-neutral-100 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200')
                    }
                  >
                    {r.status}
                  </span>
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  recipe: {r.recipe_id} / dataset: {r.dataset_id}
                </div>
                {r.latest_comparison ? (
                  <div className="mt-1 text-xs text-neutral-500">
                    latest compare: {r.latest_comparison.delta >= 0 ? '+' : ''}
                    {r.latest_comparison.delta.toFixed(3)}
                    {r.latest_comparison.replay_set_id
                      ? ` via ${r.latest_comparison.replay_set_id}`
                      : ''}
                  </div>
                ) : null}
                {r.artifact ? (
                  <div className="mt-1 text-xs text-neutral-500">
                    backend: {String((r.artifact as { backend?: string }).backend ?? 'unknown')}
                    {Boolean((r.artifact as { dry_run?: boolean }).dry_run) ? ' (dry-run)' : ''}
                  </div>
                ) : null}
                {r.gate_verdict ? (
                  <div className="mt-1 text-xs text-neutral-500">
                    gate: {String((r.gate_verdict as { reason?: string }).reason ?? '')}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <CapabilityControls
        capabilityId={id}
        clusters={clusters?.clusters ?? []}
        datasets={datasets}
        runs={runs}
        recipes={recipes}
        failures={failures}
        replaySets={replaySets}
      />
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-neutral-500">{title}</h2>
      {children}
    </section>
  );
}
