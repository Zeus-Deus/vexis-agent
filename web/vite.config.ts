import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tailscale Serve mounts the dashboard at root (--set-path=/), and
// FastAPI serves at root, so Vite's default base ("/") works for both
// localhost-direct and tailscale-fronted access. Keeping the base at
// root means the same URL structure ("/", "/api/v1/...", "/assets/...")
// is correct in dev, in localhost, and behind Tailscale Serve — no
// prefix juggling.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      // Forward /api/v1/* to the live daemon during dev so we can run
      // the dashboard with hot-reload while the daemon is running on
      // its real port.
      "/api": {
        target: "http://localhost:8766",
        changeOrigin: false,
      },
    },
  },
});
