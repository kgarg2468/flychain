import { ChatClient } from './client';
import { gateway, type CapabilitySpec } from '@/lib/gateway';

export default async function ChatPage() {
  let capabilities: CapabilitySpec[] = [];
  let loadError: string | null = null;

  try {
    capabilities = await gateway.listCapabilities();
  } catch (e) {
    loadError = (e as Error).message;
  }

  return <ChatClient capabilities={capabilities} loadError={loadError} />;
}
