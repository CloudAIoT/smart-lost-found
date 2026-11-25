import { test, expect, enterKiosk, openPickup, home, completion, posts, kioskA, regId, loginAdmin } from './fixture.js';

for (const sensor of ['no response', 'no reading', 'old closed reading']) {
  test(`pickup can complete with ${sensor}`, async ({ page, model }) => {
    model.abortDevices = sensor === 'no response';
    model.devices = sensor === 'old closed reading' ? [{ device: 'locker', locker_number: 2, connection: null, item: 'empty', door: 'closed', updated_at: '2000-01-01T00:00:00Z' }] : [];
    await enterKiosk(page);
    await openPickup(page);
    await expect(completion(page)).toBeEnabled();
    await completion(page).click();
    await expect(page.getByRole('heading', { name: '물건을 찾아가셨습니다' })).toBeVisible();
    expect(posts(model)).toHaveLength(2);
  });
}

test('warning dedupe and fixed since survive Home and resume; warnings do not hide completion', async ({ page, model }) => {
  await page.clock.install();
  model.alerts = [{ id: 'old', kiosk_id: kioskA, code: 'UNKNOWN', message: '과거 경고', reported_at: '2000-01-01T00:00:00Z' }];
  await enterKiosk(page);
  const queries = () => model.requests.filter((r) => r.path === '/api/alerts');
  await expect.poll(() => queries().length).toBeGreaterThan(0);
  const since = queries()[0].query.since;
  expect(since).toBeTruthy();
  await expect(page.getByText('과거 경고', { exact: true })).toHaveCount(0);
  await openPickup(page);
  const warning = { id: 'current', kiosk_id: kioskA, code: 'ITEM_NOT_TAKEN', severity: 'warning', locker_number: 2, reported_at: '2099-01-01T00:00:00Z' };
  model.alerts.push(warning, warning);
  await page.clock.runFor(8001);
  await expect(page.getByRole('button', { name: '확인', exact: true })).toHaveCount(1);
  await expect(page.getByText('· 2번 보관함', { exact: true })).toBeVisible();
  await expect(completion(page)).toBeEnabled();
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await home(page).click();
  await page.getByRole('button', { name: '진행 중인 업무 계속하기' }).click();
  await page.clock.runFor(16001);
  await expect(page.getByRole('button', { name: '확인', exact: true })).toHaveCount(0);
  model.alerts.push({ ...warning, id: 'late', code: 'UNKNOWN', message: '늦은 새 경고', reported_at: since });
  await page.clock.runFor(8001);
  await expect(page.getByText('늦은 새 경고', { exact: false })).toBeVisible();
  expect(queries().every((r) => r.query.since === since)).toBe(true);
  await completion(page).click();
  await expect(page.getByRole('heading', { name: '물건을 찾아가셨습니다' })).toBeVisible();
});

test('standalone device status supports reload, historical alerts and explicit sensor states', async ({ page, model }) => {
  model.devices = [
    { device: 'locker', locker_number: null, connection: 'online', item: null },
    { device: 'capture', locker_number: null, connection: 'offline', item: null },
    ...['online', 'offline', null].flatMap((connection, i) => ['present', 'empty', null].map((item, j) => ({ device: 'locker', locker_number: i * 3 + j + 1, connection, item, door: 'closed' }))),
  ];
  model.alerts = [{ id: 'old', kiosk_id: kioskA, code: 'UNKNOWN', message: '과거 장치 경고', reported_at: '2000-01-01T00:00:00Z' }];
  await page.goto('/admin/devices');
  await loginAdmin(page);
  await expect(page.getByRole('heading', { name: '장치 상태' })).toBeVisible();
  await page.reload();
  await loginAdmin(page);
  await expect(page.getByText('과거 장치 경고', { exact: true })).toBeVisible();
  expect(model.requests.filter((r) => r.path === '/api/alerts').every((r) => !r.query.since)).toBe(true);
  const rows = page.locator('tbody tr');
  await expect(rows).toHaveCount(model.devices.length);
  for (let i = 0; i < model.devices.length; i++) {
    const row = model.devices[i];
    const cells = rows.nth(i).locator('td');
    await expect(cells.nth(0)).toHaveText(row.locker_number === null ? (row.device === 'capture' ? '촬영 장치' : '보관함 컨트롤러') : `${row.locker_number}번 보관함`);
    await expect(cells.nth(1)).toHaveText(row.connection === 'online' ? '온라인' : row.connection === 'offline' ? '오프라인' : '-');
    await expect(cells.nth(4)).toHaveText(row.item === 'present' ? '감지됨' : row.item === 'empty' ? '비어 있음' : '-');
  }
});

test('successful deposit releases the guard after both shots and completion', async ({ page, model }) => {
  await enterKiosk(page);
  await page.getByRole('button', { name: /분실물 등록 습득/ }).click();
  await page.getByRole('combobox').selectOption('전자기기');
  await page.getByRole('button', { name: /다음: 물건 촬영하기/ }).click();
  await page.getByRole('button', { name: '촬영하기', exact: true }).click();
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await page.getByRole('button', { name: '방향을 바꿨어요, 촬영하기' }).click();
  await page.getByRole('button', { name: '등록 완료 · 보관함 열기' }).click();
  await expect(home(page)).toBeDisabled();
  await page.getByRole('button', { name: '물건을 넣고 문을 닫았습니다' }).click();
  await expect(page.getByRole('heading', { name: '2번 보관함에 보관되었습니다' })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/kiosk\/settings$/);
  expect(posts(model).at(-1).path).toBe(`/api/registrations/${regId}/deposit/complete`);
});
