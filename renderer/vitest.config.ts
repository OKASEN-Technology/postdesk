import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: { jsx: "transform", jsxFactory: "h", jsxFragment: "Fragment" },
  test: { include: ["test/**/*.test.ts"], testTimeout: 60_000 },
});
