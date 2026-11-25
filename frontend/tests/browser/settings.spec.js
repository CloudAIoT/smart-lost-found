import { test, expect, configure, openPickup, completion, posts, storageKey, kioskA, kioskB } from './fixture.js';

test('restores a saved kiosk beyond the first page only after all pages load', async ({ page, model }) => {
  model.kiosks = [...Array.from({ length: 100 }, (_, i) => ({ id: `10000000-0000-0000-0000-${String(i).padStart(12, '0')}`, name: `키오스크 ${i}` })), model.kiosks[1]];
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, id), { key: storageKey, id: kioskB });
  await page.goto('/kiosk');
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  expect(model.requests.filter((r) => r.path === '/api/kiosks').some((r) => r.query.offset === '100')).toBe(true);
  expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBe(kioskB);
});

test('list failure preserves saved selection, blocks work, and supports retry', async ({ page, model }) => {
  model.kiosksError = true;
  await page.addInitScript(({ key, id }) => localStorage.setItem(key, id), { key: storageKey, id: kioskB });
  await page.goto('/kiosk');
  await expect(page.getByText(/키오스크 목록을 불러오지 못했습니다/)).toBeVisible();
  await expect(page.getByRole('button', { name: /분실물 수령 인증/ })).toBeDisabled();
  expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBe(kioskB);
  expect(model.requests.some((r) => r.path.endsWith('/device-status'))).toBe(false);
  model.kiosksError = false;
  await page.getByRole('button', { name: '다시 불러오기' }).click();
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
});

test('missing saved kiosk requires an explicit new choice; reset removes only its own key', async ({ page, model }) => {
  await page.addInitScript((key) => { localStorage.setItem(key, '99999999-9999-4999-8999-999999999999'); localStorage.setItem('unrelated', 'keep'); }, storageKey);
  await page.goto('/kiosk/settings');
  await expect(page.getByText(/저장된 키오스크를 사용할 수 없습니다/)).toBeVisible();
  expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBeNull();
  await configure(page);
  await page.getByRole('button', { name: '키오스크 변경' }).click();
  await page.getByLabel('사용할 키오스크 선택').selectOption(kioskB);
  await page.getByRole('button', { name: '변경 취소' }).click();
  expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBe(kioskA);
  await page.getByRole('button', { name: '설정 초기화' }).click();
  expect(await page.evaluate((key) => localStorage.getItem(key), storageKey)).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('unrelated'))).toBe('keep');
  expect(posts(model)).toEqual([]);
});

test('storage unavailable allows explicit memory-only selection; failed reset is not reported as success', async ({ page, model }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error('blocked'); };
    Storage.prototype.setItem = () => { throw new Error('blocked'); };
    Storage.prototype.removeItem = () => { throw new Error('blocked'); };
  });
  await page.goto('/kiosk/settings');
  await configure(page, kioskB);
  await expect(page.getByText(/이 브라우저에서는 설정을 저장할 수 없어/)).toBeVisible();
  await page.getByRole('button', { name: '설정 초기화' }).click();
  await expect(page.getByText(/설정 초기화가 완료되지 않았습니다/)).toBeVisible();
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '키오스크 화면으로 돌아가기' }).click();
  await openPickup(page);
  await completion(page).click();
  expect(posts(model)[0].body.kiosk_id).toBe(kioskB);
});

test('settings supports direct reload and kiosk changes without resetting alert history on internal navigation', async ({ page, model }) => {
  await page.clock.install();
  await page.goto('/kiosk/settings');
  await configure(page);
  await page.reload();
  await expect(page.getByRole('heading', { name: '키오스크 설정', exact: true })).toBeVisible();
  await expect(page.getByText('테스트 키오스크 A', { exact: true })).toBeVisible();
  const queries = () => model.requests.filter((r) => r.path === '/api/alerts');
  await expect.poll(() => queries().length).toBeGreaterThan(0);
  const since = queries().at(-1).query.since;
  const firstAfterReload = queries().length;
  model.alerts = [{ id: 'shared-warning', kiosk_id: kioskA, code: 'UNKNOWN', message: '설정 이동 경고', reported_at: '2099-01-01T00:00:00Z' }];
  await page.getByRole('link', { name: '키오스크 화면으로 돌아가기' }).click();
  await page.clock.runFor(8001);
  await expect(page.getByText('설정 이동 경고')).toBeVisible();
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await page.goBack();
  await expect(page).toHaveURL(/\/kiosk\/settings$/);
  await page.getByRole('button', { name: '키오스크 변경' }).click();
  await configure(page, kioskB);
  await page.getByRole('link', { name: '키오스크 화면으로 돌아가기' }).click();
  await expect(page.getByText('테스트 키오스크 B', { exact: true })).toBeVisible();
  await page.goBack();
  await page.getByRole('button', { name: '키오스크 변경' }).click();
  await configure(page, kioskA);
  await page.getByRole('link', { name: '키오스크 화면으로 돌아가기' }).click();
  await page.clock.runFor(8001);
  await expect(page.getByText('설정 이동 경고')).toHaveCount(0);
  expect(queries().slice(firstAfterReload).every((r) => r.query.since === since)).toBe(true);
  expect(posts(model)).toEqual([]);
});
