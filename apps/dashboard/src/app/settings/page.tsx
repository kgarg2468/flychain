import Link from 'next/link';

import { SettingsClient } from './client';
import { gateway } from '@/lib/gateway';

export default async function SettingsPage() {
  const settings = await gateway.getSettings().catch(() => ({
    settings: {
      judge_model: 'llama3.2:3b',
      embedding_model: 'nomic-embed-text',
      min_cluster_size: 3,
      auto_eval_new_traces: false,
      auto_cluster_failures: false,
    },
    openai_configured: false,
    anthropic_configured: false,
  }));

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,_#f7faf9_0%,_#edf2f7_100%)] text-neutral-900">
      <div className="mx-auto flex max-w-5xl flex-col gap-8 px-6 py-10">
        <header className="flex items-center justify-between gap-4 rounded-[2rem] border border-white/80 bg-white/85 p-8 shadow-[0_20px_70px_-50px_rgba(15,23,42,0.55)]">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-neutral-400">
              Settings
            </div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight">
              Env-first runtime controls for the local FlyChain stack.
            </h1>
          </div>
          <nav className="flex flex-wrap gap-3 text-sm text-neutral-500">
            <Link href="/chat" className="hover:text-neutral-900">
              Chat
            </Link>
            <Link href="/" className="hover:text-neutral-900">
              Back to workspace
            </Link>
          </nav>
        </header>

        <SettingsClient initial={settings} />
      </div>
    </main>
  );
}
