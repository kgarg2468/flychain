import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'FlyChain',
  description: 'Open-source flywheel for making models better at specific capabilities',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-neutral-50 text-neutral-900 antialiased dark:bg-neutral-950 dark:text-neutral-100">
        {children}
      </body>
    </html>
  );
}
