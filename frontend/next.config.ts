import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  basePath: "/fitit",
  // The repo already has AGENTS.md. Do not generate a second one under frontend/.
  agentRules: false,
};

export default nextConfig;
