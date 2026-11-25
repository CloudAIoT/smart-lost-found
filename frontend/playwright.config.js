import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  workers: 2,
  use: { baseURL: 'http://127.0.0.1:5189', viewport: { width: 1024, height: 900 }, trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5189 --strictPort',
    url: 'http://127.0.0.1:5189',
    reuseExistingServer: false,
    // 실제 EC2 호출을 막는다. 각 테스트에서 모의 API 응답을 제공한다.
    env: { SLF_API_TARGET: 'http://127.0.0.1:9' },
  },
});
