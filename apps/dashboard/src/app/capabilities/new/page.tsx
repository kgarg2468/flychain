import Link from 'next/link';

import { gateway, type CapabilitySpec } from '@/lib/gateway';
import { NewCapabilityClient } from './client';

export default async function NewCapabilityPage() {
  let templates: CapabilitySpec[] = [];
  let error: string | null = null;
  try {
    templates = await gateway.listTemplates();
  } catch (e) {
    templates = [];
    error = (e as Error).message;
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 px-6 py-12">
      <Link href="/" className="text-sm text-neutral-500 hover:underline">
        ← Back to workspace
      </Link>

      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">New capability</h1>
        <p className="text-neutral-600 dark:text-neutral-400">
          Describe the capability you want to improve in plain language, or start from one of the
          five shipped templates.
        </p>
      </header>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          Gateway not reachable: {error}
        </div>
      )}

      <NewCapabilityClient templates={templates} />
    </main>
  );
}
