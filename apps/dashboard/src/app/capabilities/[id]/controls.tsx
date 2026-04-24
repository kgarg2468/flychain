'use client';

import { useEffect, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';

import {
  gateway,
  type ClusterSummary,
  type DatasetEntry,
  type FailureRow,
  type RecipeRow,
  type ReplaySetRecord,
  type ReplaySetRow,
  type TrainingRunRow,
} from '@/lib/gateway';

interface Props {
  capabilityId: string;
  clusters: ClusterSummary[];
  datasets: DatasetEntry[];
  runs: TrainingRunRow[];
  recipes: RecipeRow[];
  failures: FailureRow[];
  replaySets: ReplaySetRecord[];
}

function blankReplayRow(index: number): ReplaySetRow {
  return {
    trace_id: `replay-${index + 1}`,
    project_id: 'demo',
    input: '',
    context: '',
    baseline_output: '',
    candidate_output: '',
    tags: { task: 'rag' },
  };
}

function rowsFromFailures(failures: FailureRow[]): ReplaySetRow[] {
  return failures.map((failure, index) => ({
    trace_id: failure.trace_id || `replay-${index + 1}`,
    project_id: failure.project_id,
    input: failure.input,
    context: failure.context,
    baseline_output: failure.output,
    candidate_output: failure.corrected_response ?? '',
    tags: failure.tags,
  }));
}

export function CapabilityControls({
  capabilityId,
  clusters,
  datasets,
  runs,
  recipes,
  failures,
  replaySets,
}: Props) {
  const router = useRouter();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const [selectedFailureIds, setSelectedFailureIds] = useState<string[]>([]);
  const [clusterSize, setClusterSize] = useState('3');
  const [selectedCluster, setSelectedCluster] = useState(clusters[0]?.id ?? '');
  const [datasetMethod, setDatasetMethod] = useState('sft');
  const [selectedDataset, setSelectedDataset] = useState(datasets[0]?.id ?? '');
  const [selectedRecipe, setSelectedRecipe] = useState(recipes[0]?.id ?? '');
  const [selectedRun, setSelectedRun] = useState(runs[0]?.id ?? '');
  const [selectedReplaySet, setSelectedReplaySet] = useState(replaySets[0]?.id ?? '');
  const [replaySetName, setReplaySetName] = useState(replaySets[0]?.name ?? 'Held-out replay');
  const [replayRows, setReplayRows] = useState<ReplaySetRow[]>(
    replaySets[0]?.rows.length ? replaySets[0].rows : [blankReplayRow(0)],
  );

  const hasActiveJobs = runs.some((run) =>
    ['queued', 'running', 'gate-queued', 'gate-running'].includes(run.status),
  );

  useEffect(() => {
    if (!hasActiveJobs) return;
    const interval = window.setInterval(() => {
      router.refresh();
    }, 3000);
    return () => window.clearInterval(interval);
  }, [hasActiveJobs, router]);

  useEffect(() => {
    if (!selectedCluster && clusters[0]) setSelectedCluster(clusters[0].id);
  }, [clusters, selectedCluster]);

  useEffect(() => {
    if (!selectedDataset && datasets[0]) setSelectedDataset(datasets[0].id);
  }, [datasets, selectedDataset]);

  useEffect(() => {
    if (!selectedRecipe && recipes[0]) setSelectedRecipe(recipes[0].id);
  }, [recipes, selectedRecipe]);

  useEffect(() => {
    if (!selectedRun && runs[0]) setSelectedRun(runs[0].id);
  }, [runs, selectedRun]);

  useEffect(() => {
    if (replaySets.length === 0) return;
    const selected = replaySets.find((item) => item.id === selectedReplaySet) ?? replaySets[0];
    setSelectedReplaySet(selected.id);
    setReplaySetName(selected.name);
    setReplayRows(selected.rows.length ? selected.rows : [blankReplayRow(0)]);
  }, [replaySets, selectedReplaySet]);

  function toggleFailure(traceId: string) {
    setSelectedFailureIds((current) =>
      current.includes(traceId)
        ? current.filter((item) => item !== traceId)
        : [...current, traceId],
    );
  }

  function updateReplayRow(
    index: number,
    key: keyof ReplaySetRow,
    value: string | Record<string, string> | undefined,
  ) {
    setReplayRows((current) =>
      current.map((row, rowIndex) =>
        rowIndex === index
          ? {
              ...row,
              [key]: value,
            }
          : row,
      ),
    );
  }

  function addReplayRow() {
    setReplayRows((current) => [...current, blankReplayRow(current.length)]);
  }

  function removeReplayRow(index: number) {
    setReplayRows((current) => current.filter((_, rowIndex) => rowIndex !== index));
  }

  function loadReplaySet(replaySetId: string) {
    setSelectedReplaySet(replaySetId);
    const selected = replaySets.find((item) => item.id === replaySetId);
    if (!selected) return;
    setReplaySetName(selected.name);
    setReplayRows(selected.rows.length ? selected.rows : [blankReplayRow(0)]);
  }

  function runAction(action: () => Promise<string>) {
    setError(null);
    setMessage(null);
    startTransition(async () => {
      try {
        const next = await action();
        setMessage(next);
        router.refresh();
      } catch (e) {
        setError((e as Error).message);
      }
    });
  }

  const selectedRunRow = runs.find((run) => run.id === selectedRun) ?? null;

  return (
    <section className="grid gap-6 rounded-[2rem] border border-neutral-200 bg-white p-6 shadow-[0_20px_70px_-50px_rgba(15,23,42,0.55)]">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-neutral-400">
            Operator Controls
          </div>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">
            Structured flywheel actions for failures, datasets, comparisons, and promotion.
          </h2>
        </div>
        <div className="flex items-center gap-3">
          {hasActiveJobs ? (
            <span className="rounded-full bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700">
              Polling active runs…
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => router.refresh()}
            className="rounded-full border border-neutral-200 px-4 py-2 text-sm text-neutral-600 transition hover:border-neutral-400 hover:text-neutral-950"
          >
            Refresh
          </button>
        </div>
      </div>

      {message ? (
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {message}
        </div>
      ) : null}
      {error ? (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-2">
        <ControlCard
          title="Failure Inventory"
          body="Select failing traces, tune cluster size, and persist a new cluster run without pasting JSON."
        >
          <label className="grid gap-2 text-sm">
            <span>Min cluster size</span>
            <input
              type="number"
              min={2}
              value={clusterSize}
              onChange={(event) => setClusterSize(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            />
          </label>
          <div className="grid max-h-[18rem] gap-3 overflow-auto rounded-[1.25rem] border border-neutral-200 bg-neutral-50 p-3">
            {failures.length === 0 ? (
              <div className="text-sm text-neutral-500">
                No persisted failures yet. Auto-eval some traces first, then come back here.
              </div>
            ) : (
              failures.map((failure) => (
                <label
                  key={failure.trace_id}
                  className="grid gap-2 rounded-[1rem] border border-neutral-200 bg-white p-3"
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      checked={selectedFailureIds.includes(failure.trace_id)}
                      onChange={() => toggleFailure(failure.trace_id)}
                      aria-label={`Select failure ${failure.trace_id}`}
                      className="mt-1 h-4 w-4"
                    />
                    <div className="grid gap-1">
                      <div className="font-mono text-xs text-neutral-500">{failure.trace_id}</div>
                      <div className="text-sm font-medium text-neutral-950">{failure.input}</div>
                      <div className="text-xs text-neutral-500">
                        score {failure.aggregate_score?.toFixed(2) ?? '-'} •{' '}
                        {failure.failing_dimensions.join(', ')}
                      </div>
                      {failure.corrected_response ? (
                        <div className="text-xs text-neutral-500">
                          corrected: {failure.corrected_response}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </label>
              ))
            )}
          </div>
          <button
            type="button"
            disabled={pending || selectedFailureIds.length === 0}
            onClick={() =>
              runAction(async () => {
                const result = await gateway.clusterRun(capabilityId, {
                  failure_ids: selectedFailureIds,
                  min_cluster_size: Number(clusterSize),
                  summarize: true,
                });
                return `Cluster run stored with ${result.clusters.length} cluster(s).`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Running...' : 'Run clustering'}
          </button>
        </ControlCard>

        <ControlCard
          title="Synthesize Dataset"
          body="Choose a stored cluster and create an SFT or DPO dataset directly from persisted failures."
        >
          <label className="grid gap-2 text-sm">
            <span>Cluster</span>
            <select
              value={selectedCluster}
              onChange={(event) => setSelectedCluster(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">Select cluster</option>
              {clusters.map((cluster) => (
                <option key={cluster.id} value={cluster.id}>
                  {cluster.label}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm">
            <span>Method</span>
            <select
              value={datasetMethod}
              onChange={(event) => setDatasetMethod(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="sft">SFT</option>
              <option value="dpo">DPO</option>
            </select>
          </label>
          <button
            type="button"
            disabled={pending || !selectedCluster}
            onClick={() =>
              runAction(async () => {
                const dataset = await gateway.synthesizeDataset(capabilityId, {
                  cluster_id: selectedCluster,
                  method: datasetMethod,
                  generate_missing: false,
                });
                return `Dataset ${dataset.id} created with ${dataset.row_count} rows.`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Synthesizing...' : 'Create dataset'}
          </button>
        </ControlCard>

        <ControlCard
          title="Queue Training Run"
          body="Pick a dataset and recipe, then queue a background training run."
        >
          <label className="grid gap-2 text-sm">
            <span>Dataset</span>
            <select
              value={selectedDataset}
              onChange={(event) => setSelectedDataset(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">Select dataset</option>
              {datasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  {dataset.id} ({dataset.method})
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm">
            <span>Recipe</span>
            <select
              value={selectedRecipe}
              onChange={(event) => setSelectedRecipe(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">Select recipe</option>
              {recipes.map((recipe) => (
                <option key={recipe.id} value={recipe.id}>
                  {recipe.id} ({recipe.backend})
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={pending || !selectedDataset || !selectedRecipe}
            onClick={() =>
              runAction(async () => {
                const run = await gateway.createTrainingRun({
                  capability_id: capabilityId,
                  dataset_id: selectedDataset,
                  recipe_id: selectedRecipe,
                  allow_backend_fallback: true,
                });
                return `Training run ${run.id} queued with status ${run.status}.`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Creating...' : 'Queue training run'}
          </button>
        </ControlCard>

        <ControlCard
          title="Replay Set Builder"
          body="Edit held-out rows in structured fields, save them, and reuse them for A/B comparison."
        >
          <label className="grid gap-2 text-sm">
            <span>Replay set</span>
            <select
              aria-label="Replay set"
              value={selectedReplaySet}
              onChange={(event) => loadReplaySet(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">New replay set</option>
              {replaySets.map((replaySet) => (
                <option key={replaySet.id} value={replaySet.id}>
                  {replaySet.name}
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm">
            <span>Replay set name</span>
            <input
              aria-label="Replay set name"
              value={replaySetName}
              onChange={(event) => setReplaySetName(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={addReplayRow}
              className="rounded-full border border-neutral-200 px-4 py-3 text-sm font-medium text-neutral-700 transition hover:border-neutral-400 hover:text-neutral-950"
            >
              Add row
            </button>
            <button
              type="button"
              onClick={() => {
                const selectedFailures = failures.filter((failure) =>
                  selectedFailureIds.includes(failure.trace_id),
                );
                if (selectedFailures.length === 0) return;
                setReplayRows(rowsFromFailures(selectedFailures));
              }}
              className="rounded-full border border-neutral-200 px-4 py-3 text-sm font-medium text-neutral-700 transition hover:border-neutral-400 hover:text-neutral-950"
            >
              Seed from selected failures
            </button>
          </div>
          <div className="grid max-h-[28rem] gap-4 overflow-auto rounded-[1.25rem] border border-neutral-200 bg-neutral-50 p-3">
            {replayRows.map((row, index) => (
              <div
                key={`${row.trace_id}-${index}`}
                className="grid gap-3 rounded-[1rem] border border-neutral-200 bg-white p-4"
              >
                <div className="flex items-center justify-between">
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-400">
                    Row {index + 1}
                  </div>
                  {replayRows.length > 1 ? (
                    <button
                      type="button"
                      onClick={() => removeReplayRow(index)}
                      className="text-xs text-neutral-500 transition hover:text-neutral-900"
                    >
                      Remove
                    </button>
                  ) : null}
                </div>
                <Field label={`Trace ID ${index + 1}`}>
                  <input
                    aria-label={`Trace ID ${index + 1}`}
                    value={row.trace_id}
                    onChange={(event) => updateReplayRow(index, 'trace_id', event.target.value)}
                    className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
                  />
                </Field>
                <Field label={`Input ${index + 1}`}>
                  <input
                    aria-label={`Input ${index + 1}`}
                    value={row.input}
                    onChange={(event) => updateReplayRow(index, 'input', event.target.value)}
                    className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
                  />
                </Field>
                <Field label={`Context ${index + 1}`}>
                  <input
                    aria-label={`Context ${index + 1}`}
                    value={row.context ?? ''}
                    onChange={(event) => updateReplayRow(index, 'context', event.target.value)}
                    className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
                  />
                </Field>
                <Field label={`Baseline output ${index + 1}`}>
                  <input
                    aria-label={`Baseline output ${index + 1}`}
                    value={row.baseline_output}
                    onChange={(event) =>
                      updateReplayRow(index, 'baseline_output', event.target.value)
                    }
                    className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
                  />
                </Field>
                <Field label={`Candidate output ${index + 1}`}>
                  <input
                    aria-label={`Candidate output ${index + 1}`}
                    value={row.candidate_output}
                    onChange={(event) =>
                      updateReplayRow(index, 'candidate_output', event.target.value)
                    }
                    className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
                  />
                </Field>
              </div>
            ))}
          </div>
          <button
            type="button"
            disabled={pending || replayRows.length === 0 || !replaySetName.trim()}
            onClick={() =>
              runAction(async () => {
                if (selectedReplaySet) {
                  await gateway.updateReplaySet(capabilityId, selectedReplaySet, {
                    name: replaySetName.trim(),
                    rows: replayRows,
                  });
                  return `Replay set ${selectedReplaySet} updated.`;
                }
                const replaySet = await gateway.createReplaySet(capabilityId, {
                  name: replaySetName.trim(),
                  rows: replayRows,
                });
                setSelectedReplaySet(replaySet.id);
                return `Replay set ${replaySet.id} created.`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Saving...' : 'Save replay set'}
          </button>
        </ControlCard>

        <ControlCard
          title="Compare and Gate"
          body="Run A/B comparison from a stored replay set, then queue gate application from the latest result."
        >
          <label className="grid gap-2 text-sm">
            <span>Training run</span>
            <select
              value={selectedRun}
              onChange={(event) => setSelectedRun(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">Select run</option>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.id} ({run.status})
                </option>
              ))}
            </select>
          </label>
          <label className="grid gap-2 text-sm">
            <span>Replay set for compare</span>
            <select
              aria-label="Replay set for compare"
              value={selectedReplaySet}
              onChange={(event) => setSelectedReplaySet(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">Select replay set</option>
              {replaySets.map((replaySet) => (
                <option key={replaySet.id} value={replaySet.id}>
                  {replaySet.name}
                </option>
              ))}
            </select>
          </label>
          {selectedRunRow?.latest_comparison ? (
            <div className="rounded-[1rem] border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600">
              Latest comparison delta:{' '}
              <span className="font-medium text-neutral-950">
                {selectedRunRow.latest_comparison.delta >= 0 ? '+' : ''}
                {selectedRunRow.latest_comparison.delta.toFixed(3)}
              </span>
            </div>
          ) : (
            <div className="rounded-[1rem] border border-dashed border-neutral-200 px-4 py-3 text-sm text-neutral-500">
              No comparison stored on this run yet.
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={pending || !selectedRun || !selectedReplaySet}
              onClick={() =>
                runAction(async () => {
                  const result = await gateway.abCompare(capabilityId, {
                    run_id: selectedRun,
                    replay_set_id: selectedReplaySet,
                  });
                  return `A/B compare complete. Delta ${result.delta.toFixed(3)} across ${result.sample_count} rows.`;
                })
              }
              className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
            >
              {pending ? 'Comparing...' : 'Run A/B compare'}
            </button>
            <button
              type="button"
              disabled={pending || !selectedRun}
              onClick={() =>
                runAction(async () => {
                  const run = await gateway.applyGate(selectedRun, {});
                  return `Gate queued for ${run.id} with status ${run.status}.`;
                })
              }
              className="rounded-full border border-neutral-200 px-4 py-3 text-sm font-medium text-neutral-700 transition hover:border-neutral-400 hover:text-neutral-950 disabled:opacity-60"
            >
              {pending ? 'Queueing...' : 'Apply gate'}
            </button>
          </div>
        </ControlCard>

        <ControlCard
          title="Adapter Pointer"
          body="Activate a run manually or clear the active adapter pointer."
        >
          <label className="grid gap-2 text-sm">
            <span>Run to activate</span>
            <select
              value={selectedRun}
              onChange={(event) => setSelectedRun(event.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="">Select run</option>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.id} ({run.status})
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={pending || !selectedRun}
              onClick={() =>
                runAction(async () => {
                  const result = await gateway.activateAdapter(capabilityId, selectedRun);
                  return `Activated ${result.active_run_id}.`;
                })
              }
              className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
            >
              {pending ? 'Activating...' : 'Activate adapter'}
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() =>
                runAction(async () => {
                  await gateway.deactivateAdapter(capabilityId);
                  return 'Adapter pointer cleared.';
                })
              }
              className="rounded-full border border-neutral-200 px-4 py-3 text-sm font-medium text-neutral-700 transition hover:border-neutral-400 hover:text-neutral-950 disabled:opacity-60"
            >
              Clear pointer
            </button>
          </div>
        </ControlCard>
      </div>
    </section>
  );
}

function ControlCard({
  title,
  body,
  children,
}: {
  title: string;
  body: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-4 rounded-[1.5rem] border border-neutral-200 bg-neutral-50 p-5">
      <div>
        <div className="text-sm font-semibold text-neutral-950">{title}</div>
        <div className="mt-1 text-sm leading-6 text-neutral-500">{body}</div>
      </div>
      {children}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-2 text-sm">
      <span>{label}</span>
      {children}
    </label>
  );
}
