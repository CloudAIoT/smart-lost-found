import { test as base, expect } from '@playwright/test';

export const storageKey = 'smart-lost-found.kiosk_id.v1';
export const kioskA = '00000000-0000-0000-0000-000000000001';
export const kioskB = '00000000-0000-0000-0000-000000000002';
export const itemId = '44444444-4444-4444-8444-444444444444';
export const regId = '55555555-5555-4555-8555-555555555555';
export const home = (page) => page.getByRole('button', { name: '처음으로', exact: true }).last();
export const completion = (page) => page.getByRole('button', { name: '수령 완료', exact: true });
export const posts = (model) => model.requests.filter((request) => request.method === 'POST');

export const test = base.extend({
  model: async ({ page, context }, use) => {
    const model = { requests: [], unexpected: [], errors: [], kiosksError: false, openError: false,
      completeErrors: [], captureError: false, alerts: [], devices: [],
      kiosks: [{ id: kioskA, name: '테스트 키오스크 A', location_description: 'A 장소' },
        { id: kioskB, name: '테스트 키오스크 B', location_description: 'B 장소' }] };
    page.on('pageerror', (error) => model.errors.push(error.message));
    await context.route('https://**', (route) => route.abort());
    await context.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const query = Object.fromEntries(url.searchParams);
      const body = request.postDataJSON();
      model.requests.push({ path, query, method: request.method(), body });
      const reply = (json, status = 200) => route.fulfill({ json, status });
      const error = (code, status = 409) => reply({ detail: { code } }, status);
      if (path === '/api/kiosks') return model.kiosksError ? error('UNAVAILABLE', 503)
        : reply(model.kiosks.slice(Number(query.offset), Number(query.offset) + Number(query.limit)));
      if (path === '/api/categories') return reply([]);
      if (path.endsWith('/device-status')) return model.abortDevices ? route.abort() : reply(model.devices);
      if (path.endsWith('/operation-status')) {
        const kioskId = path.split('/')[3];
        if (model.operationError) return error('UNAVAILABLE', 503);
        const alarms = model.alerts.filter((a) => a.kiosk_id === kioskId && a.severity === 'alarm' && !a.resolved_at);
        return reply({ kiosk_id: kioskId, blocked: alarms.length > 0, active_alarm_count: alarms.length, alarms: alarms.slice(0, 200) });
      }
      if (path.startsWith('/api/admin/')) {
        if (request.headers().authorization !== `Basic ${Buffer.from('operator:test-password').toString('base64')}`) return error('ADMIN_UNAUTHORIZED', 401);
        if (path === '/api/admin/session') return reply({ username: 'operator' });
        if (path === '/api/admin/alerts') {
          const list = model.alerts.filter((a) => a.kiosk_id === query.kiosk_id && (query.unresolved_only !== 'true' || !a.resolved_at));
          return reply(list.slice(Number(query.offset), Number(query.offset) + Number(query.limit)));
        }
        if (path.endsWith('/resolve')) {
          if (model.resolveError) return error('UNAVAILABLE', 503);
          const alert = model.alerts.find((a) => a.id === path.split('/')[4]);
          if (!alert) return error('ALERT_NOT_FOUND', 404);
          Object.assign(alert, { resolved_at: '2026-11-25T02:00:00Z', resolved_by: 'operator', resolution_note: body.note });
          return reply(alert);
        }
      }
      if (path === '/api/alerts') {
        const alerts = model.alerts.filter((a) => a.kiosk_id === query.kiosk_id && (!query.since || Date.parse(a.reported_at) >= Date.parse(query.since)))
          .sort((a, b) => Date.parse(b.reported_at) - Date.parse(a.reported_at) || a.id.localeCompare(b.id));
        return reply(alerts.slice(Number(query.offset || 0), Number(query.offset || 0) + Number(query.limit || 50)));
      }
      const pickup = { found_item_id: itemId, kiosk_id: model.workKiosk, locker_number: 2, assignment_status: 'awaiting_pickup', item_status: 'stored' };
      if (request.method() === 'POST' && model.alerts.some((a) => a.kiosk_id === (body?.kiosk_id || model.workKiosk) && a.severity === 'alarm' && !a.resolved_at)) {
        return error('KIOSK_ALARM_ACTIVE', 423);
      }
      if (path === '/api/pickup/open') {
        model.workKiosk = body.kiosk_id;
        if (model.openGate) await model.openGate;
        return model.openError ? error('DEVICE_UNREACHABLE', 503) : reply({ ...pickup, kiosk_id: model.workKiosk });
      }
      if (path === `/api/pickup/${itemId}/complete`) {
        if (model.completeGate) await model.completeGate;
        const code = model.completeErrors.shift();
        return code ? error(code) : reply({ ...pickup, assignment_status: 'retrieved', item_status: 'retrieved' });
      }
      if (path === `/api/pickup/${itemId}/reopen`) return reply(pickup);
      if (path === '/api/registrations') {
        model.workKiosk = body.kiosk_id;
        return reply({ id: regId, kiosk_id: model.workKiosk, status: 'draft' }, 201);
      }
      if (path.match(new RegExp(`/api/registrations/${regId}/shots/[12]$`))) return model.captureError
        ? error('CAMERA_ERROR', 500) : reply({ id: regId, kiosk_id: model.workKiosk, status: 'capturing' });
      if (path.endsWith('/preview')) return reply({ url: '' });
      if (path === `/api/registrations/${regId}`) return reply({ id: regId, kiosk_id: model.workKiosk, status: 'ready', analysis_status: 'succeeded' });
      if (path === `/api/registrations/${regId}/deposit/open`) return reply({ id: regId, kiosk_id: model.workKiosk, status: 'awaiting_deposit', locker_number: 2 });
      if (path === `/api/registrations/${regId}/deposit/complete`) return reply({ id: regId, kiosk_id: model.workKiosk, status: 'stored', locker_number: 2 });
      model.unexpected.push(path);
      return error('UNEXPECTED_TEST_REQUEST', 500);
    });
    await use(model);
    expect(model.unexpected).toEqual([]);
    expect(model.errors).toEqual([]);
  },
});

export async function configure(page, id = kioskA) {
  await page.getByLabel('사용할 키오스크 선택').selectOption(id);
  await page.getByRole('button', { name: '이 키오스크로 설정' }).click();
  await expect(page.getByLabel('사용할 키오스크 선택')).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(0);
}

export async function enterKiosk(page, id = kioskA) {
  await page.goto('/kiosk/settings');
  await configure(page, id);
  await page.getByRole('link', { name: '키오스크 화면으로 돌아가기' }).click();
  await expect(page.getByRole('button', { name: /분실물 수령 인증/ })).toBeEnabled();
}

export async function openPickup(page, code = 'ABCD2345') {
  await page.getByRole('button', { name: /분실물 수령 인증/ }).click();
  await page.getByPlaceholder('OTP 8자리 입력').fill(code);
  await page.getByRole('button', { name: '인증 승인', exact: true }).click();
  await expect(page.getByRole('heading', { name: /번 보관함이 열렸습니다/ })).toBeVisible();
}

export { expect };

export async function loginAdmin(page) {
  await page.getByLabel('관리자 계정', { exact: true }).fill('operator');
  await page.getByLabel('비밀번호', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: '관리자 로그인', exact: true }).click();
  await expect(page.getByRole('heading', { name: '경고 검토 및 조치' })).toBeVisible();
}
