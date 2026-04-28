'use client';

import Link from 'next/link';
import { FormEvent, useMemo, useState } from 'react';

import {
  gateway,
  type CapabilitySpec,
  type ChatCompletionResponse,
  type ChatCompletionUsage,
  type ChatMessage,
} from '@/lib/gateway';

const DEFAULT_MODEL = 'local-ollama:llama3.2:3b';
const DEFAULT_PROJECT = 'default';
const DEFAULT_TAGS = 'source=dashboard-chat';

interface TranscriptMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  traceId?: string | null;
  usage?: ChatCompletionUsage;
  latencyMs?: number;
  projectId?: string;
}

export function ChatClient({
  capabilities,
  loadError,
}: {
  capabilities: CapabilitySpec[];
  loadError: string | null;
}) {
  const [model, setModel] = useState(DEFAULT_MODEL);
  const [projectId, setProjectId] = useState(DEFAULT_PROJECT);
  const [selectedCapability, setSelectedCapability] = useState(capabilities[0]?.id ?? '');
  const [tags, setTags] = useState(DEFAULT_TAGS);
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedCapabilityIds = useMemo(
    () => (selectedCapability ? [selectedCapability] : []),
    [selectedCapability],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || isSending) return;

    const normalizedProject = projectId.trim() || DEFAULT_PROJECT;
    const history: ChatMessage[] = messages.map((message) => ({
      role: message.role,
      content: message.content,
    }));
    const nextMessages: ChatMessage[] = [...history, { role: 'user', content }];
    const userMessage: TranscriptMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content,
    };

    setMessages((current) => [...current, userMessage]);
    setDraft('');
    setError(null);
    setIsSending(true);

    try {
      const result = await gateway.chatCompletion({
        model: model.trim() || DEFAULT_MODEL,
        messages: nextMessages,
        projectId: normalizedProject,
        capabilityIds: selectedCapabilityIds,
        tags,
      });
      const responseContent = completionContent(result.response);
      setMessages((current) => [
        ...current,
        {
          id: `model-${Date.now()}`,
          role: 'assistant',
          content: responseContent || '(empty response)',
          traceId: result.traceId ?? result.response.id ?? null,
          usage: result.response.usage,
          latencyMs:
            typeof result.response.latency_ms === 'number' ? result.response.latency_ms : undefined,
          projectId: normalizedProject,
        },
      ]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIsSending(false);
    }
  }

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,_#f8fafc_0%,_#edf2f7_100%)] text-neutral-900">
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-6 py-10">
        <header className="flex flex-col gap-4 rounded-[2rem] border border-white/80 bg-white/85 p-6 shadow-[0_20px_70px_-50px_rgba(15,23,42,0.55)] sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase text-neutral-400">Chat</div>
            <h1 className="mt-2 text-3xl font-semibold">Send real traffic through FlyChain.</h1>
          </div>
          <nav className="flex flex-wrap gap-3 text-sm text-neutral-500">
            <Link href="/" className="hover:text-neutral-900">
              Workspace
            </Link>
            <Link href="/traces" className="hover:text-neutral-900">
              Trace explorer
            </Link>
            <Link href="/settings" className="hover:text-neutral-900">
              Settings
            </Link>
          </nav>
        </header>

        {loadError ? (
          <div className="rounded-[1.25rem] border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <div className="font-medium">Gateway not reachable</div>
            <div className="mt-1">{loadError}</div>
          </div>
        ) : null}

        {capabilities.length === 0 ? (
          <div className="rounded-[1.25rem] border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
            Eval and failure workflows need a capability. You can still chat without one, or{' '}
            <Link href="/capabilities/new" className="font-medium underline">
              create one
            </Link>
            .
          </div>
        ) : null}

        <section className="grid flex-1 gap-6 lg:grid-cols-[340px_1fr]">
          <form
            onSubmit={submit}
            className="flex flex-col gap-4 rounded-[1.5rem] border border-neutral-200 bg-white p-5 shadow-[0_18px_50px_-42px_rgba(15,23,42,0.5)]"
          >
            <Field label="Model" htmlFor="model">
              <input
                id="model"
                value={model}
                onChange={(event) => setModel(event.target.value)}
                className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none focus:border-neutral-500"
              />
            </Field>
            <Field label="Project" htmlFor="project">
              <input
                id="project"
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
                className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none focus:border-neutral-500"
              />
            </Field>
            <Field label="Capability" htmlFor="capability">
              <select
                id="capability"
                value={selectedCapability}
                disabled={capabilities.length === 0}
                onChange={(event) => setSelectedCapability(event.target.value)}
                className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none focus:border-neutral-500 disabled:text-neutral-400"
              >
                {capabilities.length === 0 ? (
                  <option value="">No capability</option>
                ) : (
                  capabilities.map((capability) => (
                    <option key={capability.id} value={capability.id}>
                      {capability.name}
                    </option>
                  ))
                )}
              </select>
            </Field>
            <Field label="Tags" htmlFor="tags">
              <input
                id="tags"
                value={tags}
                onChange={(event) => setTags(event.target.value)}
                className="w-full rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm outline-none focus:border-neutral-500"
              />
            </Field>
            <Field label="Message" htmlFor="message">
              <textarea
                id="message"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                rows={7}
                className="w-full resize-none rounded-2xl border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm leading-6 outline-none focus:border-neutral-500"
              />
            </Field>
            <button
              type="submit"
              disabled={isSending || !draft.trim()}
              className="rounded-full bg-neutral-950 px-5 py-3 text-sm font-medium text-white transition hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSending ? 'Sending...' : 'Send'}
            </button>
            {error ? (
              <div
                role="alert"
                className="rounded-2xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
              >
                {error}
              </div>
            ) : null}
          </form>

          <div className="flex min-h-[520px] flex-col rounded-[1.5rem] border border-neutral-200 bg-white shadow-[0_18px_50px_-42px_rgba(15,23,42,0.5)]">
            <div className="border-b border-neutral-200 px-5 py-4 text-sm font-medium text-neutral-700">
              Transcript
            </div>
            <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
              {messages.length === 0 ? (
                <div className="flex min-h-[360px] items-center justify-center rounded-[1.25rem] border border-dashed border-neutral-300 text-sm text-neutral-500">
                  No messages yet.
                </div>
              ) : (
                messages.map((message) => <MessageBubble key={message.id} message={message} />)
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-2" htmlFor={htmlFor}>
      <span className="text-sm font-medium text-neutral-700">{label}</span>
      {children}
    </label>
  );
}

