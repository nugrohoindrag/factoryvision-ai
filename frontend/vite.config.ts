/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the local backend (uvicorn on :8080). 127.0.0.1, not localhost: on Windows
// localhost resolves to ::1 first and reaches whatever else listens there. FV_API_URL moves it if 8080 is taken.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": process.env.FV_API_URL ?? "http://127.0.0.1:8080" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
