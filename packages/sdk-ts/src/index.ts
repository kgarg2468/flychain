export const VERSION = '0.0.0';

export interface FlyChainConfig {
  gatewayUrl: string;
  apiKey?: string;
  projectId?: string;
}

export function gatewayBaseUrl(defaultUrl = 'http://localhost:8080'): string {
  const env = globalThis.process?.env?.FLYCHAIN_GATEWAY_URL;
  return env ?? defaultUrl;
}

export function resolveConfig(overrides: Partial<FlyChainConfig> = {}): FlyChainConfig {
  return {
    gatewayUrl: overrides.gatewayUrl ?? gatewayBaseUrl(),
    apiKey: overrides.apiKey ?? globalThis.process?.env?.FLYCHAIN_API_KEY,
    projectId: overrides.projectId ?? globalThis.process?.env?.FLYCHAIN_PROJECT_ID,
  };
}
