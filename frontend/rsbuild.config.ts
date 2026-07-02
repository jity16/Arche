import { defineConfig } from '@rsbuild/core';
import { pluginReact } from '@rsbuild/plugin-react';

export default defineConfig({
  plugins: [pluginReact()],
  html: { template: './index.html' },
  source: { entry: { index: './src/main.tsx' } },
  // standalone 单进程：server.py 在根路径 / 托管 dist，assetPrefix 用 '/'（OS 网关形态下才是 '/apps/arche/'）。
  output: { distPath: { root: 'dist' }, assetPrefix: '/' },
  server: {
    // 5173 是 Vite 默认端口，本机常被其它 dev server 占用；standalone dev 用 5210 规避冲突。
    port: 5210,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8501', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8501', changeOrigin: true },
    },
  },
});
