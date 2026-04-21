import Link from 'next/link';

import { gateway } from '@/lib/gateway';

interface PageProps {
  searchParams?: {
    project_id?: string;
    capability_id?: string;
    status?: string;
    provider?: string;
  };
}

export default async function TracesPage({ searchParams }: PageProps) {
  const filters = {
    project_id: searchParams?.project_id,
    capability_id: searchParams?.capability_id,
    status: searchParams?.status,
    provider: searchParams?.provider,
    limit: 100,
  };
  const [result, capabilities] = await Promise.all([
    gateway.traces(filters).catch(() => ({ traces: [], total: 0, limit: 100, offset: 0 })),
    gateway.listCapabilities().catch(() => []),
  ]);

  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="mx-auto flex max-w-6xl flex-col gap-8 px-6 py-10">
        <header className="flex flex-col gap-4 rounded-[2rem] border border-neutral-800 bg-neutral-900 p-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.24em] text-neutral-500">
                Trace Explorer
              </div>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight">
                Filter persisted traces by project, capability, provider, and status.
              </h1>
            </div>
            <Link href="/" className="text-sm text-neutral-400 hover:text-white">
              Back to workspace
            </Link>
          </div>

          <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Input name="project_id" placeholder="Project id" defaultValue={filters.project_id} />
            <select
              name="capability_id"
              defaultValue={filters.capability_id ?? ''}
              className="rounded-2xl border border-neutral-800 bg-neutral-950 px-4 py-3 text-sm outline-none"
            >
              <option value="">All capabilities</option>
              {capabilities.map((cap) => (
                <option key={cap.id} value={cap.id}>
                  {cap.name}
                </option>
              ))}
            </select>
            <Input name="provider" placeholder="Provider" defaultValue={filters.provider} />
            <select
              name="status"
              defaultValue={filters.status ?? ''}
              className="rounded-2xl border border-neutral-800 bg-neutral-950 px-4 py-3 text-sm outline-none"
            >
              <option value="">Any status</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
            </select>
            <button
              type="submit"
              className="rounded-full bg-emerald-400 px-5 py-3 text-sm font-medium text-neutral-950 transition hover:bg-emerald-300 sm:col-span-2 lg:col-span-4"
            >
              Apply filters
            </button>
          </form>
        </header>

        <section className="rounded-[2rem] border border-neutral-800 bg-neutral-900 p-6">
          <div className="flex items-center justify-between">
            <div className="text-sm text-neutral-400">{result.total} matching traces</div>
            <div className="text-xs uppercase tracking-[0.2em] text-neutral-600">
              Stored in gateway trace history
            </div>
          </div>

          {result.traces.length === 0 ? (
            <div className="mt-6 rounded-[1.5rem] border border-dashed border-neutral-800 p-8 text-sm text-neutral-500">
              No traces matched the current filters.
            </div>
          ) : (
            <div className="mt-6 overflow-hidden rounded-[1.5rem] border border-neutral-800">
              <table className="min-w-full divide-y divide-neutral-800 text-left text-sm">
                <thead className="bg-neutral-950/70 text-xs uppercase tracking-[0.18em] text-neutral-500">
                  <tr>
                    <th className="px-4 py-3">Trace</th>
                    <th className="px-4 py-3">Provider</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Tokens</th>
                    <th className="px-4 py-3">Tags</th>
                    <th className="px-4 py-3">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-800">
                  {result.traces.map((trace) => (
                    <tr key={trace.trace_id} className="align-top">
                      <td className="px-4 py-4">
                        <div className="font-mono text-xs text-neutral-300">{trace.trace_id}</div>
                        <div className="mt-1 text-xs text-neutral-500">{trace.project_id}</div>
                        <div className="mt-2 max-w-xl text-neutral-400">
                          {promptPreview(trace.request, trace.method)}
                        </div>
                      </td>
                      <td className="px-4 py-4">
                        <div>{trace.provider}</div>
                        <div className="mt-1 text-xs text-neutral-500">{trace.model}</div>
                      </td>
                      <td className="px-4 py-4">
                        <span
                          className={`rounded-full px-3 py-1 text-xs font-medium ${
                            trace.status === 'ok'
                              ? 'bg-emerald-400/15 text-emerald-300'
                              : 'bg-red-400/15 text-red-300'
                          }`}
                        >
                          {trace.status}
                        </span>
                        {trace.error ? (
                          <div className="mt-2 max-w-xs text-xs text-red-300">{trace.error}</div>
                        ) : null}
                      </td>
                      <td className="px-4 py-4 text-neutral-400">{trace.total_tokens}</td>
                      <td className="px-4 py-4">
                        <div className="flex flex-wrap gap-2">
                          {Object.entries(trace.tags ?? {}).length === 0 ? (
                            <span className="text-neutral-600">No tags</span>
                          ) : (
                            Object.entries(trace.tags).map(([key, value]) => (
                              <span
                                key={`${key}-${value}`}
                                className="rounded-full border border-neutral-800 px-2 py-1 text-xs text-neutral-400"
                              >
                                {key}={value}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
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
    </main>
  );
}

function Input(props: { name: string; placeholder: string; defaultValue?: string }) {
  return (
    <input
      name={props.name}
      placeholder={props.placeholder}
      defaultValue={props.defaultValue}
      className="rounded-2xl border border-neutral-800 bg-neutral-950 px-4 py-3 text-sm outline-none placeholder:text-neutral-600"
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
