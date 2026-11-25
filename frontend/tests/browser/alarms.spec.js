import { test, expect, enterKiosk, openPickup, completion, posts, kioskA, kioskB, storageKey, loginAdmin } from './fixture.js';

const alarm = (id, kiosk = kioskA) => ({ id, kiosk_id: kiosk, code: 'TAMPER', severity: 'alarm',
  locker_number: 2, message: '장치 점검이 필요합니다', reported_at: '2000-01-01T00:00:00Z' });
const dialog = (page) => page.getByRole('dialog', { name: '관리자의 확인이 필요합니다!' });

async function resolveFirst(admin) {
  const first = admin.locator('article').first();
  await first.getByLabel('조치 내용').fill('장치와 물품을 점검하고 문제 해결을 확인함');
  await first.getByRole('checkbox').check();
  await first.getByRole('button', { name: '조치 완료 및 운영 재개' }).click();
  await expect(admin.getByRole('status')).toContainText('조치 완료를 기록했습니다');
}

test('an old alarm blocks direct entry, reload, settings, Escape and background actions', async ({ page, model }) => {
  model.alerts = [alarm('old')];
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, id), { key: storageKey, id: kioskA });
  await page.goto('/kiosk');
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).getByText(/2번 보관함/)).toBeVisible();
  await expect(dialog(page).getByRole('button')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.mouse.click(1, 1);
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(dialog(page)).toBeVisible();
  expect(posts(model)).toHaveLength(0);
  page.once('dialog', (d) => d.accept());
  await page.reload();
  await expect(dialog(page)).toBeVisible();
  page.once('dialog', (d) => d.accept());
  await page.goto('/kiosk/settings');
  await expect(dialog(page)).toBeVisible();
  await expect(page.getByRole('button', { name: '키오스크 변경' })).toHaveCount(0);
  expect(model.requests.filter((r) => r.path.endsWith('/operation-status')).every((r) => !r.query.since)).toBe(true);
});

test('admin resolution in another tab releases only after all alarms, preserves pickup, and new alarms relock', async ({ page, context, model }) => {
  await page.clock.install();
  await enterKiosk(page);
  await openPickup(page);
  const before = posts(model).length;
  model.alerts = [alarm('first'), alarm('second')];
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  const admin = await context.newPage();
  await admin.goto('/admin/devices');
  await loginAdmin(admin);
  await expect(admin.locator('article')).toHaveCount(2);
  await resolveFirst(admin);
  await expect(admin.locator('article')).toHaveCount(1);
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  await resolveFirst(admin);
  await page.clock.runFor(2001);
  await expect(dialog(page)).toHaveCount(0);
  await expect(completion(page)).toBeEnabled();
  expect(posts(model).filter((r) => !r.path.startsWith('/api/admin/'))).toHaveLength(before);
  model.alerts.push(alarm('new-id'));
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  await admin.getByRole('button', { name: '경고 새로고침' }).click();
  await resolveFirst(admin);
  await page.clock.runFor(2001);
  await completion(page).click();
  await expect(page.getByRole('heading', { name: '물건을 찾아가셨습니다' })).toBeVisible();
});

test('warning can be dismissed and another kiosk alarm does not stop this kiosk', async ({ page, model }) => {
  model.alerts = [alarm('other', kioskB), { ...alarm('warning'), severity: 'warning', reported_at: '2099-01-01T00:00:00Z' }];
  await enterKiosk(page);
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await openPickup(page, 'DEV00000');
  await completion(page).click();
  await expect(page.getByRole('heading', { name: '물건을 찾아가셨습니다' })).toBeVisible();
  expect(posts(model)).toHaveLength(0);
});

test('failed admin resolution and failed status polling keep the kiosk blocked', async ({ page, context, model }) => {
  await page.clock.install();
  await enterKiosk(page);
  model.alerts = [alarm('failure')];
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  const admin = await context.newPage();
  await admin.goto('/admin/devices');
  await loginAdmin(admin);
  model.resolveError = true;
  const card = admin.locator('article').first();
  await card.getByLabel('조치 내용').fill('점검 완료');
  await card.getByRole('checkbox').check();
  await card.getByRole('button', { name: '조치 완료 및 운영 재개' }).click();
  await expect(admin.getByRole('alert')).toBeVisible();
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  model.resolveError = false;
  await resolveFirst(admin);
  model.operationError = true;
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  model.operationError = false;
  await page.clock.runFor(2001);
  await expect(dialog(page)).toHaveCount(0);
});