function MessageBubble({ message }: { message: TranscriptMessage }) {
  const isUser = message.role === 'user';
  const tokenText =
    typeof message.usage?.total_tokens === 'number' ? `${message.usage.total_tokens} tokens` : null;
  const latencyText =
    typeof message.latencyMs === 'number' ? `${message.latencyMs.toLocaleString()} ms` : null;

  return (
    <article
      className={`max-w-[82%] rounded-[1.25rem] border p-4 text-sm leading-6 ${
        isUser
          ? 'ml-auto border-neutral-900 bg-neutral-950 text-white'
          : 'mr-auto border-neutral-200 bg-neutral-50 text-neutral-800'
      }`}
    >
      <div
        className={
          isUser ? 'text-xs font-medium text-neutral-300' : 'text-xs font-medium text-neutral-500'
        }
      >
        {isUser ? 'You' : 'Model'}
      </div>
      <div className="mt-2 whitespace-pre-wrap">{message.content}</div>
      {!isUser && (message.traceId || tokenText || latencyText) ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-3 text-xs text-neutral-500">
          {message.traceId ? <span className="font-mono">{message.traceId}</span> : null}
          {tokenText ? <span>{tokenText}</span> : null}
          {latencyText ? <span>{latencyText}</span> : null}
          <Link
            href={`/traces?project_id=${encodeURIComponent(message.projectId ?? DEFAULT_PROJECT)}`}
            className="font-medium text-neutral-900 hover:underline"
          >
            View trace
          </Link>
        </div>
      ) : null}
    </article>
  );
}

function completionContent(response: ChatCompletionResponse): string {
  const content = response.choices[0]?.message.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        const text = part.text;
        return typeof text === 'string' ? text : '';
      })
      .filter(Boolean)
      .join('\n');
  }
  return '';
}
