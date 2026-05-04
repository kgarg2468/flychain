'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';

import { ChatClient } from './chat/client';
import { CapabilityControls } from './capabilities/[id]/controls';
import { SettingsClient } from './settings/client';
import { gateway } from '@/lib/gateway';
import type {
  ActiveAdapter,
  AutopilotDecision,
  AutopilotStatusResponse,
  CapabilitySpec,
  ClustersResponse,
  DatasetEntry,
  FailureRow,
  FlywheelSnapshot,
  GuidedAction,
  GuidedActionsResponse,
  JobRow,
  RecipeRow,
  ReplaySetRecord,
  Scorecard,
  SettingsPayload,
  TraceListResponse,
  TrainingRunRow,
} from '@/lib/gateway';

type WorkspaceTab = 'capabilities' | 'chat' | 'traces' | 'jobs' | 'settings';

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
  flywheel: FlywheelSnapshot | null;
  guidedActions: GuidedActionsResponse | null;
  autopilot: AutopilotStatusResponse | null;
}

interface WorkspaceClientProps {
  activeTab: WorkspaceTab;
  snapshots: CapabilitySnapshot[];
  selectedCapability: SelectedCapabilityDetail | null;
  capabilities: CapabilitySpec[];
  traces: TraceListResponse;
  jobs: JobRow[];
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
  { id: 'jobs', label: 'Jobs' },
  { id: 'settings', label: 'Settings' },
];

