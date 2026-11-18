import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

const proxy = { '/api': process.env.P7_1_API_PROXY || 'http://127.0.0.1:8017' };
export default defineConfig({
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 5177, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5177, strictPort: true, proxy },
});
