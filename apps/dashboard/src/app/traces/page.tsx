import { redirect } from 'next/navigation';

interface PageProps {
  searchParams?: Record<string, string | string[] | undefined>;
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default function TracesPage({ searchParams }: PageProps) {
  const params = new URLSearchParams({ tab: 'traces' });
  for (const key of ['project_id', 'capability_id', 'status', 'provider']) {
    const value = first(searchParams?.[key]);
    if (value) params.set(key, value);
  }
  redirect(`/?${params.toString()}`);
}
