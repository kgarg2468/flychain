'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import type { CapabilitySpec, InterviewQuestion } from '@/lib/gateway';

type Mode = 'recommended' | 'describe';

export function NewCapabilityClient({ templates }: { templates: CapabilitySpec[] }) {
  const [mode, setMode] = useState<Mode>('recommended');

  return (
    <div className="flex flex-col gap-8">
      <ModeToggle mode={mode} onChange={setMode} />
      {mode === 'recommended' ? <RecommendedFlow templates={templates} /> : <DescribeFlow />}
    </div>
  );
}

function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  const modes: { id: Mode; title: string; body: string }[] = [
    {
      id: 'recommended',
      title: 'Recommended',
      body: 'Pick from a curated template. Fastest path.',
    },
    {
      id: 'describe',
      title: 'Describe',
      body: 'Type a goal in plain language. The compiler turns it into a spec.',
    },
  ];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {modes.map((m) => {
        const active = mode === m.id;
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => onChange(m.id)}
            className={`rounded-lg border p-4 text-left transition ${
              active
                ? 'border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900'
                : 'border-neutral-200 bg-white hover:border-neutral-400 dark:border-neutral-800 dark:bg-neutral-900'
            }`}
          >
            <div className="text-sm font-semibold">{m.title}</div>
            <div className={`mt-1 text-xs ${active ? 'opacity-90' : 'text-neutral-500'}`}>
              {m.body}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function RecommendedFlow({ templates }: { templates: CapabilitySpec[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function create(templateId: string) {
    setBusy(templateId);
    setError(null);
    try {
      const res = await fetch('/api/capabilities/from-template', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ template_id: templateId }),
      });
      if (!res.ok) throw new Error(await res.text());
      const created = (await res.json()) as CapabilitySpec;
      router.push(`/capabilities/${encodeURIComponent(created.id)}`);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (templates.length === 0) {
    return (
      <div className="text-sm text-neutral-500">
        No templates available. Check that the gateway is running.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      )}
      <ul className="grid grid-cols-1 gap-3">
        {templates.map((t) => (
          <li
            key={t.id}
            className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900"
          >
            <div>
              <div className="font-mono text-xs text-neutral-500">{t.id}</div>
              <div className="font-medium">{t.name}</div>
              <p className="mt-1 line-clamp-2 text-sm text-neutral-600 dark:text-neutral-400">
                {t.description}
              </p>
            </div>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => create(t.id)}
              className="rounded-md bg-neutral-900 px-3 py-2 text-xs font-medium text-white hover:bg-neutral-700 disabled:opacity-50 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
            >
              {busy === t.id ? 'Creating...' : 'Use template'}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DescribeFlow() {
  const router = useRouter();
  const [description, setDescription] = useState('');
  const [phase, setPhase] = useState<'idle' | 'asking' | 'answering' | 'compiling' | 'reviewing'>(
    'idle',
  );
  const [questions, setQuestions] = useState<InterviewQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [spec, setSpec] = useState<CapabilitySpec | null>(null);
  const [provider, setProvider] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  async function askQuestions() {
    if (!description.trim()) return;
    setPhase('asking');
    setError(null);
    try {
      const res = await fetch('/api/capabilities/compiler/questions', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ description }),
      });
      if (!res.ok) throw new Error(await res.text());
      const body = (await res.json()) as { questions: InterviewQuestion[]; provider: string };
      setQuestions(body.questions);
      setProvider(body.provider);
      setPhase('answering');
    } catch (e) {
      setError((e as Error).message);
      setPhase('idle');
    }
  }

  async function compile() {
    setPhase('compiling');
    setError(null);
    try {
      const res = await fetch('/api/capabilities/compiler/compile', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ description, answers }),
      });
      if (!res.ok) throw new Error(await res.text());
      const body = (await res.json()) as { spec: CapabilitySpec };
      setSpec(body.spec);
      setPhase('reviewing');
    } catch (e) {
      setError((e as Error).message);
      setPhase('answering');
    }
  }

  async function persist() {
    if (!spec) return;
    try {
      const res = await fetch('/api/capabilities', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(spec),
      });
      if (!res.ok) throw new Error(await res.text());
      const created = (await res.json()) as CapabilitySpec;
      router.push(`/capabilities/${encodeURIComponent(created.id)}`);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <label className="text-sm font-medium" htmlFor="description">
          Describe the capability
        </label>
        <textarea
          id="description"
          className="min-h-[140px] rounded-md border border-neutral-300 bg-white p-3 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
          placeholder="e.g. The model should always cite the exact page number when answering questions about a PDF"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="flex justify-end">
          <button
            type="button"
            disabled={phase !== 'idle' || !description.trim()}
            onClick={askQuestions}
            className="rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          >
            {phase === 'asking' ? 'Asking...' : 'Ask clarifying questions'}
          </button>
        </div>
      </div>

      {provider && (
        <p className="text-xs text-neutral-500">
          Using compiler backend: <span className="font-mono">{provider}</span>
        </p>
      )}

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      )}

      {phase === 'answering' && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">Answer a few quick questions</h3>
          <ul className="flex flex-col gap-3">
            {questions.map((q) => (
              <li key={q.id} className="flex flex-col gap-1">
                <label className="text-sm text-neutral-700 dark:text-neutral-300" htmlFor={q.id}>
                  {q.question}
                </label>
                <input
                  id={q.id}
                  className="rounded-md border border-neutral-300 bg-white p-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
                  value={answers[q.id] ?? ''}
                  onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
                />
              </li>
            ))}
          </ul>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={compile}
              className="rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
            >
              Compile spec
            </button>
          </div>
        </div>
      )}

      {phase === 'reviewing' && spec && (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">Review the compiled spec</h3>
          <pre className="max-h-96 overflow-auto rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs dark:border-neutral-800 dark:bg-neutral-900">
            {JSON.stringify(spec, null, 2)}
          </pre>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setPhase('answering')}
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700"
            >
              Back
            </button>
            <button
              type="button"
              onClick={persist}
              className="rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
            >
              Save capability
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
