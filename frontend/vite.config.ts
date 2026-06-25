import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

function parseAllowedHosts(value: string | undefined): string[] | true | undefined {
  if (!value) {
    return undefined;
  }
  if (value.trim() === "*") {
    return true;
  }
  const hosts = value
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  return hosts.length > 0 ? hosts : undefined;
}

export default defineConfig(({ mode }) => {
  const root = new URL(".", import.meta.url).pathname;
  const env = loadEnv(mode, root, "");
  const allowedHosts = parseAllowedHosts(
    env.KAJAS_VITE_ALLOWED_HOSTS ?? env.VITE_ALLOWED_HOSTS,
  );

  return {
    plugins: [react()],
    server: {
      host: env.KAJAS_VITE_HOST || "0.0.0.0",
      ...(allowedHosts ? { allowedHosts } : {}),
      port: Number(env.KAJAS_VITE_PORT || 5173),
      proxy: {
        "/api": {
          target: env.KAJAS_API_PROXY_TARGET || "http://127.0.0.1:8765",
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
    },
  };
});
