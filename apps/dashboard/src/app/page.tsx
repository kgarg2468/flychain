import {
  WorkspaceClient,
  type CapabilitySnapshot,
  type SelectedCapabilityDetail,
} from './workspace-client';
import {
  gateway,
  type CapabilitySpec,
  type JobRow,
  type SettingsPayload,
  type TraceListResponse,
} from '@/lib/gateway';

type WorkspaceTab = 'capabilities' | 'chat' | 'traces' | 'jobs' | 'settings';

interface PageProps {
  searchParams?: Record<string, string | string[] | undefined>;
}

const fallbackSettings: SettingsPayload = {
  settings: {
    judge_provider: 'local-ollama',
    judge_model: 'llama3.2:3b',
    embedding_model: 'nomic-embed-text',
    min_cluster_size: 3,
    auto_eval_new_traces: false,
    auto_cluster_failures: false,
  },
  openai_configured: false,
  anthropic_configured: false,
};

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function normalizeTab(value: string | undefined): WorkspaceTab {
  if (value === 'chat' || value === 'traces' || value === 'jobs' || value === 'settings') {
    return value;
  }
  return 'capabilities';
}

async function loadSnapshots(capabilities: CapabilitySpec[]): Promise<CapabilitySnapshot[]> {
  return Promise.all(
    capabilities.map(async (capability) => {
      const [scorecard, activeAdapter] = await Promise.all([
        gateway.scorecard(capability.id).catch(() => null),
        gateway.activeAdapter(capability.id).catch(() => null),
      ]);
      return { capability, scorecard, activeAdapter };
    }),
  );
}

async function loadSelectedCapability(
  id: string | undefined,
): Promise<SelectedCapabilityDetail | null> {
  if (!id) return null;
  try {
    const spec = await gateway.getCapability(id);
    const [scorecard, clusters, datasets, runs, failures, replaySets, activeAdapter, recipes] =
      await Promise.all([
        gateway.scorecard(id).catch(() => null),
        gateway.clusters(id).catch(() => null),
        gateway
          .datasets(id)
          .then((result) => result.datasets)
          .catch(() => []),
        gateway
          .trainingRuns(id)
          .then((result) => result.runs)
          .catch(() => []),
        gateway
          .failures(id)
          .then((result) => result.failures)
          .catch(() => []),
        gateway
          .replaySets(id)
          .then((result) => result.replay_sets)
          .catch(() => []),
        gateway.activeAdapter(id).catch(() => null),
        gateway
          .recipes()
          .then((result) => result.recipes)
          .catch(() => []),
      ]);

    return {
      spec,
      scorecard,
      clusters,
      datasets,
      runs,
      failures,
      replaySets,
      activeAdapter,
      recipes,
    };
  } catch {
    return null;
  }
}

export default async function HomePage({ searchParams }: PageProps = {}) {
  const activeTab = normalizeTab(first(searchParams?.tab));
  const selectedCapabilityId = first(searchParams?.capability);
  const filters = {
    project_id: first(searchParams?.project_id),
    capability_id: first(searchParams?.capability_id),
    status: first(searchParams?.status),
    provider: first(searchParams?.provider),
  };

  let capabilities: CapabilitySpec[] = [];
  let loadError: string | null = null;
  try {
    capabilities = await gateway.listCapabilities();
  } catch (e) {
    loadError = (e as Error).message;
  }

  const [snapshots, settings, traces, selectedCapability] = await Promise.all([
    loadSnapshots(capabilities),
    gateway.getSettings().catch(() => fallbackSettings),
    gateway
      .traces({ ...filters, limit: 100 })
      .catch((): TraceListResponse => ({ traces: [], total: 0, limit: 100, offset: 0 })),
    loadSelectedCapability(selectedCapabilityId),
  ]);
  const jobs: JobRow[] = await gateway
    .jobs()
    .then((result) => result.jobs)
    .catch(() => []);

  return (
    <WorkspaceClient
      activeTab={activeTab}
      snapshots={snapshots}
      selectedCapability={selectedCapability}
      capabilities={capabilities}
      traces={traces}
      jobs={jobs}
      settings={settings}
      loadError={loadError}
      filters={filters}
    />
  );
}
