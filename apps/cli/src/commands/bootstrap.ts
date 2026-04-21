import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

import pc from 'picocolors';

const execFileAsync = promisify(execFile);

export interface BootstrapLocalModelsOptions {
  composeProject?: string;
}

async function ensureOllamaService(): Promise<void> {
  await execFileAsync('docker', ['compose', 'up', '-d', 'ollama']);
}

async function pullModel(model: string): Promise<void> {
  await execFileAsync('docker', ['exec', 'flychain-ollama', 'ollama', 'pull', model]);
}

export async function runBootstrapLocalModels(
  _opts: BootstrapLocalModelsOptions = {},
): Promise<void> {
  const models = ['llama3.2:3b', 'nomic-embed-text'];

  console.log(pc.cyan('Bootstrapping local Ollama models for FlyChain...'));
  await ensureOllamaService();
  for (const model of models) {
    console.log(pc.dim(`pulling ${model}`));
    await pullModel(model);
  }
  console.log(pc.green('✓ local FlyChain models are ready'));
}
