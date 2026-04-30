import { redirect } from 'next/navigation';

interface Params {
  params: { id: string };
}

export default function CapabilityPage({ params }: Params) {
  redirect(`/?tab=capabilities&capability=${encodeURIComponent(params.id)}`);
}