export function WorkspaceClient({
  activeTab,
  snapshots,
  selectedCapability,
  capabilities,
  traces,
  jobs,
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
          {activeTab === 'jobs' ? <JobsPanel jobs={jobs} /> : null}
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

      <AutopilotPolicyPanel capabilityId={selected.spec.id} autopilot={selected.autopilot} />

      <GuidedActionsPanel capabilityId={selected.spec.id} guidedActions={selected.guidedActions} />

      {selected.flywheel ? (
        <CapabilityFlywheel capabilityId={selected.spec.id} flywheel={selected.flywheel} />
      ) : (
        <LegacyCapabilitySummary selected={selected} />
      )}

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

function AutopilotPolicyPanel({
  capabilityId,
  autopilot,
}: {
  capabilityId: string;
  autopilot: AutopilotStatusResponse | null;
}) {
  const router = useRouter();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const policy = autopilot?.policy;
  const [enabled, setEnabled] = useState(policy?.enabled ?? false);
  const [minCorrected, setMinCorrected] = useState(policy?.min_corrected_failures ?? 3);
  const [minCluster, setMinCluster] = useState(policy?.min_cluster_size ?? 3);
  const [allowedRecipes, setAllowedRecipes] = useState(
    (policy?.allowed_training_recipes ?? ['sft-mlx-lora-local-3b']).join(', '),
  );
  const [autoGenerateCorrections, setAutoGenerateCorrections] = useState(
    policy?.auto_generate_corrections ?? false,
  );
  const [allowGeneratedCorrections, setAllowGeneratedCorrections] = useState(
    policy?.allow_generated_corrections ?? false,
  );
  const [autoCreateDataset, setAutoCreateDataset] = useState(policy?.auto_create_dataset ?? true);
  const [autoStartTraining, setAutoStartTraining] = useState(policy?.auto_start_training ?? true);
  const [autoRunValidation, setAutoRunValidation] = useState(
    policy?.auto_run_served_validation ?? true,
  );
  const [autoPromote, setAutoPromote] = useState(policy?.auto_promote ?? false);
  const [requirePromotionApproval, setRequirePromotionApproval] = useState(
    policy?.require_promotion_approval ?? true,
  );
  const [allowDryRunFallback, setAllowDryRunFallback] = useState(
    policy?.allow_dry_run_fallback ?? false,
  );
  const [requireServedValidation, setRequireServedValidation] = useState(
    policy?.require_served_validation ?? true,
  );
  const [maxTrainingRunsPerDay, setMaxTrainingRunsPerDay] = useState(
    policy?.max_training_runs_per_day ?? 1,
  );
  const [promotionCooldownSeconds, setPromotionCooldownSeconds] = useState(
    policy?.promotion_cooldown_seconds ?? 86400,
  );
  const [rollbackMode, setRollbackMode] = useState<'disable_current' | 'restore_previous'>(
    policy?.rollback_mode === 'restore_previous' ? 'restore_previous' : 'disable_current',
  );

  if (!autopilot || !policy) {
    return (
      <section className="border border-neutral-200 bg-white p-5">
        <h3 className="text-base font-semibold tracking-tight">Autopilot policy</h3>
        <EmptyInline>Autopilot readiness is unavailable.</EmptyInline>
      </section>
    );
  }

  function run(label: string, action: () => Promise<string>) {
    setMessage(null);
    setError(null);
    setPendingAction(label);
    startTransition(async () => {
      try {
        setMessage(await action());
        router.refresh();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPendingAction(null);
      }
    });
  }

  function savePolicy() {
    run('save', async () => {
      const recipes = allowedRecipes
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
      await gateway.updateAutopilotPolicy(capabilityId, {
        enabled,
        min_corrected_failures: minCorrected,
        min_cluster_size: minCluster,
        allowed_training_recipes: recipes.length > 0 ? recipes : ['sft-mlx-lora-local-3b'],
        auto_generate_corrections: autoGenerateCorrections,
        allow_generated_corrections: allowGeneratedCorrections,
        auto_create_dataset: autoCreateDataset,
        auto_start_training: autoStartTraining,
        auto_run_served_validation: autoRunValidation,
        auto_promote: autoPromote,
        require_promotion_approval: requirePromotionApproval,
        allow_dry_run_fallback: allowDryRunFallback,
        require_served_validation: requireServedValidation,
        max_training_runs_per_day: maxTrainingRunsPerDay,
        promotion_cooldown_seconds: promotionCooldownSeconds,
        rollback_mode: rollbackMode,
      });
      return 'Autopilot policy saved.';
    });
  }

  function approvePending() {
    const pendingApproval = autopilot?.pending_approval;
    if (!pendingApproval) return;
    run('approve', async () => {
      await gateway.approveAutopilot(capabilityId, pendingApproval.id, { approved: true });
      return 'Autopilot promotion approved.';
    });
  }

  function rollbackActiveAdapter() {
    run('rollback', async () => {
      await gateway.rollbackCapability(capabilityId, {
        reason: 'operator rollback from dashboard',
      });
      return 'Rollback recorded.';
    });
  }

  return (
    <section className="border border-neutral-200 bg-white p-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div>
          <h3 className="text-base font-semibold tracking-tight">Autopilot policy</h3>
          <div className="mt-1 text-sm text-neutral-600">
            Gateway-owned automation reuses guided readiness and stops on blocked, waiting, or
            approval states.
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className={statusPillClass(enabled ? 'active' : 'blocked')}>
              {enabled ? 'Policy enabled' : 'Policy disabled'}
            </span>
            <span className={statusPillClass(autopilot.pending_approval ? 'pending' : 'complete')}>
              {autopilot.pending_approval ? 'operator approval pending' : 'no pending approval'}
            </span>
            <span className={statusPillClass(policy.auto_promote ? 'active' : 'blocked')}>
              {policy.auto_promote ? 'auto-promote allowed' : 'manual promotion approval'}
            </span>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
          <Metric label="Policy version" value={String(policy.version)} />
          <Metric label="Corrected" value={String(autopilot.readiness.corrected_failures ?? 0)} />
          <Metric label="Eligible" value={String(autopilot.readiness.eligible_failures ?? 0)} />
          <Metric label="Runs" value={String(autopilot.readiness.training_runs ?? 0)} />
        </div>
      </div>

      {message ? (
        <div className="mt-4 border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {message}
        </div>
      ) : null}
      {error ? (
        <div className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <div className="grid gap-3 text-sm md:grid-cols-2">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            <span>Enable autopilot</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={autoGenerateCorrections}
              onChange={(event) => setAutoGenerateCorrections(event.target.checked)}
            />
            <span>Generate corrections</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={allowGeneratedCorrections}
              onChange={(event) => setAllowGeneratedCorrections(event.target.checked)}
            />
            <span>Allow generated dataset rows</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={allowDryRunFallback}
              onChange={(event) => setAllowDryRunFallback(event.target.checked)}
            />
            <span>Allow dry-run fallback</span>
          </label>
          <PolicyNumber
            label="Min corrected failures"
            value={minCorrected}
            onChange={setMinCorrected}
          />
          <PolicyNumber label="Min cluster size" value={minCluster} onChange={setMinCluster} />
          <PolicyNumber
            label="Training runs per day"
            value={maxTrainingRunsPerDay}
            onChange={setMaxTrainingRunsPerDay}
          />
          <PolicyNumber
            label="Promotion cooldown seconds"
            value={promotionCooldownSeconds}
            onChange={setPromotionCooldownSeconds}
          />
          <label className="md:col-span-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
              Allowed recipes
            </span>
            <input
              value={allowedRecipes}
              onChange={(event) => setAllowedRecipes(event.target.value)}
              className="mt-1 w-full border border-neutral-200 bg-neutral-50 px-3 py-2 font-mono text-xs outline-none focus:border-neutral-500"
            />
          </label>
        </div>

        <div className="grid gap-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <PolicyCheckbox
              label="Auto-create dataset"
              checked={autoCreateDataset}
              onChange={setAutoCreateDataset}
            />
            <PolicyCheckbox
              label="Auto-start training"
              checked={autoStartTraining}
              onChange={setAutoStartTraining}
            />
            <PolicyCheckbox
              label="Auto-run validation"
              checked={autoRunValidation}
              onChange={setAutoRunValidation}
            />
            <PolicyCheckbox label="Auto-promote" checked={autoPromote} onChange={setAutoPromote} />
            <PolicyCheckbox
              label="Require promotion approval"
              checked={requirePromotionApproval}
              onChange={setRequirePromotionApproval}
            />
            <PolicyCheckbox
              label="Require served validation"
              checked={requireServedValidation}
              onChange={setRequireServedValidation}
            />
          </div>
          <label>
            <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
              Rollback mode
            </span>
            <select
              value={rollbackMode}
              onChange={(event) =>
                setRollbackMode(
                  event.target.value === 'restore_previous'
                    ? 'restore_previous'
                    : 'disable_current',
                )
              }
              className="mt-1 w-full border border-neutral-200 bg-white px-3 py-2 text-sm outline-none focus:border-neutral-500"
            >
              <option value="disable_current">disable_current</option>
              <option value="restore_previous">restore_previous</option>
            </select>
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={isPending}
              onClick={savePolicy}
              className="bg-neutral-950 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
            >
              {pendingAction === 'save' ? 'Saving...' : 'Save policy'}
            </button>
            <button
              type="button"
              disabled={isPending}
              onClick={() =>
                run('manual', async () => {
                  const result = await gateway.runAutopilot(capabilityId, 'manual');
                  return `Autopilot ${result.status}.`;
                })
              }
              className="border border-neutral-200 px-3 py-2 text-xs font-medium disabled:opacity-50"
            >
              {pendingAction === 'manual' ? 'Running...' : 'Run policy check'}
            </button>
            <button
              type="button"
              disabled={isPending}
              onClick={rollbackActiveAdapter}
              className="border border-red-200 px-3 py-2 text-xs font-medium text-red-700 disabled:opacity-50"
            >
              {pendingAction === 'rollback' ? 'Rolling back...' : 'Rollback active adapter'}
            </button>
          </div>
        </div>
      </div>

      {autopilot.pending_approval ? (
        <AutopilotApprovalPanel
          decision={autopilot.pending_approval}
          pending={pendingAction === 'approve'}
          onApprove={approvePending}
        />
      ) : null}

      <AutopilotAuditTable audit={autopilot.audit} latest={autopilot.latest_decision} />
    </section>
  );
}

function PolicyCheckbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

function PolicyNumber({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
        {label}
      </span>
      <input
        type="number"
        min={0}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 w-full border border-neutral-200 bg-neutral-50 px-3 py-2 font-mono text-xs outline-none focus:border-neutral-500"
      />
    </label>
  );
}

function AutopilotApprovalPanel({
  decision,
  pending,
  onApprove,
}: {
  decision: AutopilotDecision;
  pending: boolean;
  onApprove: () => void;
}) {
  return (
    <div className="mt-4 border border-amber-200 bg-amber-50 p-4 text-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h4 className="font-semibold">Pending autopilot approval</h4>
          <div className="mt-1 text-neutral-700">
            {decision.action} for {decision.target_id ?? 'pending target'} needs operator approval.
          </div>
        </div>
        <button
          type="button"
          disabled={pending}
          onClick={onApprove}
          className="bg-neutral-950 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
        >
          {pending ? 'Approving...' : 'Approve autopilot promotion'}
        </button>
      </div>
      <div className="mt-3 grid gap-2 text-xs md:grid-cols-4">
        <EvidenceLine label="Candidate" value={String(decision.target_id ?? '-')} />
        <EvidenceLine
          label="Validation"
          value={autopilotDecisionValidationScore(decision.result)}
        />
        <EvidenceLine label="Current active" value={currentActiveFromDecision(decision.result)} />
        <EvidenceLine label="Approval state" value={decision.approval_status ?? 'pending'} />
      </div>
    </div>
  );
}

function AutopilotAuditTable({
  audit,
  latest,
}: {
  audit: AutopilotDecision[];
  latest: AutopilotDecision | null;
}) {
  const rows = audit.length > 0 ? audit : latest ? [latest] : [];
  return (
    <section className="mt-5 border-t border-neutral-100 pt-4">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
        <h3 className="text-base font-semibold tracking-tight">Autopilot audit</h3>
        <div className="text-xs text-neutral-500">
          {latest ? `Latest outcome: ${latest.outcome}` : 'No decisions recorded'}
        </div>
      </div>
      {rows.length === 0 ? (
        <EmptyInline>No autopilot decisions recorded yet.</EmptyInline>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full divide-y divide-neutral-200 text-left text-sm">
            <thead className="bg-neutral-50 text-xs uppercase tracking-[0.14em] text-neutral-500">
              <tr>
                <th className="px-3 py-2">Trigger</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Outcome</th>
                <th className="px-3 py-2">Target</th>
                <th className="px-3 py-2">Evidence</th>
                <th className="px-3 py-2">When</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {rows.map((decision) => (
                <tr key={decision.id} className="align-top">
                  <td className="px-3 py-3 font-mono text-xs">{decision.trigger}</td>
                  <td className="px-3 py-3 font-mono text-xs">{decision.action}</td>
                  <td className="px-3 py-3">
                    <span className={statusPillClass(decision.outcome)}>{decision.outcome}</span>
                    {decision.approval_status ? (
                      <div className="mt-1 text-xs text-neutral-500">
                        approval {decision.approval_status}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 font-mono text-xs text-neutral-600">
                    {decision.target_id ?? '-'}
                  </td>
                  <td className="px-3 py-3 text-xs text-neutral-600">
                    <div>{decision.reasons.join('; ') || '-'}</div>
                    {decision.job_ids.length > 0 ? (
                      <div className="mt-1 font-mono">jobs {decision.job_ids.join(', ')}</div>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 text-xs text-neutral-500">
                    {formatTimestamp(decision.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function autopilotDecisionValidationScore(result: Record<string, unknown>): string {
  const validation = result.validation;
  if (validation && typeof validation === 'object') {
    const score = (validation as { aggregate_score?: unknown }).aggregate_score;
    if (typeof score === 'number') return score.toFixed(2);
  }
  return '-';
}

function currentActiveFromDecision(result: Record<string, unknown>): string {
  const current = result.current_active_adapter;
  if (!current || typeof current !== 'object') return '-';
  return String((current as { active_run_id?: unknown }).active_run_id ?? '-');
}

function GuidedActionsPanel({
  capabilityId,
  guidedActions,
}: {
  capabilityId: string;
  guidedActions: GuidedActionsResponse | null;
}) {
  const router = useRouter();
  const [approvalActionId, setApprovalActionId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  if (!guidedActions) {
    return (
      <section className="border border-neutral-200 bg-white p-5">
        <h3 className="text-base font-semibold tracking-tight">Guided actions</h3>
        <EmptyInline>Guided action readiness is unavailable.</EmptyInline>
      </section>
    );
  }

  const nextAction = guidedActions.actions.find((action) => action.status === 'available') ?? null;
  const approvalAction =
    guidedActions.actions.find((action) => action.id === approvalActionId) ?? null;

  function execute(action: GuidedAction, approved = false) {
    setMessage(null);
    setError(null);
    setPendingActionId(action.id);
    startTransition(async () => {
      try {
        const result = await gateway.executeGuidedAction(capabilityId, action.id, { approved });
        setMessage(guidedResultMessage(action, result.result));
        setApprovalActionId(null);
        router.refresh();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPendingActionId(null);
      }
    });
  }

  return (
    <section className="border border-neutral-200 bg-white p-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h3 className="text-base font-semibold tracking-tight">Guided actions</h3>
          <div className="mt-1 text-sm text-neutral-600">
            Next action is selected by gateway readiness. Training and promotion require inline
            approval.
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <Metric
            label="Threshold"
            value={String(guidedActions.thresholds.min_corrected_failures)}
          />
          <Metric
            label="Active run"
            value={guidedActions.active_adapter.active?.active_run_id ?? '-'}
          />
        </div>
      </div>

      {message ? (
        <div className="mt-4 border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {message}
        </div>
      ) : null}
      {error ? (
        <div className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {guidedActions.actions.length === 0 ? (
        <EmptyInline>No guided actions are available for this capability yet.</EmptyInline>
      ) : (
        <div className="mt-4 divide-y divide-neutral-100 border-y border-neutral-100">
          {guidedActions.actions.map((action) => {
            const isNext = nextAction?.id === action.id;
            const canExecute = action.status === 'available' && isNext && !isPending;
            return (
              <div
                key={action.id}
                className="grid gap-3 py-4 text-sm xl:grid-cols-[1.1fr_1fr_0.7fr]"
              >
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-neutral-800">{action.type}</span>
                    <span className={statusPillClass(action.status)}>{action.status}</span>
                    {isNext ? (
                      <span className="bg-neutral-950 px-2 py-1 text-xs font-medium text-white">
                        next
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-neutral-500">
                    {action.target_id}
                  </div>
                  <div className="mt-2 text-neutral-700">{action.reason}</div>
                  {action.blocked_reasons.length > 0 ? (
                    <div className="mt-2 grid gap-1 text-xs text-red-700">
                      {action.blocked_reasons.map((reason) => (
                        <div key={reason}>{reason}</div>
                      ))}
                    </div>
                  ) : null}
                </div>
                <GuidedActionPreview action={action} />
                <div className="flex flex-col items-start gap-2 xl:items-end">
                  <button
                    type="button"
                    disabled={!canExecute}
                    onClick={() => {
                      if (action.requires_approval) {
                        setApprovalActionId(action.id);
                      } else {
                        execute(action);
                      }
                    }}
                    className="border border-neutral-200 px-3 py-2 text-xs font-medium text-neutral-700 transition hover:border-neutral-400 hover:text-neutral-950 disabled:opacity-50"
                  >
                    {pendingActionId === action.id ? 'Running...' : guidedActionButtonLabel(action)}
                  </button>
                  {action.requires_approval ? (
                    <div className="text-xs text-neutral-500">inline review required</div>
                  ) : null}
                  {action.status === 'available' && !isNext ? (
                    <div className="max-w-[12rem] text-xs text-neutral-500">
                      Waiting for the earlier available step.
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {approvalAction ? (
        <GuidedApprovalPanel
          action={approvalAction}
          pending={pendingActionId === approvalAction.id}
          onApprove={() => execute(approvalAction, true)}
          onCancel={() => setApprovalActionId(null)}
        />
      ) : null}
    </section>
  );
}

function GuidedActionPreview({ action }: { action: GuidedAction }) {
  if (action.type === 'create_dataset') {
    return (
      <div className="grid gap-1 text-xs text-neutral-600">
        <EvidenceLine label="Included" value={previewText(action, 'included_count')} />
        <EvidenceLine label="Skipped" value={previewText(action, 'skipped_count')} />
        <EvidenceLine label="Method" value={previewText(action, 'method')} />
        <EvidenceLine label="Generate missing" value="false" />
      </div>
    );
  }
  if (action.type === 'start_training') {
    return (
      <div className="grid gap-1 text-xs text-neutral-600">
        <EvidenceLine label="Recipe" value={previewText(action, 'recipe_id')} />
        <EvidenceLine label="Backend" value={previewText(action, 'recipe_backend')} />
        <EvidenceLine label="Rows" value={previewText(action, 'row_count')} />
        <EvidenceLine label="MLX health" value={previewHealth(action)} />
        <EvidenceLine label="Fallback" value="fallback disabled" />
      </div>
    );
  }
  if (action.type === 'run_served_validation') {
    return (
      <div className="grid gap-1 text-xs text-neutral-600">
        <EvidenceLine label="Managed replay" value={previewText(action, 'managed_replay_name')} />
        <EvidenceLine label="Validation" value={previewText(action, 'served_validation_status')} />
      </div>
    );
  }
  if (action.type === 'promote_adapter') {
    return (
      <div className="grid gap-1 text-xs text-neutral-600">
        <EvidenceLine label="Candidate" value={previewText(action, 'candidate_run_id')} />
        <EvidenceLine label="Validation" value={previewText(action, 'validation_status')} />
        <EvidenceLine label="Score" value={previewText(action, 'validation_score')} />
        <EvidenceLine label="Current active" value={currentActivePreview(action)} />
      </div>
    );
  }
  return <div className="text-xs text-neutral-500">{JSON.stringify(action.preview)}</div>;
}

function GuidedApprovalPanel({
  action,
  pending,
  onApprove,
  onCancel,
}: {
  action: GuidedAction;
  pending: boolean;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const isPromotion = action.type === 'promote_adapter';
  return (
    <div className="mt-4 border border-neutral-300 bg-neutral-50 p-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h4 className="text-sm font-semibold">
            {isPromotion ? 'Promotion approval' : 'Training approval'}
          </h4>
          <div className="mt-1 text-sm text-neutral-600">
            {isPromotion
              ? 'This replaces the current active adapter pointer.'
              : 'This queues one training run with backend fallback disabled.'}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={pending}
            onClick={onApprove}
            className="bg-neutral-950 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
          >
            {pending ? 'Executing...' : isPromotion ? 'Approve promotion' : 'Approve training'}
          </button>
          <button
            type="button"
            disabled={pending}
            onClick={onCancel}
            className="border border-neutral-200 px-3 py-2 text-xs font-medium disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </div>
      <div className="mt-4 grid gap-2 text-xs md:grid-cols-2 lg:grid-cols-4">
        {isPromotion ? (
          <>
            <EvidenceLine label="Current active" value={currentActivePreview(action)} />
            <EvidenceLine label="Candidate" value={previewText(action, 'candidate_run_id')} />
            <EvidenceLine label="Validation" value={previewText(action, 'validation_status')} />
            <EvidenceLine label="Score" value={previewText(action, 'validation_score')} />
          </>
        ) : (
          <>
            <EvidenceLine label="Recipe" value={previewText(action, 'recipe_id')} />
            <EvidenceLine label="Backend" value={previewText(action, 'recipe_backend')} />
            <EvidenceLine label="Rows" value={previewText(action, 'row_count')} />
            <EvidenceLine label="Fallback" value="fallback disabled" />
            <EvidenceLine label="MLX health" value={previewHealth(action)} />
          </>
        )}
      </div>
    </div>
  );
}

function guidedActionButtonLabel(action: GuidedAction): string {
  if (action.status === 'blocked') return 'Blocked';
  if (action.status === 'running') return 'Running';
  if (action.status === 'complete') return 'Complete';
  if (action.requires_approval) return 'Review approval';
  if (action.type === 'create_dataset') return 'Create dataset';
  if (action.type === 'run_served_validation') return 'Run validation';
  return 'Execute';
}

function guidedResultMessage(action: GuidedAction, result: Record<string, unknown>): string {
  if (action.type === 'create_dataset')
    return `Dataset ${String(result.dataset_id ?? '')} created.`;
  if (action.type === 'start_training')
    return `Training run ${String(result.run_id ?? '')} queued.`;
  if (action.type === 'run_served_validation') {
    return `Served validation queued for ${String(result.run_id ?? '')}.`;
  }
  if (action.type === 'promote_adapter') {
    return `Adapter ${String(result.active_run_id ?? '')} promoted.`;
  }
  return 'Guided action executed.';
}

function previewText(action: GuidedAction, key: string): string {
  const value = action.preview[key];
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

function previewHealth(action: GuidedAction): string {
  const health = action.preview.mlx_health;
  if (!health || typeof health !== 'object') return '-';
  const row = health as { status?: unknown; detail?: unknown; target?: unknown };
  const status = String(row.status ?? '-');
  const detail = row.detail ? ` / ${String(row.detail)}` : '';
  const target = row.target ? ` / ${String(row.target)}` : '';
  return `${status}${target}${detail}`;
}

function currentActivePreview(action: GuidedAction): string {
  const current = action.preview.current_active_adapter;
  if (!current || typeof current !== 'object') return '-';
  const active = current as { active_run_id?: unknown };
  return String(active.active_run_id ?? '-');
}

function LegacyCapabilitySummary({ selected }: { selected: SelectedCapabilityDetail }) {
  return (
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
              {run.served_validation ? (
                <div className="mt-1 text-xs text-neutral-500">
                  validation {run.served_validation.status}
                  {typeof run.served_validation.aggregate_score === 'number'
                    ? ` / ${run.served_validation.aggregate_score.toFixed(2)}`
                    : ''}
                </div>
              ) : null}
            </div>
          ))
        )}
      </SummarySection>
    </div>
  );
}

function CapabilityFlywheel({
  capabilityId,
  flywheel,
}: {
  capabilityId: string;
  flywheel: FlywheelSnapshot;
}) {
  const active = flywheel.summary.active_adapter;
  const validation = flywheel.summary.latest_served_validation;
  return (
    <div className="flex flex-col gap-5">
      <section className="border border-neutral-200 bg-white p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h3 className="text-base font-semibold tracking-tight">Flywheel status</h3>
            <p className="mt-1 text-sm text-neutral-600">
              Capability evidence from trace capture through active adapter serving.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
            <Metric label="Traces" value={String(flywheel.summary.total_traces)} />
            <Metric label="Evaluated" value={String(flywheel.summary.evaluated_traces)} />
            <Metric label="Failures" value={String(flywheel.summary.failing_traces)} />
            <Metric label="Unresolved" value={String(flywheel.summary.unresolved_failures)} />
            <Metric label="Clusters" value={String(flywheel.summary.clusters)} />
            <Metric label="Datasets" value={String(flywheel.summary.datasets)} />
            <Metric label="Runs" value={String(flywheel.summary.training_runs)} />
            <Metric label="Validation" value={validation?.status ?? '-'} />
          </div>
        </div>
        <div className="mt-4 grid gap-3 text-sm md:grid-cols-3">
          <EvidenceLine label="Active run" value={active?.active_run_id ?? '-'} />
          <EvidenceLine
            label="Last adapted chat"
            value={flywheel.summary.last_adapted_chat?.trace_id ?? '-'}
          />
          <EvidenceLine label="Adapter provider" value={validation?.provider ?? '-'} />
          <EvidenceLine label="Adapter model" value={validation?.model ?? '-'} />
        </div>
      </section>

      <section className="border border-neutral-200 bg-white p-5">
        <h3 className="text-base font-semibold tracking-tight">Lifecycle timeline</h3>
        <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-5">
          {flywheel.timeline.map((step) => (
            <a
              key={step.id}
              href={step.href}
              className="border border-neutral-200 px-3 py-3 text-sm transition hover:border-neutral-400"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium">{step.label}</span>
                <span className={statusPillClass(step.status)}>{step.status}</span>
              </div>
              <div className="mt-2 text-xs text-neutral-500">{step.count} item(s)</div>
              <div className="mt-1 text-xs text-neutral-500">
                {step.latest_ts ? formatTimestamp(step.latest_ts) : 'No timestamp'}
              </div>
              {step.action_needed ? (
                <div className="mt-2 text-xs font-medium text-amber-700">{step.action_needed}</div>
              ) : null}
            </a>
          ))}
        </div>
      </section>

      <FailureInbox capabilityId={capabilityId} failures={flywheel.failures} />
      <ClusterEvidence clusters={flywheel.clusters} />
      <DatasetEvidence datasets={flywheel.datasets} />
      <TrainingRunEvidence runs={flywheel.training_runs} />
      <BeforeAfterEvidence comparison={flywheel.before_after} />
    </div>
  );
}

function EvidenceLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-t border-neutral-100 pt-3">
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
        {label}
      </div>
      <div className="mt-1 break-all font-mono text-xs text-neutral-700">{value}</div>
    </div>
  );
}

function FailureInbox({
  capabilityId,
  failures,
}: {
  capabilityId: string;
  failures: FailureRow[];
}) {
  return (
    <section id="failures" className="border border-neutral-200 bg-white p-5">
      <h3 className="text-base font-semibold tracking-tight">Failure inbox</h3>
      {failures.length === 0 ? (
        <EmptyInline>No failing traces for this capability.</EmptyInline>
      ) : (
        <div className="mt-3 divide-y divide-neutral-100">
          {failures.map((failure) => (
            <div key={failure.trace_id} className="grid gap-3 py-4 lg:grid-cols-[1fr_280px]">
              <div className="min-w-0">
                <div className="font-mono text-xs text-neutral-500">{failure.trace_id}</div>
                <div className="mt-2 text-sm font-medium">{failure.input}</div>
                <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">
                  <OutputBlock label="Bad output" value={failure.output} />
                  <OutputBlock
                    label="Correction"
                    value={failure.corrected_response ?? 'No correction'}
                  />
                </div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <span className={statusPillClass(failure.correction_status ?? 'uncorrected')}>
                    {failure.correction_status ?? 'uncorrected'}
                  </span>
                  <span className={statusPillClass(failure.review_status ?? 'needs_correction')}>
                    {failure.review_status ?? 'needs_correction'}
                  </span>
                  <span
                    className={statusPillClass(failure.dataset_eligible ? 'eligible' : 'blocked')}
                  >
                    {failure.dataset_eligible ? 'dataset eligible' : 'dataset blocked'}
                  </span>
                  {(failure.cluster_ids ?? []).map((clusterId) => (
                    <span key={clusterId} className="font-mono text-neutral-500">
                      {clusterId}
                    </span>
                  ))}
                </div>
                <div className="mt-3 grid gap-2">
                  {(failure.dimension_results ?? []).map((dimension) => (
                    <div key={dimension.dimension} className="text-xs text-neutral-600">
                      <span className="font-mono text-neutral-800">{dimension.dimension}</span>
                      {' / '}
                      {dimension.reason}
                      {' / '}
                      <span className="font-mono">{dimension.evaluator_source}</span>
                    </div>
                  ))}
                </div>
              </div>
              <FailureActions capabilityId={capabilityId} failure={failure} />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function FailureActions({ capabilityId, failure }: { capabilityId: string; failure: FailureRow }) {
  const router = useRouter();
  const [correction, setCorrection] = useState(failure.corrected_response ?? '');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function run(action: () => Promise<string>) {
    setMessage(null);
    setError(null);
    startTransition(async () => {
      try {
        setMessage(await action());
        router.refresh();
      } catch (e) {
        setError((e as Error).message);
      }
    });
  }

  return (
    <div className="grid gap-2 text-sm">
      <textarea
        aria-label={`Correction for ${failure.trace_id}`}
        value={correction}
        onChange={(event) => setCorrection(event.target.value)}
        rows={4}
        className="resize-none border border-neutral-200 bg-neutral-50 px-3 py-2 outline-none focus:border-neutral-500"
      />
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={pending || !correction.trim()}
          onClick={() =>
            run(async () => {
              await gateway.submitFeedback({
                trace_id: failure.trace_id,
                project_id: failure.project_id,
                thumb: 'down',
                corrected_response: correction.trim(),
              });
              return 'Correction saved.';
            })
          }
          className="border border-neutral-200 px-3 py-2 text-xs font-medium disabled:opacity-50"
        >
          Save correction
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() =>
            run(async () => {
              await gateway.reviewFailure(capabilityId, failure.trace_id, {
                status: 'not_useful',
              });
              return 'Failure marked not useful.';
            })
          }
          className="border border-neutral-200 px-3 py-2 text-xs font-medium disabled:opacity-50"
        >
          Mark not useful
        </button>
      </div>
      {message ? <div className="text-xs text-emerald-700">{message}</div> : null}
      {error ? <div className="text-xs text-red-700">{error}</div> : null}
    </div>
  );
}

function OutputBlock({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
        {label}
      </div>
      <div className="mt-1 whitespace-pre-wrap text-sm text-neutral-700">{value}</div>
    </div>
  );
}

function ClusterEvidence({ clusters }: { clusters: FlywheelSnapshot['clusters'] }) {
  return (
    <section id="clusters" className="border border-neutral-200 bg-white p-5">
      <h3 className="text-base font-semibold tracking-tight">Clusters</h3>
      {clusters.length === 0 ? (
        <EmptyInline>No clusters stored yet.</EmptyInline>
      ) : (
        <div className="mt-3 divide-y divide-neutral-100">
          {clusters.map((cluster) => (
            <div
              key={cluster.id}
              className="grid gap-3 py-3 text-sm lg:grid-cols-[1.1fr_0.7fr_1.2fr_1fr]"
            >
              <div>
                <div className="font-medium">{cluster.label}</div>
                <div className="font-mono text-xs text-neutral-500">{cluster.id}</div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {cluster.trace_ids.map((traceId) => (
                    <span key={traceId} className="font-mono text-[11px] text-neutral-500">
                      {traceId}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div>{cluster.size} trace(s)</div>
                <div className={statusPillClass(cluster.dataset_eligible ? 'ready' : 'blocked')}>
                  {cluster.dataset_eligible ? 'ready' : 'blocked'}
                </div>
              </div>
              <div>
                <div>
                  {cluster.correction_coverage?.corrected ?? 0}/
                  {cluster.correction_coverage?.total ?? cluster.size} corrected
                </div>
                <div className="mt-2 grid gap-1">
                  {(cluster.representative_failures ?? []).slice(0, 2).map((failure) => (
                    <div key={failure.trace_id} className="text-xs text-neutral-600">
                      {failure.input}
                    </div>
                  ))}
                </div>
              </div>
              <div className="font-mono text-xs text-neutral-500">
                {cluster.latest_dataset_id ?? 'no dataset'}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function DatasetEvidence({ datasets }: { datasets: FlywheelSnapshot['datasets'] }) {
  return (
    <section id="datasets" className="border border-neutral-200 bg-white p-5">
      <h3 className="text-base font-semibold tracking-tight">Datasets</h3>
      {datasets.length === 0 ? (
        <EmptyInline>No datasets synthesized yet.</EmptyInline>
      ) : (
        <div className="mt-3 divide-y divide-neutral-100">
          {datasets.map((dataset) => (
            <div
              key={dataset.id}
              className="grid gap-2 py-3 text-sm lg:grid-cols-[1fr_0.6fr_0.6fr_1fr_1fr_1fr]"
            >
              <div>
                <div className="font-mono text-xs">{dataset.id}</div>
                <div className="mt-1 font-mono text-[11px] text-neutral-500">
                  {dataset.cluster_id ?? 'no cluster'}
                </div>
              </div>
              <div>{dataset.method}</div>
              <div>{dataset.row_count} rows</div>
              <div>
                human {dataset.correction_source?.human ?? 0} / generated{' '}
                {dataset.correction_source?.generated ?? 0}
              </div>
              <div className="font-mono text-xs text-neutral-500">
                {(dataset.training_run_ids ?? []).join(', ') || 'no runs'}
              </div>
              <div className="break-all font-mono text-[11px] text-neutral-500">{dataset.path}</div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function TrainingRunEvidence({ runs }: { runs: FlywheelSnapshot['training_runs'] }) {
  return (
    <section id="runs" className="border border-neutral-200 bg-white p-5">
      <h3 className="text-base font-semibold tracking-tight">Training runs</h3>
      {runs.length === 0 ? (
        <EmptyInline>No training runs yet.</EmptyInline>
      ) : (
        <div className="mt-3 divide-y divide-neutral-100">
          {runs.map((run) => (
            <div key={run.id} className="grid gap-3 py-3 text-sm">
              <div className="grid gap-2 xl:grid-cols-6">
                <div>
                  <div className="font-mono text-xs">{run.id}</div>
                  <div className="text-xs text-neutral-500">{run.recipe_id}</div>
                </div>
                <div>{run.dataset_id}</div>
                <div className={statusPillClass(run.status)}>
                  {run.status === 'promoted' ? 'trained' : run.status}
                </div>
                <div className={statusPillClass(run.validation_status ?? 'unvalidated')}>
                  {run.validation_status === 'passed'
                    ? 'validated'
                    : (run.validation_status ?? 'unvalidated')}
                </div>
                <div className={statusPillClass(run.gate_status ?? 'no gate')}>
                  {run.gate_status === 'promote' ? 'promoted' : (run.gate_status ?? 'no gate')}
                </div>
                <div className={statusPillClass(run.active ? 'active' : 'inactive')}>
                  {run.active ? 'active' : 'inactive'}
                </div>
              </div>
              <div className="grid gap-2 text-xs text-neutral-600 md:grid-cols-4">
                <EvidenceLine label="Backend" value={String(run.artifact?.backend ?? '-')} />
                <EvidenceLine
                  label="Offline score"
                  value={formatMaybeNumber(run.candidate?.[run.capability_id])}
                />
                <EvidenceLine
                  label="Served validation"
                  value={formatMaybeNumber(run.served_validation?.aggregate_score)}
                />
                <EvidenceLine label="Artifact" value={run.artifact_path ?? '-'} />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function BeforeAfterEvidence({ comparison }: { comparison: FlywheelSnapshot['before_after'] }) {
  return (
    <section id="before-after" className="border border-neutral-200 bg-white p-5">
      <h3 className="text-base font-semibold tracking-tight">Before / After</h3>
      {!comparison ? (
        <EmptyInline>No comparison evidence stored yet.</EmptyInline>
      ) : (
        <div className="mt-3 grid gap-4 text-sm lg:grid-cols-2">
          <OutputBlock label="Prompt" value={comparison.input ?? '-'} />
          <OutputBlock label="Verdict" value={comparison.final_verdict ?? '-'} />
          <OutputBlock label="Baseline output" value={comparison.baseline_output ?? '-'} />
          <OutputBlock label="Adapted output" value={comparison.adapted_output ?? '-'} />
          <EvidenceLine
            label="Adapter run"
            value={String(comparison.adapter_proof?.adapter_run_id ?? comparison.run_id ?? '-')}
          />
          <EvidenceLine
            label="Adapter provider"
            value={String(comparison.adapter_proof?.provider ?? '-')}
          />
          <EvidenceLine
            label="Adapter model"
            value={String(comparison.adapter_proof?.model ?? '-')}
          />
          <EvidenceLine
            label="Routing mode"
            value={String(comparison.adapter_proof?.routing_mode ?? '-')}
          />
          <div className="lg:col-span-2">
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-neutral-400">
              Evaluator scores
            </div>
            <div className="mt-2 grid gap-2">
              {(comparison.evaluator_scores ?? []).map((score) => (
                <div key={`${score.dimension}-${score.ts}`} className="text-xs text-neutral-600">
                  <span className="font-mono text-neutral-800">{score.dimension}</span>
                  {' / '}
                  {formatMaybeNumber(score.score)}
                  {' / '}
                  {score.passed ? 'passed' : 'failed'}
                  {' / '}
                  {score.reason}
                  {' / '}
                  <span className="font-mono">{score.evaluator_source}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function statusPillClass(status: string): string {
  const normalized = status.toLowerCase();
  if (
    ['complete', 'passed', 'promoted', 'active', 'corrected', 'eligible', 'ready'].includes(
      normalized,
    )
  ) {
    return 'inline-flex w-fit bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700';
  }
  if (['failed', 'blocked', 'not_useful', 'validation-failed'].includes(normalized)) {
    return 'inline-flex w-fit bg-red-50 px-2 py-1 text-xs font-medium text-red-700';
  }
  if (['pending', 'running', 'queued', 'needs_correction', 'unvalidated'].includes(normalized)) {
    return 'inline-flex w-fit bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700';
  }
  return 'inline-flex w-fit bg-neutral-100 px-2 py-1 text-xs font-medium text-neutral-700';
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatMaybeNumber(value: number | null | undefined) {
  return typeof value === 'number' ? value.toFixed(2) : '-';
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

function JobsPanel({ jobs }: { jobs: JobRow[] }) {
  const router = useRouter();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const activeCount = jobs.filter((job) =>
    ['queued', 'running', 'retrying'].includes(job.status),
  ).length;
  const failedCount = jobs.filter((job) => ['failed', 'timed_out'].includes(job.status)).length;

  function retry(job: JobRow) {
    setMessage(null);
    setError(null);
    setPendingJobId(job.id);
    startTransition(async () => {
      try {
        const retried = await gateway.retryJob(job.id);
        setMessage(`${retried.id} queued for retry.`);
        router.refresh();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPendingJobId(null);
      }
    });
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-400">
            Jobs
          </div>
          <h2 className="mt-1 text-xl font-semibold tracking-tight">Jobs</h2>
        </div>
        <dl className="grid grid-cols-3 gap-4 text-sm">
          <Metric label="Recent" value={String(jobs.length)} />
          <Metric label="Active" value={String(activeCount)} />
          <Metric label="Failed" value={String(failedCount)} />
        </dl>
      </div>

      {message ? (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {message}
        </div>
      ) : null}
      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <section className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
        {jobs.length === 0 ? (
          <div className="p-8 text-sm text-neutral-500">No background jobs recorded yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-neutral-200 text-left text-sm">
              <thead className="bg-neutral-50 text-xs uppercase tracking-[0.14em] text-neutral-500">
                <tr>
                  <th className="px-4 py-3">Job</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Target</th>
                  <th className="px-4 py-3">Runtime</th>
                  <th className="px-4 py-3">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-100">
                {jobs.map((job) => {
                  const retryable =
                    ['failed', 'timed_out'].includes(job.status) &&
                    job.retry_count < job.max_retries;
                  return (
                    <tr key={job.id} className="align-top">
                      <td className="px-4 py-4">
                        <div className="font-mono text-xs text-neutral-700">{job.id}</div>
                        <div className="mt-1 text-xs text-neutral-500">{job.type}</div>
                        {job.error ? (
                          <div className="mt-2 max-w-md text-xs text-red-600">{job.error}</div>
                        ) : null}
                      </td>
                      <td className="px-4 py-4">
                        <span
                          className={`rounded-full px-2 py-1 text-xs font-medium ${jobStatusClass(job.status)}`}
                        >
                          {job.status}
                        </span>
                      </td>
                      <td className="px-4 py-4 text-xs text-neutral-600">
                        <div>{job.capability_id ?? '-'}</div>
                        {job.run_id ? <div className="mt-1 font-mono">{job.run_id}</div> : null}
                        {job.replay_set_id ? (
                          <div className="mt-1 font-mono">{job.replay_set_id}</div>
                        ) : null}
                      </td>
                      <td className="px-4 py-4 text-xs text-neutral-600">
                        <div>
                          {job.duration_ms === null || job.duration_ms === undefined
                            ? '-'
                            : `${job.duration_ms}ms`}
                        </div>
                        <div className="mt-1">
                          retries {job.retry_count}/{job.max_retries}
                        </div>
                      </td>
                      <td className="px-4 py-4">
                        <button
                          type="button"
                          disabled={!retryable || isPending}
                          onClick={() => retry(job)}
                          className="rounded-lg border border-neutral-200 px-3 py-2 text-xs font-medium text-neutral-700 transition hover:border-neutral-400 hover:text-neutral-950 disabled:opacity-50"
                        >
                          {pendingJobId === job.id ? 'Retrying...' : 'Retry'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function jobStatusClass(status: string): string {
  if (status === 'succeeded') return 'bg-emerald-50 text-emerald-700';
  if (status === 'failed' || status === 'timed_out') return 'bg-red-50 text-red-700';
  if (status === 'running' || status === 'queued' || status === 'retrying') {
    return 'bg-amber-50 text-amber-700';
  }
  return 'bg-neutral-100 text-neutral-700';
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
