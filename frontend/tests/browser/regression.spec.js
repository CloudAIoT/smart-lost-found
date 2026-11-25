import { test, expect, enterKiosk, openPickup, home, completion, posts, kioskA, regId,
  loginAdmin, openKioskDetail } from './fixture.js';

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

test('the device tables separate Pi devices from lockers and show explicit sensor states', async ({ page, model }) => {
  model.devices = [
    { device: 'locker', locker_number: null, connection: 'online', item: null },
    // 촬영 상자는 연결과 함께 무게·물건감지를 보낸다. 보관함 컨트롤러는 연결만 보낸다.
    { device: 'capture', locker_number: null, connection: 'offline', weight_g: 0, item: 'empty' },
    { device: 'locker', locker_number: 1, connection: null, door: 'open', weight_g: 1234, item: 'present' },
    { device: 'locker', locker_number: 2, connection: null, door: 'closed', weight_g: null, item: 'empty' },
    { device: 'locker', locker_number: 3, connection: null, door: null, weight_g: null, item: null },
  ];
  model.alerts = [{ id: 'old', kiosk_id: kioskA, code: 'UNKNOWN', message: '과거 장치 경고', reported_at: '2000-01-01T00:00:00Z' }];
  await page.goto('/admin/devices');
  await loginAdmin(page);
  await page.reload();
  await loginAdmin(page);
  await openKioskDetail(page);
  await expect(page.getByRole('heading', { name: '장치 상태' })).toBeVisible();
  // 장치는 받은 값만 카드로, 칸은 표로 보여 준다(빈 칸이 열과 어긋나지 않는다).
  const controller = page.getByRole('region', { name: '보관함 컨트롤러' });
  await expect(controller).toContainText('온라인');
  await expect(controller).not.toContainText('물건감지');
  await expect(controller).not.toContainText('무게');
  const capture = page.getByRole('region', { name: '촬영 장치' });
  await expect(capture).toContainText('오프라인');
  await expect(capture).toContainText('비어 있음');
  await expect(capture).toContainText('0 g');
  const lockers = page.locator('table').locator('tbody tr');
  await expect(lockers).toHaveCount(3);
  await expect(lockers.nth(0).locator('td')).toHaveText(['1번', '열림', '1,234', '감지됨', '-']);
  await expect(lockers.nth(1).locator('td')).toHaveText(['2번', '닫힘', '-', '비어 있음', '-']);
  await expect(lockers.nth(2).locator('td')).toHaveText(['3번', '상태 모름', '-', '상태 모름', '-']);
  // 관리자 화면은 과거 경고까지 본다. 키오스크 화면과 달리 since로 자르지 않는다.
  await expect(page.getByText('과거 장치 경고', { exact: true })).toBeVisible();
  expect(model.requests.filter((r) => r.path === '/api/alerts').every((r) => !r.query.since)).toBe(true);
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
