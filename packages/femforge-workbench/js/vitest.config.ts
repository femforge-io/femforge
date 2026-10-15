import { defineConfig } from "vitest/config";

// The build config is not reused: its plugins write the package dist, which a test run has no business touching.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
