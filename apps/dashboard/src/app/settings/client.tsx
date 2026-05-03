'use client';

import { useState, useTransition } from 'react';

import { gateway, type SettingsPayload } from '@/lib/gateway';

export function SettingsClient({ initial }: { initial: SettingsPayload }) {
  const [form, setForm] = useState(initial.settings);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit() {
    setSaved(null);
    setError(null);
    startTransition(async () => {
      try {
        const response = await gateway.updateSettings(form);
        setForm(response.settings);
        setSaved('Applied to local runtime settings.');
      } catch (e) {
        setError((e as Error).message);
      }
    });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <section className="rounded-xl border border-neutral-200 bg-white p-5">
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-neutral-400">
          Local Runtime Knobs
        </div>
        <div className="mt-5 grid gap-4">
          <Field label="Judge provider">
            <select
              value={form.judge_provider}
              onChange={(e) => update('judge_provider', e.target.value)}
              className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none"
            >
              <option value="local-ollama">local-ollama</option>
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
            </select>
          </Field>
          <Field label="Judge model">
            <input
              value={form.judge_model}
              onChange={(e) => update('judge_model', e.target.value)}
              className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none"
            />
          </Field>
          <Field label="Embedding model">
            <input
              value={form.embedding_model}
              onChange={(e) => update('embedding_model', e.target.value)}
              className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none"
            />
          </Field>
          <Field label="Default min cluster size">
            <input
              type="number"
              min={2}
              value={form.min_cluster_size}
              onChange={(e) => update('min_cluster_size', Number(e.target.value))}
              className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none"
            />
          </Field>
          <Toggle
            label="Auto-evaluate new traces"
            checked={form.auto_eval_new_traces}
            onChange={(checked) => update('auto_eval_new_traces', checked)}
          />
          <Toggle
            label="Auto-cluster failures"
            checked={form.auto_cluster_failures}
            onChange={(checked) => update('auto_cluster_failures', checked)}
          />
        </div>

        <div className="mt-6 flex items-center gap-3">
          <button
            type="button"
            onClick={submit}
            disabled={isPending}
            className="rounded-full bg-neutral-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:opacity-60"
          >
            {isPending ? 'Applying...' : 'Apply settings'}
          </button>
          {saved ? <span className="text-sm text-emerald-700">{saved}</span> : null}
          {error ? <span className="text-sm text-red-600">{error}</span> : null}
        </div>
      </section>

      <section className="rounded-xl border border-neutral-200 bg-white p-5">
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-neutral-400">
          Runtime Status
        </div>
        <dl className="mt-5 space-y-4 text-sm">
          <Row label="OpenAI configured" value={initial.openai_configured ? 'yes' : 'no'} />
          <Row label="Anthropic configured" value={initial.anthropic_configured ? 'yes' : 'no'} />
          <Row label="Environment" value={initial.runtime?.env ?? 'local'} />
          <Row label="Data dir" value={initial.runtime?.data_dir ?? '-'} mono />
          <Row label="Ollama URL" value={initial.runtime?.ollama_url ?? '-'} mono />
          <Row label="Redis URL" value={initial.runtime?.redis_url ?? '-'} mono />
        </dl>
        <p className="mt-6 text-sm leading-6 text-neutral-500">
          Secrets remain env-managed. This page only edits non-secret local knobs stored under the
          shared FlyChain data directory.
        </p>
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-2">
      <span className="text-sm font-medium text-neutral-700">{label}</span>
      {children}
    </label>
  );
}

function Toggle(props: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex items-center justify-between rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm">
      <span>{props.label}</span>
      <input
        type="checkbox"
        checked={props.checked}
        onChange={(e) => props.onChange(e.target.checked)}
        className="h-4 w-4"
      />
    </label>
  );
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid gap-1">
      <dt className="text-xs font-semibold uppercase tracking-[0.18em] text-neutral-400">
        {label}
      </dt>
      <dd className={mono ? 'font-mono text-neutral-700' : 'text-neutral-700'}>{value}</dd>
    </div>
  );
}
