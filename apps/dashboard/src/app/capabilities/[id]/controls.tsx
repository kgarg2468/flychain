'use client';

import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';

import {
  gateway,
  type ClusterSummary,
  type DatasetEntry,
  type RecipeRow,
  type TrainingRunRow,
} from '@/lib/gateway';

interface Props {
  capabilityId: string;
  clusters: ClusterSummary[];
  datasets: DatasetEntry[];
  runs: TrainingRunRow[];
  recipes: RecipeRow[];
}

const defaultFailureJson = `[
  {
    "trace_id": "failure-1",
    "project_id": "demo",
    "input": "Question here",
    "output": "Bad answer here",
    "context": "Optional supporting context",
    "corrected_response": "Ideal answer here",
    "tags": { "task": "rag" }
  }
]`;

const defaultReplayJson = `[
  {
    "trace_id": "replay-1",
    "project_id": "demo",
    "input": "Question here",
    "context": "Optional context",
    "baseline_output": "Current answer",
    "candidate_output": "Candidate answer",
    "tags": { "task": "rag" }
  }
]`;

const defaultCandidateJson = `{
  "groundedness": 0.72
}`;

export function CapabilityControls({
  capabilityId,
  clusters,
  datasets,
  runs,
  recipes,
}: Props) {
  const router = useRouter();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const [clusterFailures, setClusterFailures] = useState(defaultFailureJson);
  const [clusterSize, setClusterSize] = useState('3');
  const [selectedCluster, setSelectedCluster] = useState(clusters[0]?.id ?? '');
  const [datasetMethod, setDatasetMethod] = useState('sft');
  const [datasetFailures, setDatasetFailures] = useState(defaultFailureJson);
  const [selectedDataset, setSelectedDataset] = useState(datasets[0]?.id ?? '');
  const [selectedRecipe, setSelectedRecipe] = useState(recipes[0]?.id ?? '');
  const [selectedRun, setSelectedRun] = useState(runs[0]?.id ?? '');
  const [candidateJson, setCandidateJson] = useState(
    runs[0]?.candidate && Object.keys(runs[0].candidate).length > 0
      ? JSON.stringify(runs[0].candidate, null, 2)
      : defaultCandidateJson.replace('groundedness', capabilityId),
  );
  const [replayJson, setReplayJson] = useState(defaultReplayJson);

  function parseJson<T>(raw: string): T {
    return JSON.parse(raw) as T;
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

  const activeCluster = clusters.find((cluster) => cluster.id === selectedCluster) ?? null;

  return (
    <section className="grid gap-6 rounded-[2rem] border border-neutral-200 bg-white p-6 shadow-[0_20px_70px_-50px_rgba(15,23,42,0.55)]">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-neutral-400">
            Operator Controls
          </div>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">
            Run clustering, synthesize data, compare candidates, and manage adapters.
          </h2>
        </div>
        <button
          type="button"
          onClick={() => router.refresh()}
          className="rounded-full border border-neutral-200 px-4 py-2 text-sm text-neutral-600 transition hover:border-neutral-400 hover:text-neutral-950"
        >
          Refresh
        </button>
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
          title="Cluster Failures"
          body="Paste a JSON array of failed traces and persist a fresh cluster run."
        >
          <label className="grid gap-2 text-sm">
            <span>Min cluster size</span>
            <input
              type="number"
              min={2}
              value={clusterSize}
              onChange={(e) => setClusterSize(e.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            />
          </label>
          <Textarea value={clusterFailures} onChange={setClusterFailures} />
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              runAction(async () => {
                const result = await gateway.clusterRun(capabilityId, {
                  failures: parseJson(clusterFailures),
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
          body="Pick a stored cluster and provide the source failures used to build the JSONL."
        >
          <label className="grid gap-2 text-sm">
            <span>Cluster</span>
            <select
              value={selectedCluster}
              onChange={(e) => setSelectedCluster(e.target.value)}
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
              onChange={(e) => setDatasetMethod(e.target.value)}
              className="rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 outline-none"
            >
              <option value="sft">SFT</option>
              <option value="dpo">DPO</option>
            </select>
          </label>
          <Textarea value={datasetFailures} onChange={setDatasetFailures} />
          <button
            type="button"
            disabled={pending || !activeCluster}
            onClick={() =>
              runAction(async () => {
                const dataset = await gateway.synthesizeDataset(capabilityId, {
                  cluster: activeCluster!,
                  failures: parseJson(datasetFailures),
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
          body="Select a dataset and recipe, then create a candidate adapter run."
        >
          <label className="grid gap-2 text-sm">
            <span>Dataset</span>
            <select
              value={selectedDataset}
              onChange={(e) => setSelectedDataset(e.target.value)}
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
              onChange={(e) => setSelectedRecipe(e.target.value)}
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
                return `Training run ${run.id} created with status ${run.status}.`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Creating...' : 'Create training run'}
          </button>
        </ControlCard>

        <ControlCard
          title="A/B Compare"
          body="Replay a held-out set of rows against baseline and candidate outputs."
        >
          <Textarea value={replayJson} onChange={setReplayJson} />
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              runAction(async () => {
                const result = await gateway.abCompare(capabilityId, {
                  replay: parseJson(replayJson),
                });
                return `A/B compare complete. Delta ${result.delta.toFixed(3)} across ${result.sample_count} rows.`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Comparing...' : 'Run A/B compare'}
          </button>
        </ControlCard>

        <ControlCard
          title="Apply Gate"
          body="Select a run and submit the candidate aggregate scores to promote or archive it."
        >
          <label className="grid gap-2 text-sm">
            <span>Training run</span>
            <select
              value={selectedRun}
              onChange={(e) => setSelectedRun(e.target.value)}
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
          <Textarea value={candidateJson} onChange={setCandidateJson} />
          <button
            type="button"
            disabled={pending || !selectedRun}
            onClick={() =>
              runAction(async () => {
                const result = await gateway.applyGate(selectedRun, {
                  candidate: parseJson(candidateJson),
                });
                return `Gate decision: ${String((result.verdict as { decision?: string }).decision ?? 'complete')}.`;
              })
            }
            className="rounded-full bg-neutral-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {pending ? 'Applying...' : 'Apply gate'}
          </button>
        </ControlCard>

        <ControlCard
          title="Adapter Pointer"
          body="Promote a run manually as the active adapter or clear the pointer."
        >
          <label className="grid gap-2 text-sm">
            <span>Run to activate</span>
            <select
              value={selectedRun}
              onChange={(e) => setSelectedRun(e.target.value)}
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

function Textarea(props: { value: string; onChange: (value: string) => void }) {
  return (
    <textarea
      value={props.value}
      onChange={(e) => props.onChange(e.target.value)}
      className="min-h-[180px] rounded-[1.25rem] border border-neutral-200 bg-white px-4 py-3 font-mono text-xs outline-none"
    />
  );
}