test('a server alarm refusal before the next poll shows the modal without losing the current step', async ({ page, model }) => {
  await page.clock.install();
  await enterKiosk(page);
  await openPickup(page);
  model.alerts = [alarm('just-arrived')];
  await completion(page).click();
  await expect(dialog(page)).toBeVisible();
  model.alerts[0].resolved_at = '2026-11-25T02:00:00Z';
  await page.clock.runFor(2001);
  await expect(completion(page)).toBeEnabled();
  await expect(page.getByRole('heading', { name: /번 보관함이 열렸습니다/ })).toBeVisible();
});

test('initial status failure blocks dev OTP and recovers only after a valid status response', async ({ page, model }) => {
  model.operationError = true;
  await page.clock.install();
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, id), { key: storageKey, id: kioskA });
  await page.goto('/kiosk');
  await expect(page.getByRole('dialog', { name: '운영 상태를 확인할 수 없습니다' })).toBeVisible();
  expect(posts(model)).toHaveLength(0);
  model.operationError = false;
  await page.clock.runFor(2001);
  await openPickup(page, 'DEV00000');
  await completion(page).click();
  await expect(page.getByRole('heading', { name: '물건을 찾아가셨습니다' })).toBeVisible();
});

test('another tab cannot switch a kiosk away from an unresolved alarm', async ({ page, context, model }) => {
  await page.clock.install();
  await enterKiosk(page);
  model.alerts = [alarm('fixed-kiosk')];
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  const other = await context.newPage();
  await other.goto('/web');
  await other.evaluate(({ key, id }) => localStorage.setItem(key, id), { key: storageKey, id: kioskB });
  await page.clock.runFor(2001);
  await expect(dialog(page)).toBeVisible();
  expect(model.requests.filter((r) => r.path.endsWith('/operation-status')).at(-1).path).toContain(kioskA);
});

test('admin-resolved warnings disappear on the kiosk and credentials are not persisted', async ({ page, context, model }) => {
  await page.clock.install();
  model.alerts = [{ ...alarm('warning'), severity: 'warning', code: 'NEW_WARNING', message: '새 경고 내용', reported_at: '2099-01-01T00:00:00Z' }];
  await enterKiosk(page);
  await expect(page.getByText('새 경고 내용', { exact: false })).toBeVisible();
  const admin = await context.newPage();
  await admin.goto('/admin/devices');
  await loginAdmin(admin);
  const first = admin.locator('article').first();
  await first.getByLabel('조치 내용').fill('확인 및 조치 완료');
  await first.getByRole('checkbox').check();
  await first.getByRole('button', { name: '검토 및 조치 완료' }).click();
  await expect(admin.getByRole('status')).toBeVisible();
  await page.clock.runFor(8001);
  await expect(page.getByText('새 경고 내용', { exact: false })).toHaveCount(0);
  expect(await admin.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('test-password');
  await admin.reload();
  await expect(admin.getByRole('button', { name: '관리자 로그인', exact: true })).toBeVisible();
});

test('admin pagination and kiosk selection keep the device table on the reviewed kiosk', async ({ page, model }) => {
  model.alerts = Array.from({ length: 51 }, (_, i) => alarm(`page-${i}`));
  model.alerts.push(alarm('b-only', kioskB));
  await page.goto('/admin/devices');
  await loginAdmin(page);
  await expect(page.locator('article')).toHaveCount(50);
  await page.getByRole('button', { name: '다음 경고' }).click();
  await expect(page.locator('article')).toHaveCount(1);
  await page.getByLabel('조치 대상 키오스크').selectOption(kioskB);
  await expect(page.locator('article')).toHaveCount(1);
  await expect(page.getByLabel('조회할 키오스크')).toHaveValue(kioskB);
  await expect.poll(() => model.requests.filter((r) => r.path.endsWith('/device-status')).at(-1)?.path).toContain(kioskB);
});
