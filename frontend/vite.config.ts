import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  envDir: "..",
  base: "/static/workspace/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../src/ochask/web/static/workspace",
    emptyOutDir: true,
  },
  server: { host: "127.0.0.1" },
  resolve: {
    alias: { "@": new URL("./src", import.meta.url).pathname },
  },
});
