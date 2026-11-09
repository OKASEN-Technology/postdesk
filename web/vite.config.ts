import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built into the Python package, which serves it at `/`. In development
// `vite` proxies the API to a running `postdesk serve`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/postdesk/static", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8080", "/media": "http://localhost:8080" },
  },
});
