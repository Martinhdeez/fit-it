import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The core serves `next build` output (frontend/out) at /fitit/ui/ (docs/adr/0015).
  output: "export",
  basePath: "/fitit/ui",
  // The repo already has AGENTS.md. Do not generate a second one under frontend/.
  agentRules: false,
};

export default nextConfig;
